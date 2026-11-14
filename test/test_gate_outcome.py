"""I1 — the gate's outcome reaches the eval span (and the per-agent aggregate).

Five session-2 findings share one root cause: every consumer surface reads the eval
SPAN, and the span carried only what the scorers MEASURED — never what the gates DID.
The consequences were not cosmetic:

  #50  an inbound refusal is deliberately NOT scored (never feed an injected prompt
       to a judge), so the turn is absent from the population and GUARDED reads
       "11/11 clean" on a day the guard refused an attack.
  #53  an outbound redaction IS scored, but the safety scorers see the POST-redaction
       text — clean by construction. The guard erases its own evidence.
  #36  a corpus trigger escalated to a judge that CLEARED it — the strongest evidence
       a gate works — and Evidence read "none in this window".

These run with no server and no network. Every fixture is a real stamp shape, verified
against persisted turns from the walkthrough (see the shape assertions below).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.gate_outcome import gate_attributes  # noqa: E402
from trusted_data_agent.eval.analysis import online_quality_by_agent  # noqa: E402

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")


# ── the real stamp shapes, as persisted (guards against fixture drift) ───────
INBOUND_BLOCK = {
    "action": "block", "direction": "inbound", "mode": "review",
    "reason": "prompt-injection detected (2 pattern(s))",
    "categories": ["prompt_injection"], "findings": [], "annotation_md": None,
    "review_id": "714d7a32-0000-0000-0000-000000000000",
}
OUTBOUND_REDACT = {
    "action": "redact", "direction": "outbound", "mode": "review",
    "reason": "2 PII span(s) detected", "categories": ["pii"], "findings": [],
    "annotation_md": "PII redacted", "review_id": "e848356f-0000-0000-0000-000000000000",
}
GROUNDING_BLOCK = {
    "decision": "block", "mode": "review", "ground": "corpus",
    "judge_family": "groundedness",
    "reason": "enforce — judge: unsupported by the knowledge base: panel 0✓/2✗/0? — …",
    "review_id": "4a007235-0000-0000-0000-000000000000",
}
GROUNDING_CLEARED = {   # the #36 case: trigger fired, judge cleared, answer delivered
    "decision": "pass", "mode": "review", "ground": "corpus",
    "judge_family": "groundedness", "reason": "enforce — judge: faithful",
}
GROUNDING_QUIET = {     # trigger never fired — no judge, nothing to report
    "decision": "pass", "mode": "review", "ground": "corpus", "reason": "corpus-supported",
}
HONESTY_FLAG = {"decision": "flag", "mode": "review", "band": "warn",
                "reason": "asserted non-existence after every query failed"}

# ── 1 · absence is byte-identical ───────────────────────────────────────────
check("no stamps ⇒ no attributes", gate_attributes({}), {})
check("None turn ⇒ no attributes", gate_attributes(None), {})
check("non-dict turn ⇒ no attributes", gate_attributes("nope"), {})
check("a turn with unrelated keys ⇒ no attributes",
      gate_attributes({"turn": 3, "status": "complete"}), {})

# ── 2 · #50 — an inbound refusal is visible ─────────────────────────────────
a = gate_attributes({"guardrail_inbound": INBOUND_BLOCK})
check("#50 inbound block is recorded", a.get("eval.gate.inbound"), "block")
check("#50 …and counts as the gate ACTING", a.get("eval.gate.acted"), True)
check("#50 …and as parked for review", a.get("eval.gate.parked"), True)

# ── 3 · #53 — an outbound redaction is visible ──────────────────────────────
a = gate_attributes({"guardrail": OUTBOUND_REDACT})
check("#53 outbound redact is recorded", a.get("eval.gate.outbound"), "redact")
check("#53 …and counts as acting", a.get("eval.gate.acted"), True)
check("#53 …and as parked", a.get("eval.gate.parked"), True)

# ── 4 · #36 — a CLEARED escalation is visible (the hardest case) ────────────
a = gate_attributes({"grounding": GROUNDING_CLEARED})
check("#36 escalation is recorded", a.get("eval.gate.escalated"), True)
check("#36 …and that the judge CLEARED it", a.get("eval.gate.cleared"), True)
check("#36 …a cleared turn did not act", a.get("eval.gate.acted"), False)
check("#36 …and parked nothing", a.get("eval.gate.parked"), False)

a = gate_attributes({"grounding": GROUNDING_QUIET})
check("a trigger that never fired reports no escalation",
      a.get("eval.gate.escalated"), None)
check("…and no cleared flag (absence, not a false negative)",
      a.get("eval.gate.cleared"), None)

# ── 5 · judge votes survive verbatim (#44 — "deterministic" was a lie) ──────
a = gate_attributes({"grounding": GROUNDING_BLOCK})
check("judge votes are lifted from the adjudication", a.get("eval.gate.judge_votes"), "0/2")
check("…with the family that ruled", a.get("eval.gate.judge_family"), "groundedness")
check("a confirmed block acted", a.get("eval.gate.acted"), True)
check("…and parked", a.get("eval.gate.parked"), True)
check("…and is reported as a block", a.get("eval.gate.grounding"), "block")

# `needs_judge` is the PRE-adjudication trigger flag and is persisted False on turns a
# judge demonstrably ruled on (#37) — so it must never be the source of this signal.
a = gate_attributes({"grounding": dict(GROUNDING_BLOCK, needs_judge=False)})
check("escalation is read from the adjudication, not needs_judge",
      a.get("eval.gate.escalated"), True)

# ── 6 · honesty + the acted predicate ───────────────────────────────────────
a = gate_attributes({"honesty": HONESTY_FLAG})
check("honesty flag is recorded", a.get("eval.gate.honesty"), "flag")
check("…a flag records but does not act", a.get("eval.gate.acted"), False)

# annotate attaches a note without altering the answer ⇒ recorded, not an action.
a = gate_attributes({"guardrail": dict(OUTBOUND_REDACT, action="annotate", review_id=None)})
check("annotate is recorded", a.get("eval.gate.outbound"), "annotate")
check("…but is not an action", a.get("eval.gate.acted"), False)
check("…and parks nothing", a.get("eval.gate.parked"), False)

# ── 7 · malformed stamps degrade, never raise ───────────────────────────────
for bad in ({"grounding": "not-a-dict"}, {"guardrail": 42}, {"guardrail_inbound": []},
            {"grounding": {}}, {"honesty": None}):
    try:
        gate_attributes(bad)
    except Exception as e:  # noqa: BLE001
        failures.append(f"malformed stamp raised: {bad} -> {e}")
check("an empty grounding dict still yields a decision default",
      gate_attributes({"grounding": {}}).get("eval.gate.grounding"), "pass")

# ── 8 · the aggregation link — counts reach the per-agent cell ──────────────
def span(tag, **gate):
    attrs = {"agent.profile_tag": tag, "eval.profile_class": "rag_focused",
             "eval.gating_failures": 0}
    attrs.update(gate)
    return {"attributes": attrs}


cells = online_quality_by_agent([
    span("A", **gate_attributes({"guardrail_inbound": INBOUND_BLOCK})),
    span("A", **gate_attributes({"guardrail": OUTBOUND_REDACT})),
    span("A", **gate_attributes({"grounding": GROUNDING_CLEARED})),
    span("A", **gate_attributes({})),          # a clean turn
])
g = cells["A"].get("gate") or {}
check("aggregate counts the refusal", g.get("refused"), 1)
check("aggregate counts the redaction", g.get("redacted"), 1)
check("aggregate counts both parks", g.get("parked"), 2)
check("aggregate counts both acting turns", g.get("acted"), 2)
check("aggregate counts the escalation", g.get("escalated"), 1)
check("aggregate counts the cleared escalation", g.get("cleared"), 1)
check("the clean turn is still in the population", cells["A"]["turns"], 4)

# A deployment with the gates off must produce no `gate` key at all — a zeroed
# block would read as "measured zero" where the honest reading is "not recorded".
clean = online_quality_by_agent([span("B"), span("B")])["B"]
check("no gate stamps ⇒ no gate block (absence, not a zero)", "gate" in clean, False)
check("…and no private accumulator leaks into the payload",
      [k for k in clean if k.startswith("_gate")], [])


# ── 9 · #50 · a GATE-ONLY span (refused turn) reaches the gate record only ──
# A refused turn is never scored — feeding an injected prompt to a judge hands the
# attack a second door. But "not scored" must not mean "never happened": the gate's
# outcome reaches the population, while the turn stays out of every quality
# denominator it was never measured against.
def gate_only(tag, **gate):
    attrs = {"agent.profile_tag": tag, "eval.profile_class": "rag_focused",
             "eval.gate_only": True}
    attrs.update(gate)
    return {"attributes": attrs}


mixed = online_quality_by_agent([
    span("C"), span("C"),                                            # 2 scored turns
    gate_only("C", **gate_attributes({"guardrail_inbound": INBOUND_BLOCK})),
])["C"]
check("#50 the refusal reaches the gate record", (mixed.get("gate") or {}).get("refused"), 1)
check("#50 …and is counted as an act", (mixed.get("gate") or {}).get("acted"), 1)
check("#50 …and as parked", (mixed.get("gate") or {}).get("parked"), 1)
check("#50 …but does NOT inflate the scored-turn count", mixed["turns"], 2)
check("#50 …nor the pass/fail series", len(mixed["series"]), 2)
check("#50 …nor the pass rate", mixed["pass_rate"], 1.0)

# A gate-only span alone must not fabricate a scored population.
solo = online_quality_by_agent([
    gate_only("D", **gate_attributes({"guardrail_inbound": INBOUND_BLOCK}))])["D"]
check("#50 a lone refusal scores zero turns", solo["turns"], 0)
check("#50 …with no pass rate to report", solo["pass_rate"], None)
check("#50 …but the gate record still stands", (solo.get("gate") or {}).get("refused"), 1)


# ── 10 · #43 · only GATING dimensions may fail a turn ───────────────────────
# `Verdict.gating` exists so an advisory signal can inform without convicting:
# grounded_overlap is a coarse lexical pre-signal whose own docstring calls the LLM
# judge its refiner, and every judge is non-gating until its kappa calibration clears.
# Both span builders summed `passed is False` over ALL verdicts, so those advisory
# dimensions failed whole turns — and that count is the denominator behind JUDGED, the
# roster pass rate, the sparklines and the Behavioral Trust Score.
from trusted_data_agent.eval.online import gating_failures  # noqa: E402


class V:
    def __init__(self, dimension, passed, gating=True):
        self.dimension, self.passed, self.gating = dimension, passed, gating


check("#43 a failing GATING dimension counts",
      gating_failures([V("safety", False)]), 1)
check("#43 a failing ADVISORY dimension does not",
      gating_failures([V("grounded_overlap", False, gating=False)]), 0)
check("#43 …not even several of them",
      gating_failures([V("grounded_overlap", False, gating=False),
                       V("context_utilization", False, gating=False)]), 0)
check("#43 a mixed turn counts only the gating failure",
      gating_failures([V("safety", False), V("grounded_overlap", False, gating=False)]), 1)
check("#43 abstentions never count", gating_failures([V("efficiency", None)]), 0)
check("#43 passes never count", gating_failures([V("safety", True)]), 0)
check("#43 an empty verdict list is zero", gating_failures([]), 0)
# A verdict object without the attribute at all must default to GATING — silently
# treating an unknown verdict as advisory would hide real failures.
check("#43 a verdict with no gating attribute defaults to gating",
      gating_failures([type("X", (), {"passed": False})()]), 1)

# The flag must be resolved from the REGISTRY, not the Verdict: `Scorer.gating` was
# declared on ~10 scorer classes but never passed into any Verdict, so the field kept
# its True default and every advisory dimension gated. A verdict object that carries no
# gating field at all must still be judged by its dimension's declaration.
import trusted_data_agent.eval.scorers  # noqa: E402,F401 — populate the registry
from trusted_data_agent.eval.scorer import is_gating_dimension  # noqa: E402


class Bare:
    """A verdict as the scorers actually build one: no `gating` field at all."""
    def __init__(self, dimension, passed):
        self.dimension, self.passed = dimension, passed


check("#43 the registry knows a gating dimension", is_gating_dimension("safety"), True)
check("#43 …and an advisory one", is_gating_dimension("grounded_overlap"), False)
check("#43 …and context_utilization is advisory", is_gating_dimension("context_utilization"), False)
check("#43 …an unknown dimension defaults to gating", is_gating_dimension("nope"), True)

check("#43 ROOT: an advisory dim with no verdict flag does not fail the turn",
      gating_failures([Bare("grounded_overlap", False)]), 0)
check("#43 ROOT: …nor context_utilization",
      gating_failures([Bare("context_utilization", False)]), 0)
check("#43 ROOT: a real gating dim still fails it",
      gating_failures([Bare("safety", False)]), 1)
check("#43 ROOT: the live Redis shape — 2 advisory failures, 0 gating",
      gating_failures([Bare("grounded_overlap", False), Bare("context_utilization", False)]), 0)


# ── 11 · #44 · the runs ledger must describe the turn it is explaining ──────
# The row read "turn 1 · deterministic" for a turn TWO judges adjudicated and the
# grounding gate WITHHELD — the defining fact of the turn, absent from the surface built
# to explain it. It read only the eval span, and the gate's outcome was not on the span.
from trusted_data_agent.eval.analysis import agent_runs  # noqa: E402


def _span(**gate):
    attrs = {"agent.profile_tag": "R", "session.id": "s1", "turn.id": "2",
             "eval.profile_class": "rag_focused", "eval.gating_failures": 1,
             "eval.judged": False,
             "eval.dim.safety.passed": True,
             "eval.dim.grounded_overlap.passed": False,
             "eval.dim.context_utilization.passed": False}
    attrs.update(gate)
    return {"attributes": attrs, "start_time_unix_nano": 1}


row = agent_runs([_span(**gate_attributes({"grounding": GROUNDING_BLOCK}))], "R")["runs"][0]
check("#44 the row carries the gate's decision", (row.get("gate") or {}).get("grounding"), "block")
check("#44 …and the judges' votes", (row.get("gate") or {}).get("judge_votes"), "0/2")
check("#44 …and that a judge adjudicated", (row.get("gate") or {}).get("escalated"), True)
check("#44 …and that it was parked", (row.get("gate") or {}).get("parked"), True)
check("#44 the row names the dimensions that decided it",
      row.get("failed_dimensions"), ["context_utilization", "grounded_overlap"])

# A turn with no gate record must carry no gate block — absence, never a zeroed stub.
plain = agent_runs([_span()], "R")["runs"][0]
check("#44 a turn with no gate record carries none", plain.get("gate"), None)
check("#44 …but still names its failing dimensions",
      plain.get("failed_dimensions"), ["context_utilization", "grounded_overlap"])
check("#44 a clean turn names no failures",
      agent_runs([{"attributes": {"agent.profile_tag": "R", "session.id": "s", "turn.id": "1",
                                  "eval.gating_failures": 0,
                                  "eval.dim.safety.passed": True},
                   "start_time_unix_nano": 1}], "R")["runs"][0]["failed_dimensions"], [])

# A refused turn reaches the ledger as a gate-only row: no scores, but the act is there.
refused = agent_runs([{"attributes": {
    "agent.profile_tag": "R", "session.id": "s2", "turn.id": "9", "eval.gate_only": True,
    **gate_attributes({"guardrail_inbound": INBOUND_BLOCK})}, "start_time_unix_nano": 2}], "R")
r0 = refused["runs"][0]
check("#44 a refused turn is marked gate-only", r0["gate_only"], True)
check("#44 …and names the refusal", (r0.get("gate") or {}).get("inbound"), "block")
check("#44 …with no dimensions to show", r0["failed_dimensions"], [])

print()
if failures:
    print(f"FAILED — {len(failures)} assertion(s)\n")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print("ALL PASS — I1 + #43 + #44: the span, the turn verdict, and the ledger row.")
