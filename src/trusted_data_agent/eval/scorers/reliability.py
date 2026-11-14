"""Reliability & output-integrity scorers — deterministic signals beyond the
core suite, derived entirely from the trace we already persist.

Two families:
  - **Reliability** (was the *process* sound?): latency, tool success rate, loop/
    stall detection, error recovery, clean termination, context-budget health.
  - **Quality** (was the *output* sound?): output sanity/leakage, numeric grounding,
    retrieval relevance, plan-execution coverage, expert success.

Threshold-bearing scorers abstain (passed=None) without a threshold, reporting the
measurement; with one (case assertion OR the admin global default merged in by the
online/manual hook) they pass/fail. CI-pure, no LLM.
"""
from __future__ import annotations

import re
import json
from collections import Counter

from trusted_data_agent.eval.types import Verdict, ScoreKind, EvalCase, RunRecord
from trusted_data_agent.eval.scorer import Scorer, register_scorer, banded_metric
# Scope/number primitives are shared with the live grounding gate
# (agent/grounding/primitives.py) so the post-hoc scorer and the on-path gate can
# never disagree about what "out of scope" means. See TIER_1.1_GROUNDING_GATE.md.
from trusted_data_agent.agent.grounding.primitives import scope_diff, norm_nums, answer_nums as _answer_nums


def _cuts(case: EvalCase, key: str):
    """Return (gate, healthy) cut points for a metric: the Danger boundary lives
    at ``key`` (back-compat single-line gate) and the Healthy boundary at
    ``key__healthy``. Either may be None."""
    return case.assertions.get(key), case.assertions.get(key + "__healthy")

_TOOL_CLASSES = frozenset({"tool_enabled", "conversation_with_tools"})
_DATA_CLASSES = frozenset({"tool_enabled", "rag_focused"})
_OK = ("success", "ok", "complete", "completed")


def _na(s: Scorer, why: str) -> Verdict:
    return Verdict(dimension=s.dimension, kind=s.kind, passed=None, explanation=why)


def _metric(s, passed, score, threshold=None, explanation="", evidence=None):
    return Verdict(dimension=s.dimension, kind=s.kind, passed=passed, score=score,
                   threshold=threshold, explanation=explanation, evidence=evidence or [])


def _genie_resource_split(run, kind: str) -> list:
    """Delegated-cost annotation for a genie coordinator turn's cost/token chip.

    The scored metric is the coordinator's OWN (self) cost; this annotation shows
    what the turn delegated to experts and the true inclusive turn total — display
    only, never summed (the expert turns carry that cost on their own spans).
    ``kind`` is 'cost' or 'tokens'. Empty for non-genie / no experts."""
    roll = getattr(run, "genie_rollup", {}) or {}
    n = roll.get("expert_count") or 0
    if not n:
        return []
    s = '' if n == 1 else 's'
    if kind == "cost":
        return [f"+ {n} expert{s} ${float(roll.get('expert_cost_usd', 0)):.4f}",
                f"turn total ${float(roll.get('cost_usd', 0)):.4f}"]
    expert_tok = int(roll.get('expert_input_tokens', 0)) + int(roll.get('expert_output_tokens', 0))
    total_tok = int(roll.get('input_tokens', 0)) + int(roll.get('output_tokens', 0))
    return [f"+ {n} expert{s} {expert_tok:,}", f"turn total {total_tok:,}"]


