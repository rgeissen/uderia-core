#!/usr/bin/env python3
"""Reproducible comparison: the `answer_support` assessor vs the sentence-overlap
algorithm it replaced, over the committed corpus-grounding benchmark.

    python3 scripts/verify_answer_support.py

Committed because the change it justifies is a DISCONTINUITY — `grounded_overlap` measured
sentences before and claims-or-sentences after, on the same 0..1 scale — and a
discontinuity asserted without a reproducible measurement is a claim, not evidence.
"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.agent import assurance as A
from trusted_data_agent.agent.assurance import registry
from trusted_data_agent.agent.assurance.capabilities import Lane
from trusted_data_agent.agent.assurance.contract import AssessmentContext

A.bootstrap()
DS = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "eval",
                  "benchmarks", "corpus_grounding", "dataset", "corpus_v1.json")
cases = json.load(open(DS))
if isinstance(cases, dict):
    cases = cases.get("cases", [])

_W = re.compile(r"[A-Za-z0-9]{4,}")


def old_overlap(answer, chunks, question):
    """The pre-change algorithm, verbatim."""
    ct = set(_W.findall(" ".join(list(chunks) + [question or ""]).lower()))
    sents = [s for s in re.split(r"[.!?\n]+", answer) if s.strip()]
    if not sents:
        return 1.0
    sup = sum(1 for s in sents
              if not set(_W.findall(s.lower()))
              or len(set(_W.findall(s.lower())) & ct) / len(set(_W.findall(s.lower()))) >= 0.5)
    return round(sup / len(sents), 4)


a = registry.resolve("answer_support", Lane.POST_HOC)
print(f"{'case':22} {'class':14} {'OLD':>6} {'NEW':>6} {'branch':>8}  verdict")
print("-" * 76)
old_wrong = new_wrong = 0
for c in cases:
    truth_clean = c["class"] in ("supported", "second_chance")
    old = old_overlap(c["answer"], c.get("chunks") or [], c.get("question") or "")
    stamp = {}
    if c["class"] == "second_chance":
        bare = a.run(AssessmentContext(answer=c["answer"], chunks=tuple(c.get("chunks") or []),
                                       question=c.get("question") or ""))
        stamp = {"grounding": {"second_chance_cleared": list(bare.evidence)}}
    f = a.run(AssessmentContext(answer=c["answer"], chunks=tuple(c.get("chunks") or []),
                                question=c.get("question") or "", stamp=stamp))
    new = f.metrics.get("support_ratio")
    branch = "lexical" if f.metrics.get("branch_lexical") else "claims"
    ow, nw = (old >= 0.5) != truth_clean, (not f.triggered) != truth_clean
    old_wrong += ow
    new_wrong += nw
    v = ("OLD wrong" if ow else "") + (" NEW wrong" if nw else "") or "both correct"
    print(f"{c['case_id'][:22]:22} {c['class']:14} {old:>6} {new:>6} {branch:>8}  {v}")

print(f"\n{len(cases)} cases · OLD wrong on {old_wrong} · NEW wrong on {new_wrong}")
sys.exit(1 if new_wrong > old_wrong else 0)
