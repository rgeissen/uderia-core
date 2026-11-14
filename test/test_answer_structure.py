"""An answer's own STRUCTURE is not a claim — numbering, table headers, row labels.

Prod session 49dac4d2 (Oct 2026, @CTF_SME, Focus): a figure-free comparison answer was
reported by the grounding gate as "7 claim(s) unsupported" and failed Numeric Fidelity.
Six of the seven were the answer's own scaffolding:

  · "3"                        — the ordinal of "### 3. Philosophical Differences" (1 and 2
                                 passed only because some chunk happened to hold those digits)
  · "Teradata Strategy", …     — the header row of the answer's summary table
  · "Model Approach", …        — bolded row labels ("| **Model Approach** | …")

and the scorer read a DIFFERENT support text from the gate: chunk content without the
citation line the model was shown, so citing "…Oct 2025 v0 2" / "…_20251028" counted as
inventing numbers. The seventh finding — a misquote ("single brain in the building"
where the source says "only brain in the building") — is real and must survive.

Pure: no server, no network, no LLM.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.grounding import corpus as C                        # noqa: E402
from trusted_data_agent.agent.grounding import gate as G                          # noqa: E402
from trusted_data_agent.agent.grounding.primitives import (                       # noqa: E402
    answer_nums, norm_nums, strip_ordinals)
from trusted_data_agent.agent.grounding.types import GroundingConfig, GroundingInputs  # noqa: E402
from trusted_data_agent.agent.grounding.gate import GroundingGate                 # noqa: E402
from trusted_data_agent.agent.grounding import adapter                            # noqa: E402
from trusted_data_agent.eval.types import RunRecord, EvalCase                     # noqa: E402
from trusted_data_agent.eval.scorers.reliability import NumericFidelityScorer     # noqa: E402

failures = []
checked = [0]


def check(name, got, want):
    checked[0] += 1
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")


# ── 1 · ordinals are numbering, figures are figures ─────────────────────────
for text, gone in [
    ("### 3. Philosophical Differences", "3"),
    ("## 2) Governance", "2"),
    ("1. Integration first", "1"),
    ("   12. Twelfth point", "12"),
    ("**2. Governance and Unified Control**", "2"),
    ("#### **4.** Summary", "4"),
]:
    check(f"ordinal stripped: {text!r}", gone in answer_nums(text), False)

for text, kept in [
    ("## 2025 Results", "2025"),             # a year leading a heading is a figure
    ("## 12 Months of Growth", "12"),        # a measure, no ordinal punctuation
    ("3.5 million users signed up", "3.5"),  # a decimal is never an ordinal
    ("2024. That was the year.", "2024"),    # four digits is not a list number
    ("Revenue grew 3. Then it fell.", "3"),  # mid-line: a figure ending a sentence
    ("- 3 regions use it", "3"),             # a bullet with a count, not an ordinal
    ("| 1. | 400 |", "400"),                 # table cells keep their figures
]:
    check(f"figure kept: {text!r}", kept in answer_nums(text), True)

check("support side is untouched: norm_nums still reads ordinals",
      "3" in norm_nums("### 3. Heading"), True)
check("strip_ordinals keeps the heading's words",
      strip_ordinals("### 3. Philosophical Differences").split(),
      ["###", "Philosophical", "Differences"])

# ── 2 · table scaffolding is not a claim, table cells are ───────────────────
TABLE = """Prose names Acme Widget Corp here.

| Feature | Teradata Strategy | Oracle Strategy |
| :--- | :--- | :--- |
| **Model Approach** | Integrates with Existing Models | Hosts Large Catalogs |
| Vantage Cloud Lake | Unified Product Platform | Separate Services |
"""
ids = C.extract_identifiers(TABLE)
for noise in ("Teradata Strategy", "Oracle Strategy", "Model Approach"):
    check(f"table scaffolding {noise!r} is not a claim", noise in ids, False)
for claim in ("Integrates", "Existing Models", "Hosts Large Catalogs",
              "Unified Product Platform", "Separate Services", "Vantage Cloud Lake",
              "Acme Widget Corp"):
    if claim == "Integrates":
        continue  # single word — never a T3 candidate, listed only as a reminder
    check(f"table cell / prose {claim!r} is still a claim", claim in ids, True)
# A pipe line WITHOUT a separator row below it is not a table header.
check("a pipe line with no separator keeps its capitals",
      "Teradata Strategy" in C.extract_identifiers("| Teradata Strategy | is a phrase |"),
      True)
# A bold phrase inside a cell (not leading the row) is content.
check("bold inside a later cell survives",
      "Fine Tuning Service" in C.extract_identifiers(
          "| A | B |\n|---|---|\n| x | **Fine Tuning Service** |"), True)

# ── 3 · the prod turn, reconstructed ────────────────────────────────────────
ANSWER = """Teradata differentiates through **integration versus hosting**.

### 1. Integration vs. Hosting Strategy
*   **Teradata (Integration):** integrates with existing LLMs.

