"""
The assurance substrate gate — the contract, the registry, and BEHAVIOUR PRESERVATION.

The load-bearing section is §3. An adapter that changes what a property computes while
claiming to be a wiring change is the worst possible outcome of this migration: it would
move every band on every historical comparison and nobody would know which change caused
it. So each adapter is driven beside the code it wraps and asserted to carry the same
facts.

Pure: no server, no network, no DB.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

from trusted_data_agent.agent import assurance as A                      # noqa: E402
from trusted_data_agent.agent.assurance import conformance, registry     # noqa: E402
from trusted_data_agent.agent.assurance import properties as props       # noqa: E402
from trusted_data_agent.agent.assurance.capabilities import (            # noqa: E402
    AssessorCapability as C, Lane, lanes_for)
from trusted_data_agent.agent.assurance.contract import (                # noqa: E402
    AssessmentContext, Assessor, Finding, Severity)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


def section(t):
    print(f"\n{'─' * 4} {t} {'─' * max(0, 66 - len(t))}")


A.bootstrap()

# ── 1 · the contract holds its own invariants ────────────────────────────────
section("1 · the contract")

check("the property vocabulary is closed and non-empty", len(props.keys()) >= 9)
check("every property declares at least one lane",
      all(p.lanes for p in props.PROPERTIES))
check("a property with a missing lane explains it rather than being silently short",
      all(p.lane_notes.get(l.name.lower())
          for p in props.PROPERTIES
          for l in (Lane.IN_FLIGHT, Lane.POST_HOC)
          if l not in p.lanes),
      "an absent lane with no note is indistinguishable from an oversight (Rule 3.2)")

# Lane eligibility is DERIVED. A module that gains a model call must lose the
# unconditional in-flight lane without anyone editing a second list.
check("a pure assessor may run unconditionally in flight",
      Lane.IN_FLIGHT in lanes_for({C.PURE, C.REPLAYABLE}))
check("…and a model-calling one may NOT",
      Lane.IN_FLIGHT not in lanes_for({C.NEEDS_MODEL, C.REPLAYABLE}),
      "a judge on every turn's hot path is seconds and tokens per turn")
check("…but may run behind a trigger",
      Lane.IN_FLIGHT_TRIGGERED in lanes_for({C.NEEDS_MODEL, C.REPLAYABLE}))
check("a non-replayable assessor cannot be scored post-hoc",
      Lane.POST_HOC not in lanes_for({C.PURE}),
      "it would score the world as it is now, not as the turn saw it")

# ── 2 · guarded invocation — absence is loud ─────────────────────────────────
section("2 · a missing input abstains; it never reports clean")


class _Boom(Assessor):
    key, property, label = "boom", "answer_support", "raises"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})
    requires = frozenset({"answer"})

    def assess(self, ctx):
        raise RuntimeError("kaboom")


class _Liar(Assessor):
    key, property, label = "liar", "answer_support", "returns junk"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})
    requires = frozenset()

    def assess(self, ctx):
        return "not a finding"


f = _Boom().run(AssessmentContext(answer="x"))
check("a raising assessor abstains instead of taking the turn down",
      f.abstained is not None and not f.triggered)
f = _Boom().run(AssessmentContext())
check("a missing required input abstains and NAMES what was missing",
      f.abstained is not None and "answer" in f.abstained)
check("…and an abstention is never actionable", not f.actionable)
f = _Liar().run(AssessmentContext())
check("an assessor returning the wrong type abstains", f.abstained is not None)

# ── 3 · BEHAVIOUR PRESERVATION — the adapters carry the same facts ───────────
section("3 · adapters reproduce the code they wrap")

from trusted_data_agent.agent.grounding import corpus as _corpus          # noqa: E402
from trusted_data_agent.agent import obda as _obda                        # noqa: E402
from trusted_data_agent.agent.honesty import primitives as _hp            # noqa: E402

ANSWER = "The 2024 total was 41,900 units across 3 regions."
CHUNKS = ["Regional report: the 2024 total was 41,900 units.", "Coverage: 3 regions."]

native = _corpus.assess(ANSWER, CHUNKS, "")
got = registry.resolve("answer_support", Lane.POST_HOC).run(
    AssessmentContext(answer=ANSWER, chunks=tuple(CHUNKS)))
check("answer_support: the adapter reports the native claim count",
      got.metrics.get("claims_checked") == float(native.claims_checked),
      f"{got.metrics} vs claims_checked={native.claims_checked}")
check("…the same unsupported spans, in the same order",
      tuple(got.evidence) == tuple(native.unsupported_spans))
check("…and the same trigger decision",
      got.triggered == bool(native.unsupported_spans))
check("…carrying the native finding for surfaces that understand it",
      got.payload.__class__ is native.__class__)

# The second-chance step is what made the two lanes disagree. Applying it from the turn's
# own record is the fix; proving it CHANGES the result is what makes the fix meaningful.
UNSUP = "The 2024 total was 99,999 units."
bare = registry.resolve("answer_support", Lane.POST_HOC).run(
    AssessmentContext(answer=UNSUP, chunks=tuple(CHUNKS)))
if bare.evidence:
    cleared = registry.resolve("answer_support", Lane.POST_HOC).run(
        AssessmentContext(answer=UNSUP, chunks=tuple(CHUNKS),
                          stamp={"grounding": {"second_chance_cleared": list(bare.evidence)}}))
    check("answer_support: a span the gate cleared is not re-convicted post-hoc",
          not cleared.triggered and cleared.evidence == (),
          f"still flagged: {cleared.evidence}")
    check("…and the record of the clearing survives into the summary",
          "second look" in cleared.summary)
else:
    check("answer_support: the fixture produced an unsupported span to clear", False,
          "fixture no longer exercises the second-chance path")

# The branch that overturned the map's own recommendation. A fluent fabrication carries no
# number, quote or identifier, so the span check has nothing to grade — and abstaining
# there would let through the one thing a groundedness check most needs to catch. The
# scorer's history records this being tried and reverted once before; the eval gate caught
# it a second time when this assessor first shipped claims-only.
_flu = registry.resolve("answer_support", Lane.POST_HOC).run(
    AssessmentContext(
        answer="Quantum entanglement enables faster-than-light teleportation of refunds.",
        chunks=("Refund policy: returns are accepted within 30 days for unused items.",)))
check("a span-free fluent fabrication is CAUGHT, not abstained on",
      _flu.abstained is None and _flu.triggered,
      f"abstained={_flu.abstained} triggered={_flu.triggered}")
check("…and the finding says which branch decided it",
      "no checkable claim" in _flu.summary,
      "the fact moved from an internal metric flag into the summary, where prose belongs")
_claims = registry.resolve("answer_support", Lane.POST_HOC).run(
    AssessmentContext(answer=ANSWER, chunks=tuple(CHUNKS)))
# Asserted on the BRANCH MARKER, not the exact wording: the lexical branch is the only
# one that says "no checkable claim", so the span branch is everything else. Matching a
# whole sentence made this fail when the summaries were routed through `plural()` — a
# rendering change reported as a behaviour change.
check("…while an answer WITH claims takes the span branch",
      "no checkable claim" not in _claims.summary and _claims.abstained is None,
      f"summary was: {_claims.summary!r}")

CALLS = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"},
          "result": {"status": "success", "results": [{"a": 1}]}}]
native_obda = _obda.scan_obda(CALLS)
# the property is behind the deployment's master switch (declared ONCE on the Property,
# enforced ONCE in Assessor.run); switch it on for the native-parity checks and prove the
# off state first — off ⇒ the assessor abstains NAMING the switch's route, never a finding
from trusted_data_agent.core.config import APP_CONFIG as _CFG
from trusted_data_agent.agent.assurance import properties as _pr
_prev_sw = _OP.plane_available("read")
check("data_governance declares its lane on the Property — the OBDA-READ component", _pr.get("data_governance").master_switch == "component:obda_read")
_OP.force("read", False)
_off = registry.resolve("data_governance", Lane.POST_HOC).run(AssessmentContext(tool_calls=tuple(CALLS)))
check("switch OFF ⇒ the assessor abstains naming the lane and the route, and never triggers (one gate for every reader)",
      bool(_off.abstained) and "off here" in _off.abstained and "Administration" in _off.abstained and not _off.triggered)
_OP.force("read", True)
got = registry.resolve("data_governance", Lane.POST_HOC).run(
    AssessmentContext(tool_calls=tuple(CALLS)))
_OP.force("read", _prev_sw)
check("data_governance: the adapter reports the native lane split",
      got.metrics.get("operations") == float(native_obda.get("total_data_ops") or 0),
      f"{got.metrics} vs {native_obda}")
check("…and READS the native ratio rather than recomputing it",
      got.metrics.get("governed_ratio") == float(native_obda.get("governed_ratio") or 0.0),
      "a second implementation of one number is the defect the map scores 3/5 elsewhere")
check("…and free-forming is reported, never convicted",
      got.severity in (Severity.INFO, Severity.NONE),
      "which lane was used is a fact; whether it matters is policy")
check("data_governance abstains when the turn did no data work",
      registry.resolve("data_governance", Lane.POST_HOC).run(
          AssessmentContext(tool_calls=())).abstained is not None)

check("absence_claim abstains when no query was attempted",
      registry.resolve("absence_claim", Lane.POST_HOC).run(
          AssessmentContext(answer="There is no such table.",
                            tool_calls=({"tool_name": "TDA_FinalReport"},))
      ).abstained is not None)
check("absence_claim abstains when the answer claims no absence",
      registry.resolve("absence_claim", Lane.POST_HOC).run(
          AssessmentContext(answer="There are 4 rows.", tool_calls=tuple(CALLS))
      ).abstained is not None)

# ── 3b · tool_authority — the A17 fold, and the bug that hid inside it ───────
section("3b · authority is graded against the scope that was in force")

_AUTH = {"applied": True, "principal_id": "agent:p1", "origin": "scheduler",
         "autonomous": True, "deny_writes": True,
         "denied_tools": ["base_writeQuery"], "allowed_connectors": None,
         "max_cost_per_run": None}
_names = ("base_readQuery", "base_writeQuery")
_ta = registry.resolve("tool_authority", Lane.POST_HOC)


def _auth_ctx(auth):
    return AssessmentContext(
        tool_calls=tuple({"tool_name": n} for n in _names),
        stamp={"authority": auth} if auth is not None else {})


f = _ta.run(_auth_ctx(_AUTH))
# THE assertion this section exists for. `is_tool_allowed` returns (allowed, reason) — a
# TUPLE — and the first version of this assessor tested it as a bool. A non-empty tuple is
# truthy, so `not is_tool_allowed(...)` was always False and EVERY violation passed. A
# measurement that cannot fail is worse than none: it reports a boundary as holding.
check("a denied tool is caught, not passed",
      f.abstained is None and f.triggered,
      f"abstained={f.abstained} triggered={f.triggered}")
check("…and the boundary's OWN reason is carried as evidence",
      any("denied to this agent" in e for e in f.evidence),
      "the reason is the WHY a station could never state before")
check("…with the ratio reported, not just a verdict",
      f.metrics.get("within_scope_ratio") == 0.5)

f = _ta.run(_auth_ctx({**_AUTH, "denied_tools": [], "deny_writes": False}))
check("an unrestricted autonomous turn passes", f.abstained is None and not f.triggered)

# The symmetry that makes this measurement honest: the boundary governs AUTONOMOUS turns,
# so grading an interactive one would convict a rule that was never in force.
f = _ta.run(_auth_ctx({"applied": False, "origin": "interactive"}))
check("an interactive turn abstains rather than being graded",
      f.abstained is not None and "not in force" in f.abstained)
f = _ta.run(_auth_ctx(None))
check("a turn predating the stamp abstains — what was in force cannot be known",
      f.abstained is not None)

# The scope is SEALED on the turn, never re-resolved. Re-resolving would grade a
# historical turn against today's config — the defect that was retroactively flipping
# historical SIGNED readings until that stamp was sealed at answer time.
import inspect as _insp                                                   # noqa: E402
_src = _insp.getsource(type(_ta))
check("the assessor reads the sealed stamp and never re-resolves a profile",
      "stamp" in _src and "get_profile" not in _src and "config_manager" not in _src)
check("…and requires the stamp, so it cannot be bound in flight",
      "stamp" in _ta.requires
      and Lane.IN_FLIGHT not in {Lane[l] for l in _ta.describe()["lanes"]}
      or conformance.check_binding("tool_authority", _ta.key, Lane.IN_FLIGHT) != [])

# ── 3c · answer_scope + output_sensitivity — read first, compute second ──────
section("3c · a measurement reads what the mechanism recorded")

_sc = registry.resolve("answer_scope", Lane.POST_HOC)
_scope_ctx = dict(scope_databases=("restaurants_mvm",),
                  tool_calls=({"tool_name": "base_readQuery",
                               "arguments": {"database_name": "finance_ledger"}},))
_stamped = _sc.run(AssessmentContext(
    **_scope_ctx, stamp={"grounding": {"out_of_scope_dbs": ["finance_ledger"]}}))
_computed = _sc.run(AssessmentContext(**_scope_ctx))
check("answer_scope reads the gate's own finding when it recorded one",
      "gate's own finding" in _stamped.summary and _stamped.triggered)
check("…computes it with the gate's own primitive when the gate did not run",
      "recomputed" in _computed.summary and _computed.triggered,
      "losing the reading entirely would be worse than recomputing it")
check("…and the two branches AGREE on the same turn",
      _stamped.evidence == _computed.evidence,
      f"{_stamped.evidence} vs {_computed.evidence} — a drift between them is the "
      f"A6 defect returning")
check("…and each branch SAYS which it was",
      "gate's own finding" in _stamped.summary and "recomputed" in _computed.summary,
      "a reader must be able to tell a read from a recomputation")
check("no declared scope abstains rather than reading clean",
      _sc.run(AssessmentContext(tool_calls=_scope_ctx["tool_calls"])).abstained is not None)

_os = registry.resolve("output_sensitivity", Lane.POST_HOC)
_GR = {"action": "redact", "mode": "enforce", "direction": "outbound",
       "categories": ["pii"],
       "findings": [{"category": "pii", "label": "email",
                     "excerpt": "bob@example.com"}]}
_f = _os.run(AssessmentContext(answer="x", stamp={"guardrail": _GR}))
check("output_sensitivity NAMES the finding rather than counting steps",
      _f.triggered and any("email" in e for e in _f.evidence), str(_f.evidence))
# THE invariant. The guardrail's persisted verdict carries an `excerpt`, which can hold
# the matched value itself. A Finding travels to surfaces that render it and cannot know
# the difference, so the rule is enforced HERE rather than trusted at each of them.
_blob = " ".join(list(_f.evidence) + [_f.summary, str(_f.payload)])
check("…and NEVER carries the matched value, only its label",
      "bob@example.com" not in _blob,
      "the excerpt field must not be propagated — privacy is a property of the envelope")
check("…reporting that the guard ACTED, not merely that it found something",
      _f.metrics.get("acted") == 1.0)
check("…and says the reading came from the record",
      "scanned, not recorded" not in _f.summary)

# Both lanes, one assessor — possible only because `evaluate_outbound` was split into
# assess + decide. Before that this property read `gap`: a post-hoc lane cannot bind to a
# function that has already applied a policy.
_scan = _os.run(AssessmentContext(answer="Mail bob@example.com about it."))
check("with no record it SCANS with the guard's own assessor",
      _scan.abstained is None and _scan.triggered
      and "scanned, not recorded" in _scan.summary)
check("…and says so, so a reader can tell a scan from a record",
      "scanned, not recorded" in _scan.summary)
check("…still carrying labels only, never the matched value",
      "bob@example.com" not in " ".join(list(_scan.evidence) + [_scan.summary]),
      "OutboundFindings.pii holds raw values for redaction — they must not cross here")
# The X1 whitelist must apply in the scan branch too, or it would be STRICTER than the
# gate on the same answer: a second assessment disagreeing with the first.
_url = "https://evil.example.com/x?d=eyJhIjoxfQ=="
_novel = _os.run(AssessmentContext(answer=f"See {_url} for details."))
_known = _os.run(AssessmentContext(answer=f"See {_url} for details.",
                                   chunks=(f"a source mentioning {_url}",)))
check("a novel exfil-shaped URL triggers in the scan branch", _novel.triggered)
check("…and one present in the turn's own sources does not",
      not _known.triggered,
      "omitting source_texts would make this branch stricter than the gate")
check("output_sensitivity is bound to BOTH lanes",
      registry.symmetry("output_sensitivity")["state"] == "shared")

# ── 3d · retrieval_sufficiency — the Part E "keep", made elegant ─────────────
section("3d · one assessment, two statistics, two questions")

_rs = registry.resolve("retrieval_sufficiency", Lane.POST_HOC)
_f = _rs.run(AssessmentContext(retrieval_scores=(0.91, 0.22, 0.18, 0.31)))
# Part E kept this pair rather than folding it: top answers "was the BEST chunk good
# enough to answer?", mean answers "was the retrieval SET good?". Folding to either would
# lose a real reading — so ONE assessment returns both.
check("both statistics come from ONE assessment",
      _f.metrics.get("top_score") == 0.91 and _f.metrics.get("mean_score") == 0.405)
check("…and they DIVERGE legitimately on the same data",
      _f.metrics["top_score"] != _f.metrics["mean_score"],
      "one excellent chunk among poor ones — the reason neither could be dropped")
check("…so the two lanes can disagree about the ANSWER, never about the DATA",
      _f.metrics["top_score"] == max(0.91, 0.22, 0.18, 0.31))

# The mechanism defaults its absolute threshold OFF because such floors do not transfer
# across embedding models. An assessor that invented one would manufacture the false red
# the mechanism deliberately refuses to produce.
_no_cfg = _rs.run(AssessmentContext(retrieval_scores=(0.2, 0.1)))
check("with no configured threshold nothing is judged weak",
      not _no_cfg.triggered and _no_cfg.metrics["qualifying_chunks"] == 2.0,
      "an invented absolute floor is the calibration defect this row exists to fix")
_cfg = _rs.run(AssessmentContext(retrieval_scores=(0.91, 0.22),
                                 config={"knowledgeConfig": {"sufficiencyThreshold": 0.95}}))
check("…and the configured one is honoured when set", _cfg.triggered)
_bad = _rs.run(AssessmentContext(retrieval_scores=(0.9,),
                                 config={"knowledgeConfig": {"sufficiencyThreshold": "oops"}}))
check("a malformed threshold falls back rather than disabling the reading",
      _bad.abstained is None,
      "a typo must never silently switch a signal off")
check("no scores abstains", _rs.run(AssessmentContext()).abstained is not None)

# Bound to BOTH lanes now that `assess_rag` is split into scan + decide AND the raw
# similarity scores reach the gate. It read `gap` until both were true — the registry
# reporting work left rather than claiming a lane nothing consulted.
check("it reads `shared` — the gate genuinely consults this assessment",
      registry.symmetry("retrieval_sufficiency")["state"] == "shared")

# ── 3e · the SUPPORT SET is one definition (symmetry map A5) ─────────────────
section("3e · one support set, so two lanes cannot grade against different evidence")

_ctx = AssessmentContext(chunks=("a retrieved doc",), tool_outputs=("a tool result",))
check("the support set is retrieval AND tool evidence",
      _ctx.support_set() == ("a retrieved doc", "a tool result"),
      "the corpus family saw retrieval; numeric_fidelity saw both — neither knew")
check("…kept as separate fields, so an assessor chooses explicitly",
      _ctx.chunks == ("a retrieved doc",) and _ctx.tool_outputs == ("a tool result",),
      "a silent merge is a support set an assessor cannot see")
check("an empty context yields an empty support set",
      AssessmentContext().support_set() == ())
# from_run must supply BOTH, mirroring RunRecord.grounding_text()'s own definition —
# otherwise the substrate would reintroduce the very split it exists to close.
import inspect as _insp2                                                  # noqa: E402
from trusted_data_agent.agent.assurance import run_context as _rc2        # noqa: E402
check("from_run supplies tool evidence, not only chunks",
      "tool_outputs=" in _insp2.getsource(_rc2.from_run))

# ── 3f · the BY-DESIGN divergence — a gate that acted changes the subject ────
section("3f · when a gate replaces the answer, the two lanes assess different texts")

# In flight a gate assesses what the MODEL produced; afterwards the delivered text may be
# something the PLATFORM wrote. That divergence is by design, not a symmetry defect — and
# an assessor that does not know which subject it holds will grade the platform's prose as
# the model's. Measured once: a blocked turn scored 3% context utilization, a damning
# number about a ~30-word notice the model never wrote.
_WHY = "the grounding gate withheld this answer"
_pa = AssessmentContext(answer="Withheld: unsupported claims.", chunks=("a doc",),
                        tool_calls=({"tool_name": "base_readQuery",
                                     "result": {"status": "error"}},),
                        platform_authored=_WHY)

_as = registry.resolve("answer_support", Lane.POST_HOC)
check("an assessor whose subject is the MODEL abstains on platform text",
      _as.run(_pa).abstained == _WHY)
check("…and says so in the platform's own words, not a generic message",
      "withheld" in (_as.run(_pa).abstained or ""))
check("…while the same assessor grades a model-authored answer normally",
      _as.run(AssessmentContext(answer="The 2024 total was 41,900 units.",
                                chunks=("the 2024 total was 41,900 units",))
              ).abstained is None)

# NOT uniform, and that is the point. "Does what reached the READER carry sensitive
# content?" is a question about the delivered text, so abstaining would be a blind spot at
# exactly the moment a gate acted — and the notice quotes the spans it is reporting.
_os2 = registry.resolve("output_sensitivity", Lane.POST_HOC)
check("an assessor whose subject is the DELIVERED text does NOT abstain",
      _os2.run(_pa).abstained is None,
      "the reader's exposure is about what reached them, whoever wrote it")
check("…and declares that explicitly rather than inheriting a default",
      _os2.subject_is_model_output is False and _as.subject_is_model_output is True)

# Enforced ONCE. Three scorers used to remember this individually, which is how one site
# eventually forgets — the same reason the privacy invariant lives in the envelope.
import inspect as _insp3                                                  # noqa: E402
from trusted_data_agent.agent.assurance import contract as _con           # noqa: E402
check("the rule lives in the envelope, not in each assessor",
      "subject_is_model_output and ctx.platform_authored" in _insp3.getsource(_con.Assessor.run))
from trusted_data_agent.agent.assurance import run_context as _rc3        # noqa: E402
check("…fed from the ONE existing definition",
      "platform_authored_answer" in _insp3.getsource(_rc3.from_run))

# ── 3g · a metric is a number a READER can use ───────────────────────────────
section("3g · metrics carry findings, never sentinels or internal flags")

# `floor: -1.0` meant "unset" and the renderer had to know to hide it; `branch_lexical`
# and `from_stamp` were internal flags in a reader-facing field. Both are leaky contracts:
# every surface rendering a Finding would have to learn which numbers are not numbers.
_BANNED = {"floor", "branch_lexical", "from_stamp"}
for _p in ("answer_support", "answer_scope", "context_use", "output_sensitivity",
           "retrieval_sufficiency", "tool_repetition"):
    _asr = registry.resolve(_p, Lane.POST_HOC)
    if _asr is None:
        continue
    _fin = _asr.run(AssessmentContext(
        answer="The 2024 total was 41,900 units.", chunks=("the 2024 total was 41,900 units",),
        tool_calls=({"tool_name": "base_readQuery", "arguments": {"sql": "x"}},),
        retrieval_scores=(0.8, 0.4), scope_databases=("db",),
        stamp={"guardrail": {"action": "pass", "findings": []}}))
    _m = _fin.metrics or {}
    check(f"{_p}: no internal flags in metrics", not (set(_m) & _BANNED), str(set(_m) & _BANNED))
    check(f"{_p}: no sentinel values", all(v >= 0 for v in _m.values()), str(_m))

# ── 3h · a metric's KIND is derivable from its key ───────────────────────────
section("3h · every metric key names its kind, so no surface has to guess")

# `max_repeats: 1` rendered as "100%" under a value-based rule; `numeric_fidelity: 1.0`
# rendered as "1" under a suffix rule that did not cover it. A number whose kind is guessed
# will eventually be guessed wrong, so the naming convention IS the contract.
import re as _re2                                                          # noqa: E402
_RATIO = _re2.compile(r"(_ratio|_fidelity|_rate)$")
_SCORE = _re2.compile(r"_score$")
_FLAG = _re2.compile(r"^(acted|restricted)$")
_COUNT = _re2.compile(
    r"^(claims_checked|chunks|answer_words|findings|operations|out_of_scope|tool_calls"
    r"|qualifying_chunks|queries_attempted|queries_succeeded|max_repeats|distinct_calls"
    r"|total_calls)$")

_seen = set()
_probe = AssessmentContext(
    answer="The 2024 total was 41,900 units.",
    chunks=("the 2024 total was 41,900 units",),
    tool_calls=({"tool_name": "base_readQuery", "arguments": {"sql": "x"}},),
    retrieval_scores=(0.81, 0.42), scope_databases=("db",),
    stamp={"guardrail": {"action": "pass", "findings": []},
           "authority": {"applied": True, "autonomous": True, "deny_writes": False,
                         "denied_tools": [], "allowed_connectors": None,
                         "max_cost_per_run": None}})
for _a2 in registry.assessors():
    _f2 = _a2.run(_probe)
    _seen |= set((_f2.metrics or {}).keys())
check(f"the probe exercised the assessors ({len(_seen)} distinct metrics)", len(_seen) >= 10)
_unkinded = sorted(k for k in _seen
                   if not (_RATIO.search(k) or _SCORE.search(k) or _FLAG.match(k)
                           or _COUNT.match(k)))
check("every emitted metric key matches exactly one kind", not _unkinded,
      f"ambiguous: {_unkinded} — a surface would have to guess how to render these")
_js = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "handlers",
                        "assuranceDial.js")).read()
check("the renderer derives the kind from the KEY, not the value",
      "METRIC_RATIO" in _js and "_fidelity" in _js)
check("…so a count is never rendered as a percentage",
      "if (METRIC_FLAG.test(key))" in _js and "Number.isInteger(v)" in _js)

# ── 3i · the Guide's PROPERTY entries — the seventh class ────────────────────
section("3i · every module row has a destination")

# The Trust Circle's mechanism rows expand to the modules assessing each property and
# route to `property_<key>`. Those entries did not exist, so every row was a dead end —
# the one thing the Guide's completeness rule bans.
_ents = {e["k"]: e for e in registry.guide_entries()}
check(f"one entry per property ({len(_ents)})", len(_ents) == len(props.keys()))
check("…keyed to match the route the module rows emit",
      all(k.startswith("property_") for k in _ents))
check("…declaring their own class, so the Guide can shelve them",
      all(e["cls"] == "property" for e in _ents.values()))
check("…and carrying the property's question as the body",
      all(e["long"] for e in _ents.values()))

_as = _ents["property_answer_support"]
# ONE in-flight row, not two: the unconditional and triggered lanes are ALTERNATIVES, so
# listing the unused one as "nothing bound" reads as a gap that is not one.
check("a bound property shows exactly two lanes", len(_as["lanes"]) == 2)
check("…naming the live module, not a description of one",
      all(l["assessor"] == "corpus_support" for l in _as["lanes"]))
# Three different POWERS, and a reader must not have to infer which one a module has.
check("…and what each lane may DO", {l["mode"] for l in _as["lanes"]} == {"decides", "grades"})
check("a triggered binding says it can only escalate",
      any(l.get("mode") == "escalates"
          for l in _ents["property_context_use"]["lanes"]))
# A lane the property does not HAVE is not an empty lane.
_ri = _ents["property_request_integrity"]
check("a by-design absent lane carries its REASON, not 'nothing bound'",
      any(l["assessor"] is None and "ground truth" in (l["note"] or "")
          for l in _ri["lanes"]))
check("…while a real gap says nothing is bound",
      any(l["assessor"] is None and "nothing is bound" in (l["note"] or "")
          for l in _ri["lanes"]))
_js2 = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "handlers",
                         "qualityDimensions.js")).read()
check("the Guide shelves the new class", "property: 'Properties'" in _js2)
check("…renders the lanes", "function lanesRow(" in _js2 and "${lanesRow(k)}" in _js2)
# "no module is bound to this property" beside "there is no ground truth to grade a
# refusal against" reads as though both absences are gaps, when one is a design.
#
# EXECUTED, not grepped: the first version of this assertion checked that the variable
# `hasDesignNote` appeared in the source, and passed while the suppression was disabled —
# the variable was still declared, just unused.
import re as _re3, subprocess as _sp                                       # noqa: E402
_i = _js2.index("    function lanesRow(k) {")
_j = _js2.index("    function relatedChips(k) {")
# `lanesRow` renders each bound module through `moduleName` (the M badge). Extract the
# REAL one rather than stubbing it: a stub that returned a bare label would let the badge
# be deleted from the lanes row without this probe noticing — the exact faithful-stub rule
# this suite exists to enforce. Its only collaborator is `esc`, supplied below.
_mi = _js2.index("    function moduleName(mod, label, prop) {")
_mj = _js2.index("    function modulesRow(k) {")
_probe = f"""
const LANES = {{
  shared_p: [{{lane:"IN_FLIGHT",assessor:"m",label:"M",mode:"decides",note:null}},
             {{lane:"POST_HOC",assessor:"m",label:"M",mode:"grades",note:null}}],
  design_p: [{{lane:"IN_FLIGHT",assessor:null,label:null,mode:null,
               note:"nothing is bound to this lane"}},
             {{lane:"POST_HOC",assessor:null,label:null,mode:null,
               note:"There is no ground truth to grade a per-turn refusal against."}}],
}};
const SYMMETRY = {{ shared_p: "shared", design_p: "unbound" }};
const esc = (x) => String(x == null ? "" : x);
// the lead word with its definition on hover — stubbed to the bare lead here
const leadHtml = (lead, text, cls) => `<span class="${{cls || "qd-rel-lead"}}">${{text || lead}}</span>`;
{_js2[_mi:_mj]}
{_js2[_i:_j]}
const out = {{ shared: lanesRow("shared_p"), design: lanesRow("design_p") }};
console.log(JSON.stringify(out));
"""
_r = _sp.run(["node", "-e", _probe], capture_output=True, text=True)
import json as _json                                                       # noqa: E402
_lanes_out = _json.loads(_r.stdout or "{}") if _r.returncode == 0 else {}
check("the lanes renderer was executed", bool(_lanes_out), _r.stderr[:200])
check("a shared property states the symmetry in words, not a wire token",
      "never disagree" in _lanes_out.get("shared", "")
      and "shared" not in _lanes_out.get("shared", ""))
check("…and a by-design lane SUPPRESSES the whole-property sentence",
      "No module is bound to this property" not in _lanes_out.get("design", ""),
      "the lane note already says more; no slot may restate another (Rule 3.0)")
check("…while still showing the lane's own reason",
      "ground truth" in _lanes_out.get("design", ""))
check("…and a lane's POWER is named in words",
      "can act on the answer" in _lanes_out.get("shared", "")
      and "reports only" in _lanes_out.get("shared", ""))
# A property is not measured, not tuned, and has no per-turn status — the same rule the
# guarantees and moments already follow.
check("…and treats a property as a grouping, not a measurement",
      "|| classOf(k) === 'property'" in _js2)

# ── 4 · conformance refuses what a UX must not offer ─────────────────────────
section("4 · a remap is validated in ONE place")

check("binding an assessor of a different property is refused",
      any("assesses" in p for p in
          conformance.check_binding("absence_claim", "corpus_support", Lane.POST_HOC)))
check("binding a lane the property does not have is refused, with the reason",
      any("no POST_HOC lane" in p or "has no" in p for p in
          conformance.check_binding("request_integrity", "corpus_support", Lane.POST_HOC)))
check("binding an unknown assessor is refused",
      conformance.check_binding("answer_support", "nope", Lane.POST_HOC) != [])
check("a valid binding is permitted",
      conformance.check_binding("answer_support", "corpus_support", Lane.POST_HOC) == [])


class _NeedsStamp(Assessor):
    key, property, label = "needs_stamp", "answer_support", "needs the stamp"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})
    requires = frozenset({"stamp"})

    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False)


registry.register(_NeedsStamp())
check("an assessor needing an input the lane cannot supply is refused IN FLIGHT",
      any("cannot supply" in p for p in
          conformance.check_binding("answer_support", "needs_stamp", Lane.IN_FLIGHT)),
      "it would run, see nothing, and report clean forever")
check("…and permitted post-hoc, where the stamp exists",
      conformance.check_binding("answer_support", "needs_stamp", Lane.POST_HOC) == [])
check("bind() REFUSES rather than partially applying",
      registry.bind("answer_support", "needs_stamp",
                    (Lane.IN_FLIGHT, Lane.POST_HOC)) != []
      and registry.resolve("answer_support", Lane.POST_HOC).key == "corpus_support",
      "a partial bind would leave one lane unassessed while the screen said success")

# ── 5 · symmetry is DERIVED from the bindings ────────────────────────────────
section("5 · symmetry is read from the table, never hand-recorded")

for p in ("answer_support", "absence_claim", "data_governance"):
    s = registry.symmetry(p)
    check(f"{p} reads `shared` — one assessor, both lanes",
          s["state"] == "shared" and s["in_flight"] == s["post_hoc"], str(s))
# `context_use` was the example here until Part D bound it — which is the improvement,
# not a regression. `request_integrity` is genuinely unbound: single-lane by design, and
# that lane has no assessor.
check("an unbound property reads `unbound`, not `shared`",
      registry.symmetry("request_integrity")["state"] == "unbound")
check("a property with no in-flight lane by design is not reported as a gap",
      registry.symmetry("request_integrity")["wants_post_hoc"] is False)
check("the binding table lists UNBOUND rows too",
      any(not r["bound"] for r in registry.bindings()),
      "'nothing assesses this' is the reading that must not be silent")
check("the table is serializable for a remapping UX",
      all(isinstance(r.get("property"), str) and isinstance(r.get("lane"), str)
          for r in registry.bindings()))
check("the shipped bindings are all conformant", conformance.audit() == [],
      str(conformance.audit()))

# ── 6 · the naming carries the synergy ───────────────────────────────────────
section("6 · one property name, both lanes named from it")

import pathlib                                                            # noqa: E402
_adir = pathlib.Path(__file__).parent.parent / "src/trusted_data_agent/agent/assurance/assessors"
_mods = {p.stem for p in _adir.glob("*.py")} - {"__init__"}
check("every assessor module is named for a PROPERTY, not a mechanism",
      _mods <= set(props.keys()),
      f"not property names: {sorted(_mods - set(props.keys()))}")
check("…and every registered assessor declares a real property",
      all(props.get(a.property) for a in registry.assessors()))



# ─────────────────────────────────────────────────────────────────────────────
section("a scorer DECLARES the property it grades — and the Guide routes to it")
# A property entry read "No scored dimension measures this one" while the lanes row
# directly beneath named the module that grades it after the turn: one entry contradicting
# itself, and a module row with nowhere onward to go. The link existed only as a
# `resolve()` call buried in a scorer body — code, not data — so nothing could read it.
import re as _re9, ast as _ast9                                            # noqa: E402
import trusted_data_agent.eval.scorers as _sc9                             # noqa: E402,F401
from trusted_data_agent.eval.scorer import scorers_for as _sf9             # noqa: E402

_by_prop = {}
for _c in ("rag_focused", "tool_enabled", "llm_only", "genie", "conversation_with_tools"):
    for _s in _sf9(_c):
        _p = getattr(_s, "property", None)
        if _p:
            _by_prop.setdefault(_p, set()).add(_s.dimension)

_unknown = sorted(set(_by_prop) - {x.key for x in props.PROPERTIES})
check("every property a scorer declares is a REAL property",
      not _unknown, f"not properties: {_unknown}")
_multi = {k: sorted(v) for k, v in _by_prop.items() if len(v) != 1}
check("…and each declared property resolves to exactly one grading dimension",
      not _multi, f"ambiguous: {_multi}")

# The declaration must be VERIFIABLE against the code that proves it — a free-floating
# attribute drifts the moment a scorer stops consuming its property. Every class whose
# body resolves a property post-hoc must declare that same property, and vice versa,
# EXCEPT where the resolve lives in a shared module-level helper (recorded by name, so a
# new bypass is not silently waved through).
# A declaration without a matching `resolve()` in the same class is allowed only for a
# RECORDED reason — otherwise a scorer could claim a property it does not actually read.
_NOT_REGISTRY_ROUTED = {
    # resolves inside the shared `_authority_finding` helper, not the class body
    "must_not_call": "tool_authority",
    # reads the gate's STAMP first, which the assessor cannot express: under `enforce` the
    # gate rewrites the answer, so re-deriving from delivered text scores a caught
    # over-claim as clean. Routing it would be a regression, not a symmetry gain.
    "honest_absence": "absence_claim",
}
for _f in ("src/trusted_data_agent/eval/scorers/deterministic.py",
           "src/trusted_data_agent/eval/scorers/reliability.py"):
    _src = pathlib.Path(_f).read_text()
    for _node in _ast9.walk(_ast9.parse(_src)):
        if not isinstance(_node, _ast9.ClassDef):
            continue
        _seg = _ast9.get_source_segment(_src, _node) or ""
        _res = set(_re9.findall(r'resolve\("([a-z_]+)", _Lane\.POST_HOC\)', _seg))
        _decl = next((b.value.value for b in _node.body
                      if isinstance(b, _ast9.Assign)
                      and getattr(b.targets[0], "id", "") == "property"), None)
        _dim = next((b.value.value for b in _node.body
                     if isinstance(b, _ast9.Assign)
                     and getattr(b.targets[0], "id", "") == "dimension"), None)
        if _res:
            check(f"{_node.name} declares the property its own body resolves "
                  f"(resolves {sorted(_res)}, declares {_decl!r})", _decl in _res, True)
        elif _decl:
            check(f"{_node.name} declares {_decl!r} without resolving it — "
                  "allowed only for a recorded reason",
                  _NOT_REGISTRY_ROUTED.get(_dim) == _decl,
                  f"dimension {_dim!r} has no recorded reason for bypassing the registry")

# The specific bypass this closed: the governed-data scorer called `scan_obda` directly,
# so a remap of `data_governance` would move the in-flight lane and leave the measurement
# reading the old computation — the exact asymmetry the substrate exists to remove.
_rel = pathlib.Path("src/trusted_data_agent/eval/scorers/reliability.py").read_text()
_gi = _rel.index('dimension = "governed_data_ratio"')
_gov = _rel[_rel.rindex("\nclass ", 0, _gi):]
_gov = _gov[:_gov.index("\nclass ", 1)]
check("the governed-data scorer reads its property through the registry",
      'resolve("data_governance"' in _gov)
check("…and no longer calls the underlying scan directly",
      "scan_obda(" not in _gov)


# ══════════════════════════════════════════════════════════════════════════════
# PROP-003 · Phase 0 — the binding table is PERSISTED and SERVED
#
# The substrate always held bindings as data; nothing could change one without editing
# source. These assertions cover the override layer that makes a remap durable, and the
# three latent gaps found while building it — each of which made a whole class of binding
# unreachable or unenforced.
# ══════════════════════════════════════════════════════════════════════════════
import tempfile as _tf                                                     # noqa: E402
from trusted_data_agent.agent.assurance import store as _store             # noqa: E402

section("7 · PROP-003 · binding overrides are durable")

_tmpdb = _tf.mktemp(suffix="_assurance.db")


def _fresh(db):
    """A registry rebuilt from the shipped defaults + whatever is in `db`."""
    A.reset_for_tests()
    A.bootstrap(db_path=db)


_fresh(_tmpdb)
_default_key = registry.resolve("answer_support", Lane.POST_HOC)
check("the shipped default binds before any override exists",
      _default_key is not None and _default_key.key == "corpus_support")

# A REBIND. Uses a second assessor of the same property so the row is legitimate — the
# point is durability, not conformance (§4 covers that).
class _AltSupport(Assessor):
    key = "alt_support"
    property = "answer_support"
    label = "Alternative support"
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC, C.RETURNS_METRIC})
    requires = frozenset({"answer"})

    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False,
                       severity=Severity.NONE, summary="alt", metrics={"support_ratio": 1.0})


registry.register(_AltSupport())
_store.save_override("answer_support", "POST_HOC", "alt_support", "u1", "", db_path=_tmpdb)

# A FRESH BOOTSTRAP, not a reload — this is the server-restart case, and it is the one
# that matters most: an override that survives only until the process restarts is not
# persistence, it is a session preference. (Mutation-found: an earlier version of this
# section exercised `reload_overrides` alone and passed with `bootstrap` ignoring the
# override layer entirely.)
A.reset_for_tests()
registry.register(_AltSupport())
A.bootstrap(db_path=_tmpdb)
check("a STORED override is applied by bootstrap itself — it survives a restart",
      (registry.resolve("answer_support", Lane.POST_HOC) or None)
      and registry.resolve("answer_support", Lane.POST_HOC).key == "alt_support")

A.reload_overrides(db_path=_tmpdb)
check("an override REBINDS the lane without a process restart",
      (registry.resolve("answer_support", Lane.POST_HOC) or None)
      and registry.resolve("answer_support", Lane.POST_HOC).key == "alt_support")
check("…and the other lane is untouched — a bind names ONE lane",
      registry.resolve("answer_support", Lane.IN_FLIGHT).key == "corpus_support")
check("…and the registered assessors survive the reload",
      len(registry.assessors()) >= 10)

# An EXPLICIT UNBIND is a different act from having no row at all.
_store.save_override("answer_support", "POST_HOC", None, "u1", "", db_path=_tmpdb)
A.reload_overrides(db_path=_tmpdb)
check("a NULL override means NOT ASSESSED, never assessed-clean",
      registry.resolve("answer_support", Lane.POST_HOC) is None)

_store.delete_override("answer_support", "POST_HOC", db_path=_tmpdb)
A.reload_overrides(db_path=_tmpdb)
check("deleting the row RESTORES THE SHIPPED DEFAULT — a distinct act from unbinding",
      (registry.resolve("answer_support", Lane.POST_HOC) or None)
      and registry.resolve("answer_support", Lane.POST_HOC).key == "corpus_support")

# Fail soft and LOUD. Every one of these is a row a live deployment can end up holding.
for _bad, _why in (
    ({"property": "answer_support", "lane": "POST_HOC", "assessor_key": "no_such_module"},
     "an override naming an uninstalled module"),
    ({"property": "answer_support", "lane": "NOT_A_LANE", "assessor_key": "corpus_support"},
     "an override naming an unknown lane"),
    ({"property": "no_such_property", "lane": "POST_HOC", "assessor_key": "corpus_support"},
     "an override naming an unknown property"),
    ({"property": "tool_authority", "lane": "IN_FLIGHT", "assessor_key": "authority_scope"},
     "an override the conformance rules refuse"),
):
    _store.save_override(_bad["property"], _bad["lane"], _bad["assessor_key"],
                         "u1", "", db_path=_tmpdb)
    _raised = None
    try:
        A.reload_overrides(db_path=_tmpdb)
    except Exception as _e:  # noqa: BLE001
        _raised = _e
    check(f"{_why} is SKIPPED, not fatal", _raised is None,
          f"startup must not die for a stale preference row: {_raised}")
    _store.delete_override(_bad["property"], _bad["lane"], db_path=_tmpdb)
A.reload_overrides(db_path=_tmpdb)
check("…and the shipped defaults survived every bad override",
      registry.resolve("answer_support", Lane.POST_HOC).key == "corpus_support"
      and registry.resolve("tool_authority", Lane.POST_HOC).key == "authority_scope")

check("a read against an unreachable DB fails OPEN, so bootstrap still runs",
      _store.load_overrides(db_path="/nonexistent/dir/x.db") == [])

try:
    os.unlink(_tmpdb)
except OSError:
    pass

section("8 · PROP-003 · the three latent gaps")

# (a) `tool_outputs` was in the support set but in NO lane's supply, so an assessor
#     declaring it could never bind ANYWHERE — half the evidence was unreachable.
check("tool_outputs is suppliable post-hoc",
      "tool_outputs" in conformance.LANE_SUPPLIES[Lane.POST_HOC])
check("…and not in flight, where a turn assembles it incrementally",
      "tool_outputs" not in conformance.LANE_SUPPLIES[Lane.IN_FLIGHT])


class _NeedsOutputs(Assessor):
    key = "needs_outputs"
    property = "answer_support"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})
    requires = frozenset({"tool_outputs"})

    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False)


registry.register(_NeedsOutputs())
check("…so an assessor requiring it can be bound post-hoc",
      conformance.check_binding("answer_support", "needs_outputs", Lane.POST_HOC) == [])
check("…and is refused in flight, naming the missing input",
      any("tool_outputs" in p for p in
          conformance.check_binding("answer_support", "needs_outputs", Lane.IN_FLIGHT)))


class _NeedsNonsense(Assessor):
    key = "needs_nonsense"
    property = "answer_support"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})
    requires = frozenset({"weather"})

    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False)


registry.register(_NeedsNonsense())
_nons = conformance.check_binding("answer_support", "needs_nonsense", Lane.POST_HOC)
check("an input NO lane can ever supply is its own refusal, not a lane limitation",
      any("not an assessment input" in p for p in _nons),
      "a typo in `requires` would otherwise read as 'try a different lane'")
check("…and platform_authored is not declarable — the envelope owns it",
      "platform_authored" not in conformance._DECLARABLE)

# (b) capabilities were declared-but-unenforced, so a module with none was silently
#     eligible for the triggered lane alone — by accident rather than by intent.
class _NoCaps(Assessor):
    key = "no_caps"
    property = "answer_support"
    capabilities = frozenset()

    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False)


_capfail = None
try:
    registry.register(_NoCaps())
except ValueError as _e:
    _capfail = str(_e)
check("an assessor declaring NO capabilities is refused at registration",
      _capfail is not None and "capabilit" in _capfail)

# (c) the assessor package is discovered, not listed — a module list was one more place
#     to forget.
from trusted_data_agent.agent.assurance import assessors as _assessors_pkg  # noqa: E402
check("the assessor package is DISCOVERED rather than hand-listed",
      not hasattr(_assessors_pkg, "_MODULES"))
_fresh(_tmpdb)
check("…and discovery finds every shipped assessor",
      len(registry.assessors()) == 11,
      f"found {len(registry.assessors())}")
check("…deterministically ordered, so two deployments agree",
      _assessors_pkg._module_names() == sorted(_assessors_pkg._module_names()))

section("9 · PROP-003 · in-flight consumption is declared honestly")

# A property can be BOUND in flight and consulted by nobody. Rendering the binding as if
# it could move an answer would be the decoration-wearing-a-control's-clothes reading the
# studio exists to prevent — so the consumption map is data, and it is pinned against the
# real call sites.
import inspect as _inspect                                                 # noqa: E402
from trusted_data_agent.agent.grounding import gate as _ggate              # noqa: E402
from trusted_data_agent.agent.honesty import runtime as _hrt               # noqa: E402

from trusted_data_agent.agent.guardrails import runtime as _grt            # noqa: E402
from trusted_data_agent.agent.assurance import inflight as _inflight       # noqa: E402


def _inflight_own(mechanism):
    return _inflight.own_properties_for(mechanism)

_GATE_SRC = {"grounding_gate": _inspect.getsource(_ggate),
             "honesty_gate": _inspect.getsource(_hrt),
             "outbound_guard": _inspect.getsource(_grt),
             "inbound_guard": _inspect.getsource(_grt)}

# Two ways a property reaches a gate, and the map must be honest about BOTH:
#   directly  — the gate resolves it itself, and it IS the gate's assessment
#   the seam  — the gate has its own assessment and `inflight.augment` adds to it
# Either way the property name must appear in the gate's own source; a declared consumer
# nothing consults would put the studio back to claiming a binding can move an answer
# when it cannot.
for _prop, _gate in A.INFLIGHT_CONSUMERS.items():
    _src = _GATE_SRC.get(_gate, "")
    if _prop in A.SEAM_CONSULTED:
        # Consulted THROUGH the seam: the gate names the mechanism, and the derivation
        # supplies the property. Asserting the property NAME appears in the gate's source
        # was only ever true because of the duplication that has just been removed — an
        # implementation-pinning grep that broke on the refactor while the property it
        # stood for held perfectly.
        # Whitespace-tolerant: the grounding gate's call spans lines, and a substring
        # match failed on the line break while the gate plainly did call the seam.
        _reached = (_re3.search(r'augment\(\s*"' + _gate + r'"', _src) is not None
                    and _prop in _inflight_own(_gate))
        _how = "through the seam"
    else:
        # RESOLVED directly by the gate: it really does name the property itself.
        _reached = f'"{_prop}"' in _src or f"'{_prop}'" in _src
        _how = "directly"
    check(f"{_prop} is really consulted by {_gate} ({_how})", _reached,
          f"declared in INFLIGHT_CONSUMERS but {_gate} does not reach it")
check("…and the map is not empty (an empty map would pass the rule vacuously)",
      len(A.INFLIGHT_CONSUMERS) >= 2)
check("a seam-consulted property is marked as such, not as the gate's own assessment",
      A.SEAM_CONSULTED and A.SEAM_CONSULTED <= set(A.INFLIGHT_CONSUMERS),
      f"seam={A.SEAM_CONSULTED}")
check("…and a directly-resolved one is NOT marked as seam-consulted",
      "context_use" not in A.SEAM_CONSULTED and "retrieval_sufficiency" not in A.SEAM_CONSULTED)
check("a property no gate consults at all is honestly absent from the map",
      "data_governance" not in A.INFLIGHT_CONSUMERS
      and "tool_authority" not in A.INFLIGHT_CONSUMERS,
      "a property nothing consults in flight must not claim it is consulted")

section("9 · every module can explain ITSELF — platform and pack alike")

# The dogfooding gap: a pack could carry version/author/guide text while a platform module
# offered only a label, so the studio rendered a richer row for third-party code than for
# the platform's own. Worse, NEITHER could say what it actually does — and in a picker
# offering two modules for one property, the method is the only thing to choose on.
for _a in registry.assessors():
    _d = _a.describe()
    check(f"{_d['key']} describes its METHOD, not just its name",
          bool(_d.get("description")) and len(_d["description"]) > 40,
          f"got {_d.get('description')!r}")
    check(f"…and {_d['key']} says where it came from",
          _d.get("origin") == "platform")
check("a module's description is its METHOD, never the property's question",
      all(registry.get(k).description != (props.get(registry.get(k).property).question)
          for k in ("corpus_support", "kg_scope_diff", "retrieval_stats")),
      "the question is declared once on the Property and is true of EVERY module "
      "serving it; repeating it here would make two modules indistinguishable")
check("origin is DERIVED from the key, never declared beside it",
      registry.get("corpus_support").origin() == "platform"
      and "description" in registry.get("corpus_support").describe())


class _PackShaped(Assessor):
    key = "vendor.mod"
    property = "answer_support"
    label = "Vendor module"
    description = "A third-party method."
    version = "2.1.0"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})

    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False)


registry.register(_PackShaped())
_pd = registry.get("vendor.mod").describe()
check("a pack module fills the SAME slots as a platform one",
      set(_pd) == set(registry.get("corpus_support").describe()),
      "one row shape, so a reader compares like with like")
check("…and its origin names the pack, which is the honest difference",
      _pd["origin"] == "vendor" and _pd["version"] == "2.1.0")
check("…while a builtin carries no version (its version IS the platform's)",
      registry.get("corpus_support").describe()["version"] == "",
      "the same string on every native row is noise, not information")

# The Guide reads the module's method through the same lanes rows both kinds travel on.
_ge = registry.guide_entry("answer_support")
check("the Guide's lane rows carry the module's method and origin",
      all("assessor_desc" in l and "assessor_origin" in l for l in _ge["lanes"]))
check("…filled for a bound module",
      any(l.get("assessor_desc") for l in _ge["lanes"]))


section("9a · PROP-003 · what a gate consults is DERIVED, never listed twice")

# The first version of the seam took a literal at each gate — a second copy of
# `POINT_PROPERTIES`, which would go stale silently: a property added to the registry
# simply would not be consulted in flight, the exact invisible gap the seam exists to
# close. It is derived now, and no call site may pass its own list.
from trusted_data_agent.agent.assurance_points import POINT_PROPERTIES        # noqa: E402

for _m, _expect in (("outbound_guard", ("output_sensitivity",)),
                    ("inbound_guard", ("request_integrity",)),
                    ("grounding_gate", ("answer_support", "answer_scope"))):
    check(f"{_m}'s consulted set is derived from POINT_PROPERTIES",
          _inflight.own_properties_for(_m) == _expect,
          f"got {_inflight.own_properties_for(_m)}")
check("…and a property the gate RESOLVES itself is excluded (it would run twice)",
      "context_use" in POINT_PROPERTIES["grounding_gate"]
      and "context_use" not in _inflight.own_properties_for("grounding_gate"))
check("…while a mechanism with no in-flight wiring derives cleanly rather than raising",
      isinstance(_inflight.own_properties_for("governed_data"), tuple))

_gate_srcs = {
    "guardrails/runtime.py": _inspect.getsource(_grt),
    "grounding/gate.py": _inspect.getsource(_ggate),
}
for _name, _src in _gate_srcs.items():
    # A literal tuple of property names beside the mechanism name is the shape of the
    # duplication. Match the CALL, not a formatting accident.
    _bad = _re3.search(r'augment\(\s*"[a-z_]+"\s*,\s*\(', _src)
    check(f"{_name} passes no property list of its own",
          _bad is None,
          "a literal here is a second copy of POINT_PROPERTIES and will drift from it")


section("9b · PROP-003 · the in-flight seam is UNION-ONLY")

# The invariant that makes a third-party in-flight module safe: it can make a gate act
# where it would not have, and can NEVER make it pass where it would have acted. Enforced
# structurally — `augment` returns additions and has no way to express a removal.
_inflight.clear_routes()
check("a shipped module contributes NOTHING through the seam — the gate already ran it",
      _inflight.augment("outbound_guard", ("output_sensitivity",),
                        AssessmentContext(answer="call me at 555-867-5309")) == [],
      "a builtin assessed twice would double-count one finding")


class _PackDetector(Assessor):
    key = "demo.detector"
    property = "demo.thing"
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC})
    requires = frozenset({"answer"})

    def assess(self, ctx):
        hit = "SECRET-42" in (ctx.answer or "")
        return Finding(property=self.property, assessor=self.key, triggered=hit,
                       severity=Severity.CONCERN if hit else Severity.NONE,
                       summary="found" if hit else "clean",
                       payload={"spans": [["DEMO", 0, 9, "SECRET-42"]], "category": "pii"})


props.register_property(props.Property(
    key="demo.thing", label="Demo", question="q?",
    lanes=frozenset({Lane.IN_FLIGHT, Lane.POST_HOC})))
registry.register(_PackDetector())
registry.bind("demo.thing", "demo.detector", (Lane.IN_FLIGHT,), validate=False)
_inflight.route_property("demo.thing", "outbound_guard")

check("a routed pack property IS consulted by the gate it named",
      "demo.thing" in _inflight.consulted_properties("outbound_guard", ("output_sensitivity",)))
_hit = _inflight.augment("outbound_guard", ("output_sensitivity",),
                         AssessmentContext(answer="the code is SECRET-42"))
check("…and its finding reaches the gate", len(_hit) == 1 and _hit[0].triggered)
check("…while a clean answer contributes nothing",
      _inflight.augment("outbound_guard", ("output_sensitivity",),
                        AssessmentContext(answer="nothing here")) == [])
check("…and it is NOT consulted by a gate it did not name",
      _inflight.augment("inbound_guard", ("request_integrity",),
                        AssessmentContext(answer="the code is SECRET-42")) == [])


class _Exploder(Assessor):
    key = "demo.boom"
    property = "demo.thing"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})
    requires = frozenset({"answer"})

    def assess(self, ctx):
        raise RuntimeError("boom")


registry.register(_Exploder())
registry.bind("demo.thing", "demo.boom", (Lane.IN_FLIGHT,), validate=False)
check("a module whose assess() RAISES contributes nothing (the envelope absorbs it)",
      _inflight.augment("outbound_guard", ("output_sensitivity",),
                        AssessmentContext(answer="the code is SECRET-42")) == [])


class _BrokenEnvelope(Assessor):
    """The failure the seam's OWN guard is for: `run` itself is broken.

    `Assessor.run()` absorbs a raising `assess()`, so a normal bad module never reaches
    the seam's except-block. A module that overrides `run` — or any fault in resolution
    itself — does, and a gate must survive it. (Mutation-found: narrowing that except
    initially changed nothing, because no test drove this path.)
    """

    key = "demo.broken"
    property = "demo.thing"
    capabilities = frozenset({C.PURE, C.REPLAYABLE})
    requires = frozenset({"answer"})

    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False)

    def run(self, ctx):
        raise RuntimeError("the envelope itself is broken")


registry.register(_BrokenEnvelope())
registry.bind("demo.thing", "demo.broken", (Lane.IN_FLIGHT,), validate=False)
_survived = True
try:
    _out = _inflight.augment("outbound_guard", ("output_sensitivity",),
                             AssessmentContext(answer="the code is SECRET-42"))
except Exception:  # noqa: BLE001
    _survived, _out = False, None
check("a module whose ENVELOPE is broken cannot break the gate",
      _survived and _out == [],
      "the seam must fail open to nothing, whatever a module does")

_inflight.clear_routes()
props.unregister_property("demo.thing")
check("clearing a route removes it — an uninstalled pack stops being consulted",
      _inflight.routed_to("outbound_guard") == ())

section("10 · PROP-003 · a pack property joins the live vocabulary")

_before = len(props.keys())
_ok = props.register_property(props.Property(
    key="demo_pack.tone", label="Tone", question="Is the tone appropriate?",
    lanes=frozenset({Lane.POST_HOC})))
check("a namespaced pack property registers", _ok == [])
check("…and appears in the LIVE vocabulary", "demo_pack.tone" in props.keys())
check("…and in the binding table, so it can be assigned",
      any(r["property"] == "demo_pack.tone" for r in registry.bindings()))
check("…and shipped PROPERTIES stays the SHIPPED set",
      "demo_pack.tone" not in {p.key for p in props.PROPERTIES})
check("an un-namespaced pack property is refused",
      props.register_property(props.Property(
          key="tone", label="T", question="q?", lanes=frozenset({Lane.POST_HOC}))) != [])
check("a colliding key is refused rather than overwriting a shipped property",
      props.register_property(props.Property(
          key="demo_pack.tone", label="T", question="q?",
          lanes=frozenset({Lane.POST_HOC}))) != [])
check("a property that both declares and explains-away a lane is refused",
      props.register_property(props.Property(
          key="demo_pack.bad", label="B", question="q?",
          lanes=frozenset({Lane.POST_HOC}),
          lane_notes={"post_hoc": "absent"})) != [])
check("unregistering removes a pack property", props.unregister_property("demo_pack.tone"))
check("…but never a shipped one", not props.unregister_property("answer_support"))
check("…and the vocabulary is back to its shipped size", len(props.keys()) == _before)

try:
    os.unlink(_tmpdb)
except OSError:
    pass

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)