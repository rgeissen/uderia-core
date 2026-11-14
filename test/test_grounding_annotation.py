"""#37 + #42 — what a withheld answer TELLS the reader, and what the trigger flags.

Both findings came out of the walkthrough's first real grounding BLOCK (session
`7c4b2796`), where the user-facing annotation named five red herrings and never
mentioned the judge's actual finding:

  #37  `unsupported_claims[0]` was a 200-character fragment starting mid-word
       ("s **Connector Registry**, which su…") — a POSSESSIVE apostrophe opened a
       spurious quote that ran to the next contraction. And `needs_judge: false` was
       persisted on a turn a judge demonstrably ruled on, so the only way to tell an
       adjudication had happened was to parse the reason prose.
  #42  Three of the five flagged "claims" were the answer's own markdown headings
       ("File Location", "Environment Variables", "Implementation Details"), and the
       annotation opened with that noise while the judge's reason — a roadmap spec
       presented as shipped configuration — appeared nowhere.

Pure: no server, no network, no LLM.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.grounding import corpus as C            # noqa: E402
from trusted_data_agent.agent.grounding.gate import GroundingGate     # noqa: E402
from trusted_data_agent.agent.grounding.enforce import resolve_enforced_verdict  # noqa: E402
from trusted_data_agent.agent.grounding.types import GroundingVerdict, Decision  # noqa: E402

failures = []
checked = [0]


def check(name, got, want):
    checked[0] += 1
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")


# ── #37a · a possessive apostrophe is not a quotation ───────────────────────
check("#37 a possessive + contraction yields no quote",
      C.extract_quotes("Uderia's Connector Registry is broad. It doesn't list any ports."), [])
check("#37 …and no span begins mid-word",
      [q for q in C.extract_quotes("Uderia's registry supports servers. It doesn't list ports.")
       if q[:1].islower() and not q.split()[0].isalpha()], [])
check("#37 a real double-quoted claim still extracts",
      C.extract_quotes('The docs say "the session index is stored in Postgres" here.'),
      ["the session index is stored in Postgres"])
check("#37 an apostrophe INSIDE a double quote does not break it",
      C.extract_quotes('It states "it doesn\'t support Redis at all yet" clearly.'),
      ["it doesn't support Redis at all yet"])
check("#37 typographic pairs work",
      C.extract_quotes("It said “the backend is horizontally scalable” here."),
      ["the backend is horizontally scalable"])
check("#37 a straight quote cannot be closed by a typographic one",
      C.extract_quotes('He said "this is not a real pairing” ok'), [])
check("#37 a genuine single-quoted phrase still extracts",
      C.extract_quotes("The flag is 'enabled for every profile class' per the docs."),
      ["enabled for every profile class"])
check("#37 no candidate crosses a blank line",
      C.extract_quotes('opens "here and never closes\n\nlater "and this one opens'), [])

# ── #42a · the answer's own structure is not a claim ────────────────────────
REAL_BLOCKED_ANSWER = """According to the **Uderia Platform - Comprehensive Scale Assessment** documentation, you configure the Redis session backend as follows:

*   **File Location**: You need to implement the Redis client in `src/trusted_data_agent/core/redis_client.py`.
*   **Environment Variables**:
    *   Set `TDA_REDIS_ENABLED` to `true` (it defaults to `false`).

## Implementation Details

The Postgres session index is separate.
"""
# Models emit the colon on EITHER side of the closing bold marker; live output used
# `**Label:**`, which a pattern covering only `**Label**:` let straight through.
REAL_BLOCKED_ANSWER += """
*   **Primary Configuration File:** `src/trusted_data_agent/core/redis_client.py`
*   **Required Environment Variables:**