### 2. Governance and Unified Control
*   **Teradata:** By avoiding a "single brain in the building" approach, Teradata helps.

### 3. Philosophical Differences
*   **Oracle's Approach:** As noted in *Birds of a Feather - Oracle AI World Oct 2025 v0 2*, Oracle relies on services.

| Feature | Teradata Strategy | Oracle Strategy |
| :--- | :--- | :--- |
| **Model Approach** | Integrates with existing third-party LLMs | Hosts a library of LLMs |
| **Primary Advantage** | Unified governance | Fine-tuning models in-cloud |
| **Operational Model** | Unified product/platform | Assembly of separate services |

*Source: Birds of a Feather - Oracle AI World Oct 2025 v0 2; Competitive Office Hours_20251028*
"""
TURN = {
    "user_query": "How does Teradata differentiate from Oracle on Generative AI?",
    "final_summary_text": ANSWER,
    "knowledge_retrieval_event": {
        "collections": ["CTF"],
        "chunks": [
            {"source": "Birds of a Feather - Oracle AI World Oct 2025 v0 2",
             "content": "Teradata integrates with existing LLMs. Oracle hosts LLMs and "
                        "offers services. Oracle wants to be the only brain in the "
                        "building. Teradata helps enterprises use every brain. 1 2"},
            {"source": "Birds of a Feather_ Competitive Office Hours_20251028",
             "content": "Unified governance. Fine-tuning models in-cloud. "
                        "Assembly of separate services. Unified product/platform."},
        ],
    },
}
inp = adapter.build_inputs(TURN, ANSWER)
f = C.assess(ANSWER, inp.retrieved_chunks, inp.question, vouched_numbers=inp.vouched_numbers)
check("prod turn: the misquote is still found",
      f.unsupported_quotes, ("single brain in the building",))
check("prod turn: no table scaffolding reported", f.unsupported_identifiers, ())
check("prod turn: no section number reported", f.unsupported_numbers, ())
check("prod turn: numeric fidelity is whole", f.numeric_fidelity, 1.0)
check("prod turn: the trigger still fires (on the misquote)", f.triggered, True)

v = NumericFidelityScorer().score(
    EvalCase(id="x", query=TURN["user_query"], profile_class="rag_focused"), RunRecord(TURN))
check("scorer: the cited document's numbers are grounded (one support definition)",
      v.score, 1.0)
check("scorer: the section number is not counted", v.explanation.startswith("4/4"), True)

# The scorer and the gate read ONE chunk support text.
rr = RunRecord(TURN)
for t in rr.retrieved_chunk_texts():
    check("grounding_text carries each chunk's citation line", t in rr.grounding_text(), True)

# ── 4 · the scope lane reads the same answer-side definition ────────────────
v = GroundingGate(GroundingConfig(mode="observe")).evaluate(GroundingInputs(
    answer_text="### 3. Totals\nThe total was 120.", scope_databases=frozenset({"sales"}),
    tool_calls=(), grounding_text="total 120"))
check("scope lane: a section number does not dent numeric fidelity", v.numeric_fidelity, 1.0)
check("scope lane uses answer_nums (no hand-rolled copy)",
      "norm_nums(inputs.answer_text)" in open(G.__file__).read(), False)

# ── report ──────────────────────────────────────────────────────────────────
print()
# ── the budget finish line (S5) is the platform's, not the answer's ──────────────────────
# The lane appends `_finished within budget — 1 of 8 steps used · ~43k of ~60k tokens used ·
# 27 of 240 s used_` under every answer of a budgeted agent. Its figures are the platform's
# accounting; reading them as claims graded an honest "could not retrieve" as numeric
# fabrication (Optimize campaign, 2026-10-27: numeric_fidelity danger on a decline).
for footer in ("The count could not be retrieved.\n\n_finished within budget — 1 of 12 steps used · ~71k of ~150k tokens used · 10 of 300 s used_",
               "We have 100 customers.\n\n_budget exhausted (tokens) — stopped gracefully; 1 of 8 steps used · ~61k of ~60k tokens used · 5 of 240 s used_"):
    nums = answer_nums(footer)
    for noise in ("12", "71", "150", "10", "300", "8", "61", "60", "5", "240"):
        check(f"budget line figure {noise!r} is not a claim", noise in nums, False)
check("the answer's own figure beside a budget line is still a claim",
      "100" in answer_nums("We have 100 customers.\n\n_budget exhausted (tokens) — stopped gracefully; 1 of 8 steps used_"), True)

if failures:
    print(f"FAILED — {len(failures)} assertion(s)\n")
    print(f"{checked[0] - len(failures)} passed, {len(failures)} failed")
    for x in failures:
        print(f"  ✗ {x}")
    sys.exit(1)
print(f"{checked[0]} passed, 0 failed — ALL PASS — an answer's numbering, table headers and row labels are structure, "
      "not claims; one support text for the gate and the scorer.")
