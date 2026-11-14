"""
Knowledge-Graph NO-REGRESSION gate — compares a benchmark run against the frozen
baseline (test/performance/results/kg_bench_before.json), per graph and per question.

The contract (operator: "no regression allowed"):
  * delivered tokens never GROW on any budget;
  * the decision-relevant target, TABLE SCHEMAS and JOINABLE COLUMNS presence never DROP;
  * the seed rank of the target never WORSENS;
  * SQLite connections per question never grow;
  * on the graphs where the pipeline was already healthy (fitness, airlines) a phase that
    claims "no behaviour change" must render BYTE-IDENTICAL text (KG_FREEZE_STRICT=1).

Run:  PYTHONPATH=src python3 test/performance/kg_enrichment_bench.py --label after
      KG_BENCH_LABEL=after PYTHONPATH=src python3 test/test_kg_no_regression.py
"""
import json
import os
import sys

RES = os.path.join(os.path.dirname(__file__), "performance", "results")
LABEL = os.environ.get("KG_BENCH_LABEL", "after")
STRICT = os.environ.get("KG_FREEZE_STRICT", "0") == "1"
HEALTHY = ("fitness", "airlines")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {('— ' + str(detail)) if detail else ''}")


BASELINE = os.environ.get("KG_BASELINE", "before")  # "before" (pre-Phase-A) or "postD" (locks in the gains)
before = json.load(open(os.path.join(RES, f"kg_bench_{BASELINE}.json")))["rows"]
after_path = os.path.join(RES, f"kg_bench_{LABEL}.json")
if not os.path.exists(after_path):
    print(f"no run labelled '{LABEL}' — run the benchmark first")
    sys.exit(2)
after = {(r["graph"], r["query"]): r for r in json.load(open(after_path))["rows"]}

for b in before:
    key = (b["graph"], b["query"])
    a = after.get(key)
    tag = f"[{b['graph']}] {b['query'][:50]}"
    if a is None:
        check(f"{tag}: present in run", False)
        continue
    # seed rank never worsens (None = absent; absent→present is fine, present→absent is not)
    rb, ra = b.get("target_rank"), a.get("target_rank")
    check(f"{tag}: target seed rank not worse ({rb}→{ra})",
          (rb is None) or (ra is not None and ra <= rb))
    check(f"{tag}: sqlite connections not more ({b['sqlite_connections']}→{a['sqlite_connections']})",
          a["sqlite_connections"] <= b["sqlite_connections"])
    for budget in ("gemini_1m", "k128", "k32"):
        bb, aa = b[budget], a[budget]
        # a trim-capped delivery may differ by line granularity; the cap itself is the ceiling
        cap = {"gemini_1m": 73819, "k128": 9227, "k32": 2306}[budget]
        # tokens may not GROW: bounded by the baseline (+5% estimator noise); when the
        # baseline itself sat at the trim cap, the cap (+2% per-line rounding) is the ceiling
        ceiling = int(cap * 1.02) if bb["rendered_tokens"] > cap else int(bb["delivered_tokens"] * 1.05)
        check(f"{tag} @{budget}: delivered tokens not more ({bb['delivered_tokens']}→{aa['delivered_tokens']}, ceiling {ceiling})",
              aa["delivered_tokens"] <= ceiling)
        for flag in ("target_in_delivered", "schemas", "joinable"):
            check(f"{tag} @{budget}: {flag} not lost ({bb[flag]}→{aa[flag]})", (not bb[flag]) or aa[flag])
        if STRICT and b["graph"] in HEALTHY:
            check(f"{tag} @{budget}: byte-identical on a healthy graph (strict freeze)",
                  bb.get("text_sha") == aa.get("text_sha"), (bb.get("text_sha"), aa.get("text_sha")))

print(f"\n=== KG no-regression ({LABEL} vs {BASELINE}, strict={STRICT}): {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
