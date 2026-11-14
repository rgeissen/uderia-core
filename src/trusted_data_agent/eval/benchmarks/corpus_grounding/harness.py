"""Corpus-grounding benchmark harness — PURE runner (no server, no LLM, zero tokens).

Unlike the anti-hallucination harness (Tier 1.4, which drives live turns + a judge), the
corpus trigger is a deterministic function of (answer, chunks, question), so this benchmark
runs entirely in-process against the committed dataset. That makes the published recall /
false-positive numbers reproducible offline by anyone with the repo — the strongest form of
proof (charter #6, #19).

Each case is scored by the SAME production primitives the live gate uses
(``agent/grounding/corpus.assess`` + ``apply_second_chance``) — the benchmark and the
running platform can never disagree about what "unsupported" means.

Run:
    python -m trusted_data_agent.eval.benchmarks.corpus_grounding.harness [--dataset corpus_v1] [--json]
"""
from __future__ import annotations

import argparse
import json

from trusted_data_agent.agent.grounding import corpus
from trusted_data_agent.eval.benchmarks.corpus_grounding.dataset import load_dataset
from trusted_data_agent.eval.benchmarks.corpus_grounding.report import (
    CorpusCase, aggregate, expected_for_class, render_markdown,
)


def score_case(case: dict) -> CorpusCase:
    """Score ONE dataset case with the production trigger. For a ``second_chance`` case the
    committed ``second_chance`` chunks are the spans a targeted re-search would return — we
    clear exactly the spans they support, exactly as ``runtime._second_chance_clear`` does."""
    answer = case["answer"]
    chunks = case.get("chunks", [])
    question = case.get("question", "")
    findings = corpus.assess(answer, chunks, question)

    if case["class"] == "second_chance" and findings.unsupported_spans:
        sc_text = corpus._norm_text("\n".join(case.get("second_chance", [])))
        sc_nums = set()
        try:
            from trusted_data_agent.agent.grounding.primitives import norm_nums
            sc_nums = norm_nums("\n".join(case.get("second_chance", [])))
        except Exception:
            pass
        cleared = [s for s in corpus.spans_for_second_chance(findings)
                   if corpus.span_supported(s, sc_text, sc_nums)]
        findings = corpus.apply_second_chance(findings, cleared)

    return CorpusCase(
        case_id=case["case_id"], case_class=case["class"],
        triggered=findings.triggered,
        expected_trigger=expected_for_class(case["class"]),
        unsupported_spans=findings.unsupported_spans,
        second_chance_cleared=findings.second_chance_cleared)


def run(dataset_name: str = "corpus_v1"):
    data = load_dataset(dataset_name)
    scored = [score_case(c) for c in data["cases"]]
    return aggregate(scored), scored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="corpus_v1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report, _ = run(args.dataset)
    if args.json:
        print(json.dumps(report.__dict__, indent=2, default=str))
    else:
        print(render_markdown(report, dataset_name=args.dataset))
    # This command is the REPORT tool. The regression gate is the CI suite
    # (test/test_corpus_grounding_benchmark.py), which pins the published numbers —
    # including the honest false negative — so drift fails CI without demanding a
    # dishonest 100%.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
