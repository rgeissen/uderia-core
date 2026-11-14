"""CI-pure tests — the corpus-grounding benchmark (RAG Trust Hardening, knowledge twin of
the anti-hallucination benchmark).

Pins the published headline numbers so they cannot silently drift, validates the committed
dataset, and proves the benchmark scores each case with the SAME production primitive the
live gate uses. Because the corpus trigger is deterministic, the whole benchmark — and
therefore its published recall / false-positive numbers — is reproducible here at 0 tokens.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.benchmarks.corpus_grounding import dataset, report, harness  # noqa: E402
from trusted_data_agent.eval.benchmarks.corpus_grounding.report import (  # noqa: E402
    CorpusCase, aggregate, expected_for_class, SUPPORTED, UNSUPPORTED, SECOND_CHANCE,
)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def t_dataset_valid():
    data = dataset.load_dataset("corpus_v1")
    check("dataset loads + validates", dataset.validate_dataset(data))
    counts = dataset.class_counts(data)
    check("all three classes populated",
          counts[SUPPORTED] > 0 and counts[UNSUPPORTED] > 0 and counts[SECOND_CHANCE] > 0)
    check("second_chance cases carry a second_chance corpus",
          all(c.get("second_chance") for c in data["cases"] if c["class"] == "second_chance"))
    # structural guards actually fire
    try:
        dataset.validate_dataset({"cases": [{"case_id": "x", "class": "bogus", "answer": "a"}]})
        check("invalid class rejected", False)
    except AssertionError:
        check("invalid class rejected", True)


def t_expected_truth():
    check("only unsupported is expected to trigger",
          expected_for_class(UNSUPPORTED) is True
          and expected_for_class(SUPPORTED) is False
          and expected_for_class(SECOND_CHANCE) is False)


def t_aggregation_math():
    # a hand-built set with known TP/FP/FN
    cases = [
        CorpusCase("u1", UNSUPPORTED, triggered=True, expected_trigger=True),   # TP
        CorpusCase("u2", UNSUPPORTED, triggered=False, expected_trigger=True),  # FN
        CorpusCase("s1", SUPPORTED, triggered=False, expected_trigger=False),   # TN
        CorpusCase("s2", SUPPORTED, triggered=True, expected_trigger=False),    # FP
        CorpusCase("c1", SECOND_CHANCE, triggered=False, expected_trigger=False),  # recovered
        CorpusCase("c2", SECOND_CHANCE, triggered=True, expected_trigger=False),   # FP (not recovered)
    ]
    r = aggregate(cases)
    check("recall = 1/2", r.recall == 0.5)
    check("FP rate = 2/(2 supp-ish denom: 2 supported + 2 second_chance)... = 2/4",
          r.false_positive_rate == 0.5)
    check("second-chance recovery = 1/2", r.second_chance_recovery == 0.5)
    check("net precision = TP/(TP+FP) = 1/3", r.net_precision == round(1/3, 4))
    check("accuracy = 3/6 (u1, s1, c1 correct)", r.accuracy == 0.5)
    check("false negatives listed", r.false_negatives == ["u2"])
    check("false positives listed", set(r.false_positives) == {"s2", "c2"})


def t_production_primitive():
    """The benchmark must score via the real corpus.assess (single source of truth)."""
    supported = harness.score_case({
        "case_id": "x", "class": "supported",
        "question": "how many?", "answer": "There are 16 types.",
        "chunks": ["The platform supports 16 types."]})
    check("supported answer does not trigger", not supported.triggered and supported.correct)
    unsupported = harness.score_case({
        "case_id": "y", "class": "unsupported",
        "question": "how many?", "answer": "There are 42 types.",
        "chunks": ["The platform supports 16 types."]})
    check("unsupported answer triggers", unsupported.triggered and unsupported.correct)
    sc = harness.score_case({
        "case_id": "z", "class": "second_chance",
        "question": "how many passes?", "answer": "It runs 12 passes.",
        "chunks": ["The optimizer plans."],
        "second_chance": ["The optimizer runs 12 rewrite passes."]})
    check("second_chance case cleared by its re-search corpus",
          not sc.triggered and "12" in sc.second_chance_cleared)


def t_published_numbers():
    """Pin the corpus_v1 headline numbers — the CI gate that keeps the proof pack honest.
    If a primitive change moves these, the proof pack must be re-rendered deliberately."""
    r, scored = harness.run("corpus_v1")
    check("18 cases scored", r.total == 18)
    check("recall == 0.857 (one honest miss: the ≥3-identifier floor, uns-id-01)",
          r.recall == round(6/7, 4) and r.false_negatives == ["uns-id-01"])
    check("false-positive rate == 0.0 (no supported answer wrongly flagged)",
          r.false_positive_rate == 0.0 and r.false_positives == [])
    check("second-chance recovery == 1.0 (every re-searchable claim cleared)",
          r.second_chance_recovery == 1.0)
    check("net precision == 1.0", r.net_precision == 1.0)
    check("markdown renders the numbers", "Recall" in report.render_markdown(r)
          and "85.7%" in report.render_markdown(r))


def main():
    for t in (t_dataset_valid, t_expected_truth, t_aggregation_math,
              t_production_primitive, t_published_numbers):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