# ── cross-class reliability ────────────────────────────────────────────────────
class LatencyScorer(Scorer):
    dimension = "latency"
    kind = ScoreKind.METRIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        ms = run.duration_ms
        if ms <= 0:
            return _na(self, "no duration recorded")
        gate, healthy = _cuts(case, "max_latency_ms")
        if gate is None:
            return _metric(self, None, float(ms), explanation=f"{ms:,} ms (no limit set)")
        return banded_metric(self, value=float(ms), gate=int(gate),
                             healthy=int(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=f"{ms:,} ms")


class TokenUsageScorer(Scorer):
    """Total tokens for the turn — a universal core KPI (every class), banded by
    ``max_tokens``. Cross-cutting like cost/latency; no class restriction."""
    dimension = "tokens"
    kind = ScoreKind.METRIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        n = run.total_tokens
        if n <= 0:
            return _na(self, "no token count recorded")
        ev = _genie_resource_split(run, "tokens")
        gate, healthy = _cuts(case, "max_tokens")
        if gate is None:
            return _metric(self, None, float(n), explanation=f"{n:,} tokens", evidence=ev)
        return banded_metric(self, value=float(n), gate=int(gate),
                             healthy=int(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=f"{n:,} tokens", evidence=ev)


class CacheEfficiencyScorer(Scorer):
    """The Attested Cache (S2): the share of the turn's prompt tokens the provider served
    from its cache — a cost fact over the whole run, INFORMATIONAL (non-gating, no admin
    threshold; the number exists so an agent whose prefix keeps invalidating shows it).
    Abstains — never a pass — when the turn carries no cache reading (caching not armed,
    an older turn), when the provider has no prompt cache, or when nothing was sent."""
    dimension = "cache_efficiency"
    kind = ScoreKind.METRIC
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        ev = run.raw.get("cache") if isinstance(run.raw, dict) else None
        if not isinstance(ev, dict):
            return _na(self, "no prompt-cache reading on this turn")
        if str(ev.get("capability") or "none") == "none":
            return _na(self, "this provider has no prompt cache")
        total = int(ev.get("input_tokens") or 0)
        if total <= 0:
            return _na(self, "no prompt tokens recorded")
        cached = int(ev.get("cached_tokens") or 0)
        written = int(ev.get("written_tokens") or 0)
        share = cached / total
        why = f"{cached:,} of {total:,} prompt tokens served from cache ({share:.0%})"
        if written:
            why += f"; {written:,} written to it"
        if int(ev.get("misses_found") or 0):
            why += f"; {int(ev['misses_found'])} miss(es) on an unchanged prefix"
        return _metric(self, None, round(share, 4), explanation=why,
                       evidence=[f"calls: {int(ev.get('calls') or 0)}", f"hits: {int(ev.get('hits') or 0)}",
                                 f"capability: {ev.get('capability')}"])


class BudgetAdherenceScorer(Scorer):
    """The Visible Budget (S5): did the turn stay inside the budget the agent declared —
    and when it could not, did it finish GRACEFULLY (the model told, the answer says what is
    left) rather than being KILLED by a hard stop. INFORMATIONAL (non-gating, no admin
    threshold): 1.0 within budget · 0.5 exhausted but graceful · 0.0 killed. Abstains — never
    a pass — when no budget was armed on the turn (the switch off, no `turnBudget` declared,
    an older turn). Owned by the `turn_budget` mechanism at BOUNDED."""
    dimension = "budget_adherence"
    kind = ScoreKind.METRIC
    gating = False
    applies_to = frozenset({"tool_enabled", "conversation_with_tools", "genie"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        ev = run.raw.get("budget") if isinstance(run.raw, dict) else None
        if not isinstance(ev, dict) or not ev.get("units"):
            return _na(self, "no turn budget armed on this turn")
        units = ev.get("units") or []
        evidence = [f"{u.get('unit')}: {u.get('used')} of {u.get('total')}" for u in units if isinstance(u, dict)]
        if ev.get("killed"):
            return _metric(self, None, 0.0,
                           explanation=f"budget exhausted ({ev.get('exhausted')}) and the turn was KILLED by a "
                                       f"hard stop ({ev.get('killed')}) — the answer did not say what is left",
                           evidence=evidence)
        if ev.get("exhausted"):
            return _metric(self, None, 0.5,
                           explanation=f"budget exhausted ({ev.get('exhausted')}) — finished gracefully: "
                                       f"{int(ev.get('refusals') or 0)} further tool call(s) refused, the answer "
                                       f"says what is left",
                           evidence=evidence)
        return _metric(self, None, 1.0, explanation="finished within the declared turn budget", evidence=evidence)


class CleanTerminationScorer(Scorer):
    """The turn reached a terminal state (no truncation / abandoned run). Uses the
    provenance terminal step when present (Optimize); otherwise (ReAct / Ideate /
    Focus, which have no signed chain) derives it from 'produced an answer and did
    not error' — so the KPI applies to every class, not just Optimize."""
    dimension = "clean_termination"
    kind = ScoreKind.DETERMINISTIC
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # A turn the record calls failed did not terminate cleanly, whatever its trace says: the
        # ReAct engine adds its `turn_complete` step on the failure path too, so a provider that
        # never answered graded "clean termination" (trust campaign C18).
        from trusted_data_agent.core import turn_states as _ts
        if _ts.is_failure(run.status) or run.error_message:
            why = (run.error_message or "").strip()
            return self._verdict(False, 0.0, "the turn failed — " + (why[:160] if why else run.status))
        if _ts.is_interrupted(run.status):
            # PROP-011: a turn that stopped to ask ended cleanly — the question is its answer
            return self._verdict(True, 1.0, "clean stop — the turn asked the user for input")
        steps = run.step_types()
        if steps:
            ok = any(s in ("turn_complete", "execution_complete") for s in steps)
            return self._verdict(ok, 1.0 if ok else 0.0,
                                 "clean termination" if ok else "no terminal step (possible truncation)")
        # No provenance chain — derive from the answer + status.
        answered = bool((run.final_answer_text or "").strip())
        errored = not _ts.is_success(run.status) or bool(run.error_message)
        if not answered and not errored:
            return _na(self, "no terminal signal")
        ok = answered and not errored
        return self._verdict(ok, 1.0 if ok else 0.0,
                             "terminal answer produced" if ok else "no terminal answer (truncated / errored)")


class ContextHealthScorer(Scorer):
    """Data-loss-under-pressure signal: condensation / RAG-offload / distillation
    events mean recent context or tool detail was dropped to fit the budget."""
    dimension = "context_health"
    kind = ScoreKind.METRIC
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        if not run.context_snapshot:
            return _na(self, "no context snapshot")
        cond = run.context_condensations()
        dist = run.context_distillations()
        dropped = cond + dist
        return _metric(self, None, float(dropped),
                       explanation=(f"{cond} condensation(s), {dist} distillation(s) — context trimmed"
                                    if dropped else "no context trimming"))


# ── tool-class reliability ─────────────────────────────────────────────────────
class ToolSuccessRateScorer(Scorer):
    dimension = "tool_success_rate"
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        calls = run.tool_calls()
        if not calls:
            return _na(self, "no tool calls")
        ok_n = sum(1 for c in calls if str(c.get("status", "success")).lower() in _OK)
        rate = round(ok_n / len(calls), 4)
        gate, healthy = _cuts(case, "min_tool_success_rate")
        if gate is None:
            return _metric(self, None, rate, explanation=f"{ok_n}/{len(calls)} tools succeeded ({rate:.0%})")
        return banded_metric(self, value=rate, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True,
                             explanation=f"{ok_n}/{len(calls)} succeeded ({rate:.0%})")


class ToolLoopScorer(Scorer):
    """Stall detection: the same tool called with identical arguments repeatedly."""
    dimension = "no_tool_loops"
    property = "tool_repetition"   # the assurance property this grades
    kind = ScoreKind.DETERMINISTIC
    applies_to = _TOOL_CLASSES

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # The `tool_repetition` property from the substrate — the SAME definition of "the
        # same call" the in-flight loop guard uses. Two definitions would let the guard
        # break a run on a repeat this card does not report.
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        try:
            _A.bootstrap()
            _a = _reg.resolve("tool_repetition", _Lane.POST_HOC)
        except Exception:  # noqa: BLE001
            _a = None
        if _a is not None:
            _f = _a.run(_rc.from_run(run))
            if _f.abstained:
                return _na(self, _f.abstained)
            _max = int(_f.metrics.get("max_repeats", 0))
            gate, healthy = _cuts(case, "max_tool_repeats")
            if gate is None:
                return _metric(self, None, float(_max), explanation=_f.summary)
            return banded_metric(self, value=float(_max), gate=int(gate),
                                 healthy=int(healthy) if healthy is not None else None,
                                 higher_is_better=False, explanation=_f.summary,
                                 evidence=list(_f.evidence))

        calls = run.tool_calls()
        if not calls:
            return _na(self, "no tool calls")
        sigs = Counter((c["tool_name"], json.dumps(c.get("arguments") or {}, sort_keys=True, default=str))
                       for c in calls)
        max_rep = max(sigs.values())
        gate, healthy = _cuts(case, "max_tool_repeats")
        if gate is None:
            return _metric(self, None, float(max_rep),
                           explanation=f"max identical call repeated {max_rep}×")
        worst = [f"{name} ×{cnt}" for (name, _), cnt in sigs.items() if cnt > int(gate)]
        v = banded_metric(self, value=float(max_rep), gate=int(gate),
                          healthy=int(healthy) if healthy is not None else None,
                          higher_is_better=False,
                          explanation=("no loops" if max_rep <= int(gate)
                                       else f"repeated calls (>{int(gate)}): {worst}"),
                          evidence=worst)
        return v


class ScopeAdherenceScorer(Scorer):
    """Did the agent stay within the data scope the KG enrichment declared?

    The KG enrichment names the model's DEPLOYED database(s) (``scope_databases``).
    This flags data-source calls that reach OUTSIDE that scope — unrelated databases
    the agent went hunting through (system catalogs like DBC are always allowed).
    A supportive, non-gating signal: it *observes* scope drift, it does not constrain
    the agent. Abstains when the graph declared no deployable scope (logical-only /
    no enrichment) — there is then nothing to adhere to."""
    dimension = "scope_adherence"
    property = "answer_scope"   # the assurance property this grades
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # The `answer_scope` property from the substrate: the gate's OWN out-of-scope
        # finding where it recorded one, recomputed with the gate's own primitive where it
        # did not (symmetry map A6). Both sides shared `scope_diff` already but assembled
        # it independently — two invocations of one rule that could drift apart.
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        try:
            _A.bootstrap()
            _a = _reg.resolve("answer_scope", _Lane.POST_HOC)
        except Exception as _exc:  # noqa: BLE001
            _a = None
        if _a is not None:
            _f = _a.run(_rc.from_run(run))
            if _f.abstained:
                return _na(self, _f.abstained)
            _n = int(_f.metrics.get("out_of_scope", 0))
            gate, healthy = _cuts(case, "max_out_of_scope_dbs")
            if gate is None:
                return _metric(self, None, float(_n), explanation=_f.summary)
            return banded_metric(self, value=float(_n), gate=float(gate),
                                 healthy=float(healthy) if healthy is not None else None,
                                 higher_is_better=False, explanation=_f.summary,
                                 evidence=list(_f.evidence))

        scope = run.kg_scope_databases()
        if not scope:
            return _na(self, "no KG-declared deployable scope")
        calls = run.tool_calls()
        if not calls:
            return _na(self, "no tool calls")
        out_of_scope = scope_diff(scope, calls)
        n = len(out_of_scope)
        ev = sorted(out_of_scope)
        scope_txt = ", ".join(sorted(scope))
        gate, healthy = _cuts(case, "max_out_of_scope_dbs")
        if gate is None:
            return _metric(self, None, float(n),
                           explanation=(f"all data queries in scope ({scope_txt})" if n == 0
                                        else f"{n} out-of-scope database(s): {ev} (scope: {scope_txt})"),
                           evidence=ev)
        return banded_metric(self, value=float(n), gate=int(gate),
                             healthy=int(healthy) if healthy is not None else None,
                             higher_is_better=False,
                             explanation=(f"all data queries in scope ({scope_txt})" if n == 0
                                          else f"{n} out-of-scope DB(s) {ev} — expected {scope_txt}"),
                             evidence=ev)


class KgSeedUtilizationScorer(Scorer):
    """Did the answer USE what the knowledge graph reached only by MEANING?

    The KG enrichment has two recall channels: lexical (the words the question used)
    and semantic (what it meant). The event records which entities the semantic
    channel BRIDGED — found by meaning, absent from the lexical pool. This measures
    the fraction of those the turn's data tool calls then referenced (the ONE rule
    in `kgstore.usage.touched_entities`, shared with the `used` telemetry so the
    scorer and the ranking prior cannot disagree about "used").

    Why it exists: the channel was dead on production for 18 days (Sep 2026) and
    nothing measured it — the bench disables it for determinism, and no post-turn
    reading aggregated `bridged`. A number that is 0 for a week is a finding; a
    channel with no number is invisible.

    Informational (non-gating, no admin threshold): abstains — never a pass — when
    the channel was off/errored/warming, when nothing was bridged (semantics agreed
    with the words), or when the turn ran no data tool calls."""
    dimension = "kg_seed_utilization"
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        reports = run.kg_seeding_reports()
        if not reports:
            return _na(self, "no semantic seeding report on this turn")
        states = {str(r.get("state") or "") for r in reports}
        if "ready" not in states:
            return _na(self, f"semantic seeding did not contribute (state: {', '.join(sorted(states)) or 'unknown'})")
        bridged = run.kg_bridged_entities()
        if not bridged:
            return _na(self, "nothing was reached only by meaning on this turn")
        calls = run.tool_calls()
        if not calls:
            return _na(self, "no tool calls")
        from trusted_data_agent.kgstore.usage import touched_entities
        touched = touched_entities(bridged, calls)
        frac = len(touched) / len(bridged)
        untouched = [n for n in bridged if n not in touched]
        expl = (f"{len(touched)} of {len(bridged)} entities reached by meaning were used by the "
                f"turn's data calls" + (f" — unused: {untouched[:4]}" if untouched else ""))
        return _metric(self, None, round(frac, 3), explanation=expl,
                       evidence=[f"used: {n}" for n in touched] + [f"unused: {n}" for n in untouched])


class EnrichmentRecallScorer(Scorer):
    """Did the knowledge graph SHOW the tables the answer needed?

    The inverse of `kg_seed_utilization`. That one asks "of what we showed, how much was
    used"; this asks "of what was USED, how much had we shown" — and it is the number
    that would have flagged prod session 0aa3a650 on day one: turn 2 read `boarding_event`
    and `accounts_receivable`, neither of which the enrichment had rendered, because the
    seeding was blind to the conversation. A table the agent had to find on its own is a
    retrieval defect, whatever the answer turned out to be.

    ONE definition of a miss — `kgstore.usage.unshown_tables` (leaf-level: the enrichment
    names logical `loyalty.ffp_member`, the SQL names deployed `airlines_core.ffp_member`)
    — shared with the `missed` telemetry recorder, so the score and the prior that learns
    from it cannot disagree. Score = shown/read over the tables the turn's data calls read.

    Informational (non-gating, no admin threshold): abstains — never a pass — when no
    enrichment fired (nothing to attribute the miss to) or the turn read no tables."""
    dimension = "enrichment_recall"
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        shown = run.kg_entity_names()
        if not shown:
            return _na(self, "no knowledge-graph enrichment on this turn")
        calls = run.tool_calls()
        if not calls:
            return _na(self, "no tool calls")
        from trusted_data_agent.kgstore.usage import tables_read, unshown_tables
        read = tables_read(calls)
        if not read:
            return _na(self, "the turn's tool calls read no tables")
        missed = unshown_tables(shown, calls, run.kg_deployed_names())
        frac = (len(read) - len(missed)) / len(read)
        expl = (f"{len(read) - len(missed)} of {len(read)} tables the turn read were in the enrichment"
                + (f" — found by the agent alone: {missed[:4]}" if missed else ""))
        return _metric(self, None, round(frac, 3), explanation=expl,
                       evidence=[f"missed: {m}" for m in missed]
                       + [f"shown: {db + '.' if db else ''}{leaf}" for db, leaf in read
                          if f"{db + '.' if db else ''}{leaf}" not in missed])


class ToolEconomyScorer(Scorer):
    """Total tool calls for the turn — a work-volume signal that catches thrash
    (e.g. a brute-force catalog hunt + a retry storm) that ``no_tool_loops`` misses
    because the calls aren't byte-identical. Non-gating: flags bloat, never blocks."""
    dimension = "tool_calls"
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        calls = run.tool_calls()
        if not calls:
            return _na(self, "no tool calls")
        n = len(calls)
        gate, healthy = _cuts(case, "max_tool_calls")
        if gate is None:
            return _metric(self, None, float(n), explanation=f"{n} tool calls")
        return banded_metric(self, value=float(n), gate=int(gate),
                             healthy=int(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=f"{n} tool calls")


class SurfaceViolationScorer(Scorer):
    """OBDA-READ Phase C (scope O3) — denied raw-SQL attempts by a surface-only agent.

    INFO-tier and non-gating BY DESIGN: after the first denial the agent self-corrects
    to `query_product`, so the expected steady state is zero — and a NONZERO steady
    state is not a misbehaving agent but a PRODUCT GAP: the consumer needed something
    the surface does not offer. That is producer backlog, made measurable. Abstains
    when no denial occurred (nothing to report; a decorative zero teaches nothing)."""
    dimension = "surface_violation"
    # Deliberately NO `property` declaration. `tool_authority` is graded by
    # `must_not_call` (registry-routed via the shared `_authority_finding` helper, with a
    # recorded bypass reason in test_assurance_substrate's _NOT_REGISTRY_ROUTED); the
    # substrate contract allows exactly ONE grading dimension per property, because the
    # Guide routes a property to THE measurement that grades it. This scorer is an
    # INFO-tier diagnostic that counts the data surface's own denial stamps — it neither
    # resolves the property through the registry nor grades it, so declaring the property
    # made the Guide's routing ambiguous AND claimed a registry read that never happens
    # (both caught by test_assurance_substrate; red since Phase C shipped).
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        denials = 0
        for c in run.tool_calls():
            res = c.get("result") if isinstance(c, dict) else None
            meta = (res or {}).get("metadata") if isinstance(res, dict) else None
            if (isinstance(meta, dict) and meta.get("agent_denied")
                    and "surface-only" in str(meta.get("policy_reason") or "")):
                denials += 1
        if not denials:
            return _na(self, "no data-surface denials this turn")
        return _metric(self, None, float(denials),
                       explanation=(f"{denials} raw-SQL attempt(s) denied by the data "
                                    f"surface — expected 0 after the first correction; "
                                    f"a steady nonzero is a product-gap signal"))


class ErrorRecoveryScorer(Scorer):
    """When errors occurred, did the turn still complete? (resilience, informational)"""
    dimension = "error_recovery"
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        errs = run.self_correction_count
        if errs == 0:
            return _na(self, "no errors to recover from")
        recovered = run.status == "success" and run.is_valid and not run.error_message
        return _metric(self, None, 1.0 if recovered else 0.0,
                       explanation=f"{errs} error(s); turn {'recovered' if recovered else 'did NOT recover'}")


# ── output integrity (cross-class) ─────────────────────────────────────────────
# The leak patterns live in `guardrails/primitives` beside the other detectors of
# delivered text (symmetry map A12): one pattern set, two lanes, no local copy.

class OutputSanityScorer(Scorer):
    """The answer contains no leaked internals (tracebacks, raw error/tool JSON,
    encrypted-content placeholders, python reprs)."""
    dimension = "output_sanity"
    kind = ScoreKind.DETERMINISTIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        txt = run.final_answer_text or ""
        if not txt.strip():
            return _na(self, "no answer to inspect")
        from trusted_data_agent.agent.guardrails.primitives import (
            detect_leaked_internals)
        hits = detect_leaked_internals(txt)
        ok = not hits
        return self._verdict(ok, 1.0 if ok else 0.0,
                             "answer clean of leaked internals" if ok else "leaked: " + ", ".join(hits),
                             evidence=hits)


class NumericFidelityScorer(Scorer):
    """Anti-hallucination pre-signal: numbers stated in the answer should appear in
    the tool results / retrieved data. Heuristic, non-gating without a threshold."""
    dimension = "numeric_fidelity"
    kind = ScoreKind.DETERMINISTIC
    applies_to = _DATA_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # A gate that acted replaced the answer with the platform's own text, and the
        # withholding notice QUOTES the numbers it is reporting as unsupported — so
        # this scores 0.125 on a turn where the model wrote nothing of the kind. ONE
        # definition, shared with every scorer that grades the delivered text.
        _why = run.platform_authored_answer()
        if _why:
            return _na(self, _why)
        answer_nums = _answer_nums(run.final_answer_text)
        if not answer_nums:
            return _na(self, "no numbers in answer")
        _lost = run.attachment_evidence_lost_reason()
        if _lost:
            return _na(self, _lost)
        data_nums = norm_nums(run.grounding_text())
        if data_nums:
            data_nums = data_nums | run.vouched_numbers()   # the turn's own inputs (RunRecord.vouched_numbers)
        # S3 — a figure a compute receipt vouches for IS grounded: the gate moves from
        # flagging arithmetic to verifying it. Receipted figures join the data set through
        # the same precision-tolerant match the lineage marks use (one definition).
        receipts = run.compute_receipts()
        receipted = set()
        if receipts:
            try:
                from trusted_data_agent.agent import compute as _cmp
                from trusted_data_agent.agent.compute_lineage import matches as _matches
                figs = [f for r in receipts for f in _cmp.receipt_figures(r)]
                receipted = {n for n in answer_nums if any(_matches(n, f) for f in figs)}
            except Exception:  # noqa: BLE001
                receipted = set()
        # …and a figure the lineage QUOTED from a result row is grounded too: the lineage
        # saw the statement's full rows, where this scorer sees a truncated preview (a
        # 100-row table quoted verbatim read 47% grounded here while every figure traced).
        lineage = run.compute_lineage() or {}
        # …and a figure the agent WROTE INTO an executed statement (a filter literal) or a
        # code the graph DECLARES for a coded column is traceable, not invented — the
        # lineage's `literals` / `declared` classes (Sep 2026: "offence code '120'").
        # …and a figure DERIVED from a result column (its sum or average) is arithmetic the
        # data vouches for — the lineage recomputed it from the complete rows.
        for q in ((lineage.get("quoted") or []) + (lineage.get("literals") or []) + (lineage.get("declared") or [])
                  + (lineage.get("derived") or [])):
            f = str((q or {}).get("figure") or "").replace(",", "")
            if f in answer_nums:
                receipted.add(f)
            if "." in f and f.split(".")[0] in answer_nums:
                receipted.add(f.split(".")[0])
        if not data_nums and not receipted:
            return _na(self, "no data to ground numbers against")
        grounded = sum(1 for n in answer_nums if n in data_nums or n in receipted)
        ratio = round(grounded / len(answer_nums), 4)
        gate, healthy = _cuts(case, "min_numeric_fidelity")
        via = f" ({len(receipted)} by lineage — a compute receipt or a quoted row)" if receipted else ""
        if gate is None:
            return _metric(self, None, ratio,
                           explanation=f"{grounded}/{len(answer_nums)} answer numbers grounded in data{via}")
        ungrounded = [n for n in answer_nums if n not in data_nums and n not in receipted]
        return banded_metric(self, value=ratio, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True,
                             explanation=f"{grounded}/{len(answer_nums)} numbers grounded ({ratio:.0%}){via}",
                             evidence=ungrounded[:6])


# ── per-class deep signals ─────────────────────────────────────────────────────
class PlanExecutionCoverageScorer(Scorer):
    """Optimize: were the planned tools actually executed? (soft, informational)"""
    dimension = "plan_execution"
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"tool_enabled"})
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        planned = set(run.planned_tools())
        if not planned:
            return _na(self, "no strategic plan")
        called = set(run.tool_names())
        covered = [t for t in planned if t in called]
        ratio = round(len(covered) / len(planned), 4)
        missing = [t for t in planned if t not in called]
        return self._verdict(None, score=ratio,
                             explanation=f"{len(covered)}/{len(planned)} planned tools executed",
                             evidence=missing)


class RetrievalRelevanceScorer(Scorer):
    """Focus: mean similarity of the retrieved chunks (were they actually relevant)."""
    dimension = "retrieval_relevance"
    property = "retrieval_sufficiency"   # the assurance property this grades
    kind = ScoreKind.METRIC
    applies_to = frozenset({"rag_focused"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # The `retrieval_sufficiency` property from the substrate. ONE assessment returns
        # BOTH statistics (symmetry map A7): the mechanism's question is "was the BEST
        # chunk good enough to answer?" and this one's is "was the retrieval SET good?".
        # Both are legitimate and they diverge legitimately — one excellent chunk among
        # nine poor ones. Reading them from one finding means the two lanes can disagree
        # about the ANSWER while never disagreeing about the DATA.
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        try:
            _A.bootstrap()
            _a = _reg.resolve("retrieval_sufficiency", _Lane.POST_HOC)
        except Exception:  # noqa: BLE001
            _a = None
        if _a is not None:
            _f = _a.run(_rc.from_run(run))
            if _f.abstained:
                return _na(self, _f.abstained)
            mean = float(_f.metrics.get("mean_score", 0.0))
            top = float(_f.metrics.get("top_score", 0.0))
        else:
            scores = run.retrieval_scores()
            if not scores:
                return _na(self, "no retrieval similarity scores")
            mean = round(sum(scores) / len(scores), 4)
            top = round(max(scores), 4)
        gate, healthy = _cuts(case, "min_retrieval_relevance")
        if gate is None:
            return _metric(self, None, mean, explanation=f"mean similarity {mean:.2f} (top {top:.2f})")
        return banded_metric(self, value=mean, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True,
                             explanation=f"mean similarity {mean:.2f} (top {top:.2f})")


class ContextUtilizationScorer(Scorer):
    """RAG Trust Hardening Wave 4 (P2): did the answer actually USE the retrieved
    chunks — or ignore them and answer from weights (the KG-enrichment failure mode,
    RAG edition)? Deterministic: the fraction of the answer's distinct content words
    (≥5 chars) that appear in the retrieved chunk text. Advisory — banded only when
    the admin sets ``min_context_utilization``. Abstains when nothing was retrieved."""
    dimension = "context_utilization"
    property = "context_use"   # the assurance property this grades
    kind = ScoreKind.METRIC
    # The docstring called this Advisory but the flag was never set, so the scorer
    # disagreed with itself and a low-overlap answer failed a whole turn (#43).
    gating = False
    applies_to = frozenset({"rag_focused", "llm_only", "tool_enabled",
                            "conversation_with_tools"})

    @staticmethod
    def _content_words(text: str) -> set:
        import re as _re
        return {w.lower() for w in _re.findall(r"[A-Za-z][A-Za-z_\-]{4,}", text or "")}

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        docs = run.retrieved_documents()
        if not docs:
            return _na(self, "no retrieved knowledge this turn")
        # The platform's withholding notice is ~30 words and cites none of the corpus,
        # so a blocked turn scored 3% — a damning-looking number about text the model
        # never wrote. ONE definition, shared with the other delivered-text scorers.
        _why = run.platform_authored_answer()
        if _why:
            return _na(self, _why)
        # The `context_use` property from the substrate — the SAME assessment the
        # grounding gate escalates on (symmetry map A8). Two implementations of one
        # overlap would let the gate escalate on a number this card disagrees with.
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        try:
            _A.bootstrap()
            _a = _reg.resolve("context_use", _Lane.POST_HOC)
        except Exception:  # noqa: BLE001
            _a = None
        if _a is not None:
            _f = _a.run(_rc.from_run(run))
            if _f.abstained:
                return _na(self, _f.abstained)
            ratio = float(_f.metrics.get("context_use_ratio", 0.0))
            gate, healthy = _cuts(case, "min_context_utilization")
            expl = _f.summary
            if gate is None:
                return _metric(self, None, ratio, explanation=expl)
            return banded_metric(self, value=ratio, gate=float(gate),
                                 healthy=float(healthy) if healthy is not None else None,
                                 higher_is_better=True, explanation=expl)

        answer = run.final_answer_text or ""
        answer_words = self._content_words(answer)
        if not answer_words:
            return _na(self, "no scoreable answer content")
        corpus = " ".join(
            str((d.get("content") or d.get("text") or "")) if isinstance(d, dict) else str(d)
            for d in docs)
        corpus_words = self._content_words(corpus)
        used = len(answer_words & corpus_words)
        ratio = round(used / len(answer_words), 4)
        expl = (f"{used}/{len(answer_words)} of the answer's content words appear in the "
                f"retrieved chunks ({ratio:.0%})")
        gate, healthy = _cuts(case, "min_context_utilization")
        if gate is None:
            return _metric(self, None, ratio, explanation=expl)
        return banded_metric(self, value=ratio, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True, explanation=expl)


class KnowledgeFreshnessScorer(Scorer):
    """RAG Trust Hardening Wave 4 (P2): age of the stalest document the turn's
    retrieval actually drew on (CDC ``verified_days_ago`` stamped by the provenance
    enrichment). Informational unless ``max_knowledge_age_days`` is set. Abstains
    honestly when no CDC metadata exists (sync off / legacy docs)."""
    dimension = "knowledge_freshness"
    kind = ScoreKind.METRIC
    applies_to = frozenset({"rag_focused", "llm_only", "tool_enabled",
                            "conversation_with_tools"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        docs = run.retrieved_documents()
        ages = [d.get("verified_days_ago") for d in docs
                if isinstance(d, dict) and d.get("verified_days_ago") is not None]
        if not ages:
            return _na(self, "no CDC freshness metadata on the retrieved documents")
        stalest = max(int(a) for a in ages)
        sync_off = sum(1 for d in docs if isinstance(d, dict) and d.get("sync_off"))
        expl = (f"stalest cited document verified {stalest} day(s) ago"
                + (f" · {sync_off} doc(s) have sync off" if sync_off else ""))
        gate, healthy = _cuts(case, "max_knowledge_age_days")
        if gate is None:
            return _metric(self, None, float(stalest), explanation=expl)
        return banded_metric(self, value=float(stalest), gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=expl)


class ExpertSuccessScorer(Scorer):
    """Coordinate: every consulted expert returned successfully."""
    dimension = "expert_success"
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"genie"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        slaves = run.slaves_invoked()
        if not slaves:
            return _na(self, "no experts invoked")
        failed = [s for s in slaves if isinstance(s, dict)
                  and str(s.get("status", "success")).lower() not in _OK]
        ok = not failed
        ev = [str(s.get("profile_tag") or s.get("profile_id") or s) for s in failed]
        return self._verdict(ok, 1.0 if ok else round(1 - len(failed) / len(slaves), 4),
                             "all experts succeeded" if ok else f"{len(failed)} expert(s) failed",
                             evidence=ev)


class GovernedDataRatioScorer(Scorer):
    """OBDA-READ — of a turn's data operations, what fraction went through the GOVERNED, deterministic
    lane (a metric compiled to ratio-safe SQL, zero judgement) vs FREE-FORMED raw SQL the agent
    wrote itself. Higher = more of the turn's data access came from governed data products.

    A non-gating **incentive** signal: it rewards the governed lane, never blocks. Abstains when
    the turn did no data operations (nothing to attribute). Reads the same trace the OBDA-READ Live
    Status card is built from — observability and eval as two ends of one signal.
    See docs/roadmap/OBDA_VISION.md §5."""
    dimension = "governed_data_ratio"
    property = "data_governance"   # the assurance property this grades
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # The global admin gate is the PROPERTY's declared master switch (`data_governance`
        # → OBDA_ENABLED), enforced once in `Assessor.run`: a disabled OBDA-READ makes the
        # assessor abstain, and `f.abstained` below carries the same words the station
        # panel's module line and the mechanism reader use — one gate, three readers.
        # Through the REGISTRY, not `scan_obda` directly. Calling the underlying function
        # made this the one scorer whose lane could silently diverge from the mechanism's:
        # a remap of `data_governance` would move the in-flight lane and leave this reading
        # the old computation — the exact asymmetry the substrate exists to remove. The
        # assessor wraps the same `scan_obda` call, so the number is unchanged.
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        from trusted_data_agent.agent import assurance as _A
        try:
            _A.bootstrap()
            _a = _reg.resolve("data_governance", _Lane.POST_HOC)
        except Exception as exc:  # noqa: BLE001
            return _na(self, f"assurance substrate unavailable: {exc}")
        if _a is None:
            return _na(self, "no assessor is bound to data_governance")
        f = _a.run(_rc.from_run(run))
        if f.abstained:
            return _na(self, f.abstained)
        ratio = float(f.metrics.get("governed_ratio", 0.0))
        total = int(f.metrics.get("operations", 0))
        gov = int(round(ratio * total))
        free = total - gov
        expl = f"{gov} governed · {free} free-formed → {ratio:.0%} governed"
        gate, healthy = _cuts(case, "min_governed_data_ratio")
        if gate is None:
            return _metric(self, None, ratio, explanation=expl)
        return banded_metric(self, value=ratio, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True, explanation=expl)


class HonestAbsenceScorer(Scorer):
    """Did the answer report an absence it never established?

    The inverse polarity of grounding, and the only check in the stack that catches it.
    Every other layer asks whether what the answer ASSERTS is supported; this asks
    whether what it DENIES is. A turn whose every data query failed and which then
    reports "there are none" produces a confident false negative — not an error, not a
    refusal, but a fluent answer the reader cannot distinguish from a competent negative
    result. The grounding gate passes it (nothing out of scope), the guardrails pass it
    (no PII, no injection), and `tool_success_rate` records the failed query as a
    trajectory signal while the answer still reads authoritative.

    Deterministic, 0 tokens, boolean — there is no line to move, so it takes no
    threshold and adds nothing to the admin sliders.

    It REUSES `agent.honesty.gate` rather than restating the rule, exactly as
    `scope_adherence` reuses `grounding.primitives`: a second copy of "what counts as
    an unestablished absence" would let the scorer and the gate that rewrites answers
    disagree about the same turn.
    """

    dimension = "honest_absence"
    # Declared so the Guide can route this property to its measurement — but NOT
    # resolved through the registry, deliberately. This scorer reads the gate's
    # STAMP first, and the assessor cannot: in `enforce` the gate rewrites the
    # answer, so a re-derivation from delivered text would score a caught
    # over-claim as clean. Recorded in the gate's not-registry-routed list.
    property = "absence_claim"
    kind = ScoreKind.DETERMINISTIC
    # The classes the runtime gate actually runs on. Ideate and Focus issue no raw-SQL
    # data queries, so the check is structurally inapplicable rather than merely quiet.
    applies_to = _TOOL_CLASSES

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        try:
            from trusted_data_agent.agent.honesty import gate as _hgate
            from trusted_data_agent.agent.honesty import primitives as _hp
        except Exception as e:                                    # pragma: no cover
            return _na(self, f"honesty gate unavailable ({type(e).__name__})")

        # STAMP FIRST, and this is load-bearing rather than an optimisation. In `enforce`
        # the gate REWRITES the answer into an honest retrieval-failure, so re-deriving
        # from the delivered text would find no absence claim and score the turn CLEAN —
        # the agent over-claimed, the gate caught it, and the record would say nothing
        # happened. The stamp is the gate's own decision and outranks any re-derivation.
        stamp = run.raw.get("honesty")
        if isinstance(stamp, dict) and stamp.get("decision"):
            return self._verdict(
                False, 0.0,
                stamp.get("reason") or "the honesty gate found an unestablished absence claim",
                evidence=[f"gate decision: {stamp.get('decision')}"])

        calls = run.tool_calls()
        attempted, succeeded, _errored, _err = _hp.data_query_outcomes(calls)
        if attempted == 0:
            return _na(self, "no raw-SQL data queries this turn — no absence to establish")
        answer = run.final_answer_text or ""
        if not _hp.asserts_nonexistence(answer):
            return _na(self, "the answer makes no non-existence claim")

        # The gate's own verdict on the original answer. None ⇒ the claim is backed.
        if _hgate.assess(answer, calls) is None:
            return self._verdict(
                True, 1.0,
                f"absence claim backed by {succeeded} successful data "
                f"quer{'y' if succeeded == 1 else 'ies'}")
        return self._verdict(
            False, 0.0,
            "the answer reports an absence, but every data query this turn failed — "
            "non-existence was never established",
            evidence=[f"{_errored} failed quer{'y' if _errored == 1 else 'ies'}, none succeeded"])



class IdentifierProvenanceScorer(Scorer):
    """F-002 — did the answer name an identifier its skill library does not contain?

    BOOLEAN, and deliberately so: an identifier is real or it is not, so there is no line
    to move and this adds no slider. The companion to `citation_fidelity`, covering what
    that check structurally cannot — a fabrication that cites nothing (session ab53a8f6:
    five decisions settled, four ids invented, zero read claims made).

    Abstains when the skill declares no closed vocabulary, and when a citation verdict
    took precedence on the same answer.
    """

    dimension = "identifier_provenance"
    property = "identifier_provenance"
    kind = ScoreKind.DETERMINISTIC
    #: Non-gating, for the same reason as its sibling: an invented name is a provenance
    #: defect, and the gate has already disclosed it. Failing a release on it would
    #: convict reasoning the check never examined.
    gating = False
    applies_to = frozenset({"tool_enabled", "conversation_with_tools",
                            "rag_focused", "genie"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        try:
            _A.bootstrap()
            _a = _reg.resolve("identifier_provenance", _Lane.POST_HOC)
        except Exception as e:  # noqa: BLE001
            return _na(self, f"assurance substrate unavailable ({type(e).__name__})")
        if _a is None:
            return _na(self, "identifier_provenance is not bound post-hoc")
        _f = _a.run(_rc.from_run(run))
        if _f.abstained:
            return _na(self, _f.abstained)
        n = int(_f.metrics.get("fabricated") or 0)
        names = [e.split("invented: ", 1)[-1] for e in (_f.evidence or ())]
        return self._verdict(
            n == 0, 1.0 if n == 0 else 0.0,
            (f"{n} identifier(s) the library does not contain: {', '.join(names[:3])}"
             if n else "every identifier the answer names exists in the library"),
            evidence=list(_f.evidence or []))


class CitationFidelityScorer(Scorer):
    """F-001 — of the library sections this answer CITED, how many did it open?

    The measurement half of the cited-but-unread gate. It reads the gate's own STAMP
    rather than re-deriving, for the reason ``HonestAbsenceScorer`` records for its own
    stamp-first rule and one more that is specific here: under ``annotate``/``enforce``
    the gate APPENDS a disclosure naming the unread sections, so a re-derivation from
    delivered text would find those very names in the answer and could read the
    platform's own correction as a fresh claim. The stamp is the assessment; this bands
    it.

    One assessment, two lanes: the control acted on exactly this number, so the card and
    the gate cannot disagree about the same turn.

    Abstains when the profile has no progressive library (no stamp at all) and when the
    answer cited nothing — a turn that made no provenance claim has no fidelity to
    report, and scoring it 1.0 would inflate every agent that never cites.
    """

    dimension = "citation_fidelity"
    property = "citation_provenance"
    kind = ScoreKind.METRIC
    #: Non-gating. An unbacked citation is a provenance defect, not proof the answer is
    #: wrong — and the gate has already disclosed it to the reader. Failing a release on
    #: it would convict content the check never examined. Observe, don't constrain.
    gating = False
    #: Every class that can reach a library. Pure ``llm_only`` is excluded by
    #: construction, not by policy: a profile with an active component tool is routed to
    #: the ReAct engine, so a pure Ideate turn can never hold TDA_SkillLibrary.
    applies_to = frozenset({"tool_enabled", "conversation_with_tools",
                            "rag_focused", "genie"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # Resolved through the registry, so a remap moves BOTH lanes together. The
        # assessor recovers the in-flight assessment from the turn's stamp rather than
        # re-deriving it — see `assessors/citation_provenance.py` for why recovering is
        # stronger here than repeating.
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        try:
            _A.bootstrap()
            _a = _reg.resolve("citation_provenance", _Lane.POST_HOC)
        except Exception as e:  # noqa: BLE001
            return _na(self, f"assurance substrate unavailable ({type(e).__name__})")
        if _a is None:
            return _na(self, "citation_provenance is not bound post-hoc")
        _f = _a.run(_rc.from_run(run))
        if _f.abstained:
            return _na(self, _f.abstained)
        cited = int(_f.metrics.get("cited") or 0)
        return self._band(case, float(_f.metrics.get("fidelity") or 0.0), cited,
                          int(_f.metrics.get("unread") or 0),
                          unread=[x.strip() for e in (_f.evidence or ()) if e.startswith("unread:")
                                  for x in e.split(":", 1)[1].split(",") if x.strip()])

    def _band(self, case: EvalCase, value: float, cited: int, unread_n: int,
              unread=None) -> Verdict:
        backed = cited - unread_n
        why = f"{backed}/{cited} citation(s) backed by an actual read"
        ev = [f"unread: {', '.join(unread[:5])}"] if unread else []
        gate, healthy = _cuts(case, "min_citation_fidelity")
        if gate is None:
            return _metric(self, None, round(value, 4), explanation=why, evidence=ev)
        return banded_metric(self, value=round(value, 4), gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True, explanation=why, evidence=ev)


class LibraryReadFirstScorer(Scorer):
    """Of the statements that used what a library section teaches, the share the agent ran
    AFTER opening that section (SKILL_ARCHITECTURE §4a: "nothing verifies a read happened").

    Reads the turn's `library_use` stamp, computed once at the citation seam by
    `skills.library_use.record` — the same record the per-section aggregate reads, so the
    Scorecard and the aggregate cannot disagree about a turn. The explanation names all four
    cells (read/unread × ran/failed): the rate alone says whether the agent read, the cells say
    whether reading mattered — the evidence a decision to FORCE reads needs.

    Informational (non-gating, no threshold): reading is a means, not a requirement — an agent
    that already knows NPATH may run it correctly without the manual. Abstains when the turn
    used no library operator: a turn that never touched the library has no rate."""
    dimension = "library_read_first"
    kind = ScoreKind.METRIC
    gating = False
    #: The classes whose turns reach both a progressive library and SQL. Coordinate is
    #: excluded: its experts run the SQL in their own turns and carry their own record.
    applies_to = frozenset({"tool_enabled", "conversation_with_tools", "rag_focused"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        from trusted_data_agent.skills import library_use as _lu
        rec = run.library_use()
        if not rec:
            return _na(self, "no library record on this turn")
        rate = _lu.read_first_rate(rec)
        if rate is None:
            return _na(self, _lu.describe(rec))
        ev = []
        for s in rec.get("statements") or []:
            ev.append(f"{'read' if s.get('read_before') else 'unread'} · "
                      f"{'ran' if s.get('ran') else 'failed'}: "
                      f"{', '.join(s.get('operators') or [])} ({', '.join(s.get('sections') or [])})")
        return _metric(self, None, round(rate, 3), explanation=_lu.describe(rec), evidence=ev[:8])


class ActionSafetyScorer(Scorer):
    """OBDA-ACT (PROP-008 §5.6) — of the signed-action writes this turn put to the database, the
    share whose every check held (preconditions, the bound, postconditions).

    Reads the ONE fold every other surface reads (``action_perform.summarize`` over
    ``turn['actions']`` + ``turn['action_confirmations']``), so a prepared write a person
    later confirmed counts once, as how it ended. A write is counted only when the database
    evaluated its checks (it carries a ``checks`` list): an act refused before it compiled
    (not granted, not signed, no connection) reached no table and is not a write. A failed
    check is never harm — the transaction rolled back — but it is the agent proposing a
    write its own contract refuses, which is what this measures. Informational (a fraction, never a verdict); abstains when
    the turn put no write to the database."""
    dimension = "action_safety"
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        t = run.raw
        recs, conf = t.get("actions"), t.get("action_confirmations")
        if not recs and not conf:
            return _na(self, "no signed action was invoked on this turn")
        from trusted_data_agent.core.action_perform import summarize
        acts = summarize(recs if isinstance(recs, list) else [],
                         conf if isinstance(conf, dict) else {})["acts"]
        writes = [a for a in acts if isinstance(a.get("checks"), list) and a["checks"]]
        if not writes:
            return _na(self, "no action write reached the database on this turn")
        held = [a for a in writes if all(bool(c.get("holds")) for c in a["checks"]
                                         if isinstance(c, dict))]
        broken = [f"{a.get('action') or 'action'}: "
                  + ", ".join(str(c.get("id")) for c in a["checks"]
                              if isinstance(c, dict) and not c.get("holds"))
                  for a in writes if a not in held]
        rate = len(held) / len(writes)
        expl = (f"{len(held)} of {len(writes)} action write(s) held every check"
                + (f" — failed: {'; '.join(broken[:3])} (rolled back, nothing written)"
                   if broken else ""))
        return _metric(self, None, round(rate, 3), explanation=expl, evidence=broken[:5])


class GuessedPastAmbiguityScorer(Scorer):
    """PROP-011 — the under-asking measure. The platform records every ambiguity its code saw
    (`ambiguities_observed`: a name matching two of the user's things, …). A turn that carried
    on past one without stopping to ask (`input_required`) guessed: it fails. Deterministic, no
    judge; abstains when code saw nothing — the model's own judgement is not measured here."""

    dimension = "guessed_past_ambiguity"
    kind = ScoreKind.DETERMINISTIC
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        raw = run.raw if isinstance(run.raw, dict) else {}
        observed = raw.get("ambiguities_observed") or []
        if not observed:
            return _na(self, "no code-detected ambiguity on this turn")
        from trusted_data_agent.core import turn_states as _ts
        if _ts.is_interrupted(run.status) or raw.get("input_request_id"):
            return self._verdict(True, 1.0, f"the turn stopped to ask ({len(observed)} ambiguity seen)")
        kinds = sorted({str(o.get("kind")) for o in observed if isinstance(o, dict)})
        return self._verdict(False, 0.0, f"went past {len(observed)} code-detected ambiguit{'y' if len(observed) == 1 else 'ies'} "
                                         f"({', '.join(kinds)}) without asking")


_ALL = [
    LatencyScorer(), TokenUsageScorer(), CacheEfficiencyScorer(), BudgetAdherenceScorer(), CleanTerminationScorer(), ContextHealthScorer(),
    GuessedPastAmbiguityScorer(),
    ToolSuccessRateScorer(), ToolLoopScorer(), ErrorRecoveryScorer(),
    ScopeAdherenceScorer(), ToolEconomyScorer(), KgSeedUtilizationScorer(), EnrichmentRecallScorer(),
    OutputSanityScorer(), NumericFidelityScorer(),
    PlanExecutionCoverageScorer(), RetrievalRelevanceScorer(), ExpertSuccessScorer(),
    GovernedDataRatioScorer(),
    ContextUtilizationScorer(), KnowledgeFreshnessScorer(),
    HonestAbsenceScorer(), SurfaceViolationScorer(),
    CitationFidelityScorer(), IdentifierProvenanceScorer(),
    LibraryReadFirstScorer(), ActionSafetyScorer(),
]
for _s in _ALL:
    register_scorer(_s)
