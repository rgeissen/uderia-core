#!/usr/bin/env python3
"""Re-read a saved Optimize campaign run — the persisted turns are the evidence.

    python3 test/optimize_campaign/analyse.py                       # newest run: frontier, gap, misses
    python3 test/optimize_campaign/analyse.py <run-dir> --recheck   # re-score every saved turn with the
                                                                    # CURRENT oracle check, invariants and
                                                                    # expectations (no tokens), rewrite raw.json
    python3 test/optimize_campaign/analyse.py <run-dir> --case R4   # one case on every lever, in full
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oracle  # noqa: E402
from registry import fired, llm_calls, tool_calls  # noqa: E402
from run_campaign import frontier, gap  # noqa: E402
from suite import CASES, WASTE, answer_text, check_expectations, check_invariants, self_corrections  # noqa: E402


def recheck(rs):
    by_id = {c["id"]: c for c in CASES}
    n = 0
    for r in rs:
        c = by_id.get(r.get("id"))
        if not c:
            continue
        turns_spec = c.get("turns") or [c]
        for i, t in enumerate(r.get("turns") or []):
            raw = t.get("raw")
            if not raw:
                continue
            spec = dict(c, **{k: v for k, v in (turns_spec[i] if i < len(turns_spec) else {}).items() if v is not None})
            expected = t.get("expected")
            if spec.get("extract") == "set" and isinstance(expected, list):
                expected = set(expected)
            ans = answer_text(raw)
            if spec.get("oracle_sql") and expected is not None:
                try:
                    correct, detail = oracle.check(ans, expected, spec.get("extract", "number"),
                                                   spec.get("tolerance", 0.0), spec.get("prompt", ""))
                except (TypeError, ValueError):
                    # the case changed shape since this run (a set became a number): unscorable
                    correct, detail = None, "case changed since the run — not rescorable"
            else:
                correct, detail = None, "no oracle"
            t["correct"], t["correct_detail"] = correct, detail
            t["fired"] = sorted(fired(raw))
            t["llm_calls"] = len(llm_calls(raw))
            t["tool_calls"] = len(tool_calls(raw))
            t["self_corrections"] = self_corrections(raw)
            w = spec.get("waste")
            if w in WASTE:
                try:
                    t["waste"] = WASTE[w](raw)
                except Exception as e:  # noqa: BLE001
                    t["waste"] = f"error:{e}"
            t["invariants"] = check_invariants(raw, spec, correct, None)
            t["expectations"] = check_expectations(spec, raw, correct, raw.get("rag_case_count"))
            n += 1
        done = [t for t in r.get("turns") or [] if "raw" in t]
        if done:
            last = done[-1]
            for k in ("correct", "self_corrections", "waste", "rag_case_count"):
                r[k] = last.get(k)
            r["fired"] = sorted({k for t in done for k in t.get("fired", [])})
            r["llm_calls"] = sum(t.get("llm_calls", 0) for t in done)
            r["invariants"] = [x for t in done for x in t["invariants"]]
            r["expectations"] = [x for t in done for x in t["expectations"]]
    return n


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    d = args[0] if args else sorted(glob.glob("tda_eval/optimize_campaign/*/"))[-1]
    rs = json.load(open(os.path.join(d, "raw.json")))
    if "--recheck" in argv:
        n = recheck(rs)
        json.dump(rs, open(os.path.join(d, "raw.json"), "w"), indent=1, default=str)
        print(f"rechecked {n} turns → raw.json rewritten\n")
    case = argv[argv.index("--case") + 1].upper() if "--case" in argv else None
    if case:
        for r in rs:
            if r["id"].upper() != case:
                continue
            for t in r.get("turns") or []:
                if "raw" not in t:
                    print(f"## {r['lever']} {r['id']} t{t.get('turn')} ERROR {t.get('error')}")
                    continue
                raw = t["raw"]
                print(f"\n## {r['lever']} {r['id']} t{t['turn']} correct={t.get('correct')} · {t.get('correct_detail')}")
                print(f"   answer: {t.get('answer', '')[:300].replace(chr(10), ' ')}")
                print(f"   plan:   {json.dumps(raw.get('original_plan'))[:300]}")
                for n_, a, ok in tool_calls(raw):
                    print(f"   tool:   {n_} {json.dumps(a)[:120]} {'ok' if ok else 'ERR'}")
                print(f"   fired:  {t.get('fired')} · llm {t.get('llm_calls')} · sc {t.get('self_corrections')} · rag {raw.get('rag_case_count')}")
                for x in t["invariants"] + t["expectations"]:
                    if not x["ok"]:
                        print(f"   NOT SATISFIED {x['id']} — {x['detail'][:120]}")
        return 0
    print(f"run: {d}   cases: {len(rs)}\n")
    fr = frontier(rs)
    print(f"{'lever':11s} {'correct':>9s} {'decl':>5s} {'right%':>7s} {'cost':>8s} {'$/correct':>10s} {'p50 ms':>7s} {'llm/case':>9s} {'sc':>3s} {'waste':>5s}")
    for lv, f in fr.items():
        print(f"{lv:11s} {str(f['correct']) + '/' + str(f['scored']):>9s} {f['declined']:>5d} {str(f['answered_rate']):>7s} {f['cost_total']:>8.4f} "
              f"{str(f['cost_per_correct']):>10s} {str(f['p50_latency_ms']):>7s} {str(f['llm_calls_per_case']):>9s} "
              f"{f['self_corrections']:>3d} {f['waste']:>5d}")
    g = gap(rs)
    if g:
        print("\nTHE GAP — working model vs weak model, per case")
        for cid, a, b, kind in g:
            print(f"  {cid:4s} OCFL={str(a):5s} OCWEAK={str(b):5s} {kind}")
    print("\nNOT SATISFIED")
    n = 0
    for r in rs:
        for x in (r.get("invariants") or []) + (r.get("expectations") or []):
            if not x["ok"]:
                n += 1
                print(f"  {r['lever']:11s} {r['id']:4s} {x['id'][:34]:34s} {x['detail'][:95]}")
    print(f"  total: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