The **Connector Registry** supports Private MCP Servers.
"""
ids = C.extract_identifiers(REAL_BLOCKED_ANSWER)
check("#42 a bolded label with an inside colon is not a claim",
      "Primary Configuration File" in ids, False)
check("#42 …nor its sibling", "Required Environment Variables" in ids, False)
check("#42 bold EMPHASIS in prose survives (it may be a real assertion)",
      "Connector Registry" in ids, True)
for noise in ("File Location", "Environment Variables", "Implementation Details"):
    check(f"#42 the answer's own label {noise!r} is not a claim", noise in ids, False)
# The fabrications themselves MUST survive — this is a noise filter, not a mute button.
check("#42 the invented module path is still flagged",
      "src/trusted_data_agent/core/redis_client.py" in ids, True)
check("#42 the invented env var is still flagged", "TDA_REDIS_ENABLED" in ids, True)
# A capitalised sequence in ordinary PROSE is still a candidate — the structure filter
# must remove the answer's scaffolding without muting its actual assertions.
# (A sequence led by a generic word like "The" is filtered by the pre-existing
# `_GENERIC_LEADS` rule, which is unrelated to this fix.)
check("#42 prose capitals are untouched",
      "Postgres Session Storage" in C.extract_identifiers("Postgres Session Storage is separate."),
      True)

# ── #42b · a withheld answer leads with WHY ─────────────────────────────────
DET = GroundingVerdict(Decision.PASS, "enforce — judge required (corpus)", needs_judge=True,
                       ground="corpus", judge_family="groundedness",
                       unsupported_claims=("Uderia Platform", "TDA_REDIS_ENABLED"))
JUDGE = ("panel 0✓/2✗/0? — The answer incorrectly claims there is a Redis 'session "
         "backend' to configure, whereas the retrieved documents state that session data "
         "runs on Postgres.; The ANSWER does not provide configuration information")

blocked = resolve_enforced_verdict(DET, False, JUDGE).to_event()
ann = blocked["annotation_md"] or ""
check("#42 the block annotation leads with the review's finding",
      ann.startswith("This answer was withheld after review:"), True)
check("#42 …quoting the judge's actual reason", "incorrectly claims there is a Redis" in ann, True)
check("#42 …without the panel tally bookkeeping", "panel 0" in ann, False)
check("#42 …and only the FIRST ruling, not every judge's prose",
      "does not provide configuration information" in ann, False)
check("#42 the trigger list survives as supporting detail",
      "TDA_REDIS_ENABLED" in ann, True)
# `str.index` RAISES when the substring is absent, so this assertion crashed the suite
# instead of reporting when the lead sentence was removed — the same "assert defensively"
# lesson this walkthrough has now learned twice. `find` returns -1 and compares cleanly.
check("#42 …demoted below the finding",
      0 <= ann.find("withheld after review") < ann.find("TDA_REDIS_ENABLED"), True)
check("#42 a withheld answer drops the annotate-voiced hedge",
      "unsupported ≠ false" in ann, False)

# An ANNOTATE keeps the hedge: the answer WAS delivered, so "unsupported ≠ false" is
# exactly the right caution there.
annotated = resolve_enforced_verdict(DET, None, "panel 0✓/0✗/2? — undecided").to_event()
check("#42 an annotated answer keeps the hedge",
      "unsupported ≠ false" in (annotated["annotation_md"] or ""), True)

# No adjudication to quote ⇒ the sentence is simply omitted, never a stub.
check("#42 an unparseable reason yields no lead sentence",
      GroundingGate._judge_explanation("enforce — judge: drifted"), "")

# ── #37b · the adjudication is a recorded fact, not prose to re-parse ───────
check("#37 a cleared escalation records that it was judged", blocked["judged"], True)
check("#37 …and how it ruled", blocked["judge_passed"], False)
faithful = resolve_enforced_verdict(DET, True, JUDGE).to_event()
check("#37 a faithful ruling is recorded", (faithful["judged"], faithful["judge_passed"]),
      (True, True))
check("#37 …and delivers the answer", faithful["decision"], "pass")
# A genuine ABSTENTION and "no panel existed" both arrive with judge_passed=None and must
# stay distinguishable — claiming an adjudication that never happened is the same class
# of dishonesty this finding is about.
abstained = resolve_enforced_verdict(DET, None, "panel 0✓/0✗/2?", judged=True).to_event()
no_panel = resolve_enforced_verdict(DET, None, "judge unavailable", judged=False).to_event()
check("#37 a genuine abstention is judged", abstained["judged"], True)
check("#37 an absent panel is NOT judged", no_panel["judged"], False)
check("#37 both annotate (fail-safe never withholds on uncertainty)",
      (abstained["decision"], no_panel["decision"]), ("annotate", "annotate"))


# ── #42c · the quoted reason must SUPPORT the ruling ────────────────────────
# The panel joins every judge's reasoning, so a 0-pass/2-fail block could open with a
# PASSING judge's words — live-observed: a withheld answer explained itself with "the
# answer accurately distinguishes… and correctly identifies…". `consensus()` now orders
# the reasoning by the verdict it supports, so the first clause always explains the
# decision that was actually reached.
from trusted_data_agent.eval.judge import consensus  # noqa: E402

_mk = lambda v, r: {"verdict": v, "score": None, "evidence": [], "reasoning": r}


def _lead(parsed):
    passed, _, _, expl = consensus(parsed)
    return passed, expl.split("—", 1)[1].split(";")[0].strip()


p1, lead1 = _lead([_mk("fail", "it invents a Redis backend"), _mk("fail", "no such env var")])
check("#42 a blocking panel leads with a failing reason", (p1, lead1),
      (False, "it invents a Redis backend"))
p2, lead2 = _lead([_mk("pass", "reads accurate to me"), _mk("fail", "it invents a Redis backend")])
check("#42 a tie blocks, and still leads with the failing reason", (p2, lead2),
      (False, "it invents a Redis backend"))
p3, lead3 = _lead([_mk("fail", "minor nit"), _mk("pass", "faithful to the sources"),
                   _mk("pass", "supported")])
check("#42 a passing panel leads with a passing reason", (p3, lead3),
      (True, "faithful to the sources"))
# Abstentions never lead — they explain nothing about a decision.
p4, lead4 = _lead([_mk("unknown", "cannot tell"), _mk("fail", "fabricated the path")])
check("#42 an abstention never leads", lead4, "fabricated the path")

# ── report ──────────────────────────────────────────────────────────────────
print()
if failures:
    print(f"FAILED — {len(failures)} assertion(s)\n")
    print(f"{checked[0] - len(failures)} passed, {len(failures)} failed")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print(f"{checked[0]} passed, 0 failed — ALL PASS — #37/#42: honest spans, a recorded adjudication, and an annotation "
      "that leads with the finding.")
