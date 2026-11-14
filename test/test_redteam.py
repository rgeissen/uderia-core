"""CI-pure tests for eval/redteam.py — the deterministic resilience self-test.

Runs the real detection primitives; asserts the honest floor (injection + PII
strong, toxicity partial by design) and that benign traffic is not flagged.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.redteam import run_self_test  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def t_self_test():
    r = run_self_test()
    check("resilience score present", isinstance(r["resilience_score"], float))
    cats = r["categories"]
    check("injection recall strong (>=0.9)", cats["prompt_injection"]["recall"] >= 0.9)
    check("pii recall strong (>=0.9)", cats["pii"]["recall"] >= 0.9)
    # Toxicity is a documented narrow high-precision heuristic — partial recall is
    # honest, not a regression; assert it runs and publishes any misses.
    check("toxicity category runs", cats["toxicity"]["total"] >= 1
          and cats["toxicity"]["recall"] is not None)
    check("misses are published, not hidden",
          all("misses" in c for c in cats.values()))
    check("corpus size reported", r["corpus_size"] == sum(c["total"] for c in cats.values()))


def t_no_false_positives():
    r = run_self_test()
    fp = r["false_positives"]
    check("benign corpus scored", fp["benign_total"] >= 5)
    check("no benign flagged (0 false positives)",
          fp["false_positive_rate"] == 0.0 and fp["flagged_benign"] == [])


def t_deterministic():
    a, b = run_self_test(), run_self_test()
    check("self-test is deterministic", a == b)


def t_scope_honesty():
    r = run_self_test()
    check("scope names the deterministic layer", "deterministic" in r["scope"])
    check("note disclaims live-model coverage", "NOT drive a live model" in r["note"])


def main():
    for t in (t_self_test, t_no_false_positives, t_deterministic, t_scope_honesty):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
