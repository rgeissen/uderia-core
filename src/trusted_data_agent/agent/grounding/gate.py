"""Tier 1.1 — the synchronous grounding gate (+ RAG Trust Hardening Wave 1).

Pure, deterministic, 0-token. ONE gate, TWO trigger families (RAG_TRUST_HARDENING.md §1.1):

  * **scope**  — the answer/tool calls reach outside the KG-declared deployable scope
                 (the shipped Tier 1.1 family; unchanged).
  * **corpus** — the answer asserts specifics (numbers / verbatim quotes / identifiers)
                 the retrieved knowledge chunks do not support (Wave 1, `corpus.py`).

The decision machinery (mode ladder, needs_judge escalation, judge-backed enforce, review
retention, fail-open) is shared; each family has its own deterministic trigger and its own
adjudicating judge (`scope_faithfulness` vs `groundedness` — verdict.judge_family). Each
family abstains cleanly when its inputs are absent (no scope / no chunks), so a turn with
neither behaves exactly as before the gate existed, and a scope-only turn behaves
byte-identically to the shipped Tier 1.1 gate.

Resolved design decisions baked in (TIER_1.1 §5/§14 + RAG_TRUST_HARDENING §1.2):
  - Scope drift and unsupported claims are judge-adjudicated in enforce, never
    deterministically blocked (the benchmark proved deterministic blocking punishes honest
    refusals). The deterministic layer is a recall-first TRIGGER.
  - Ungrounded numbers (scope family) stay annotate-only. Corpus findings arriving here
    are post-second-chance (the runtime cleared spans a targeted retrieval supported).
  - ``observe`` measures + records but never alters the answer.

This module is side-effect-free: no logging, no SSE, no persistence, no retrieval — the
runtime owns second-chance retrieval and passes the final ``corpus_findings`` in.
"""
from __future__ import annotations

import re
from typing import Optional

from trusted_data_agent.agent.grounding import corpus as corpus_mod
from trusted_data_agent.agent.grounding.corpus import CorpusFindings
from trusted_data_agent.agent.grounding.primitives import (
    scope_diff, norm_nums, answer_out_of_scope_refs,
    answer_nums as answer_nums_of,
)
from trusted_data_agent.agent.grounding.types import (
    Decision, GroundingConfig, GroundingInputs, GroundingVerdict,
)

_SEVERITY = {Decision.PASS: 0, Decision.ANNOTATE: 1, Decision.BLOCK: 2}


class GroundingGate:
    def __init__(self, config: GroundingConfig | None = None):
        self.config = config or GroundingConfig()

    def evaluate(self, inputs: GroundingInputs,
                 corpus_findings: Optional[CorpusFindings] = None) -> GroundingVerdict:
        cfg = self.config
        mode = cfg.normalized_mode()
        corpus_mode = cfg.normalized_corpus_mode()
        scope = frozenset(inputs.scope_databases or ())
        scope_sorted = tuple(sorted(scope))

        # ── family activation ────────────────────────────────────────────────
        scope_active = mode != "off" and bool(scope)
        corpus_active = corpus_mode != "off" and bool(inputs.retrieved_chunks)

        if not scope_active and not corpus_active:
            if mode == "off" and corpus_mode == "off":
                return GroundingVerdict(Decision.PASS, "grounding gate off",
                                        scope_databases=scope_sorted)
            # Abstain: nothing to adhere to (no declared scope, no retrieved chunks).
            return GroundingVerdict(Decision.PASS, "no KG-declared deployable scope")

        # ── scope family (byte-identical to the shipped gate) ────────────────
        out_of_scope: tuple = ()
        answer_refs: tuple = ()
        numeric_fidelity = None
        ungrounded: tuple = ()
        scope_trigger = False
        scope_numeric_concern = False
        if scope_active:
            out_of_scope = tuple(sorted(scope_diff(scope, inputs.tool_calls)))
            answer_refs = tuple(sorted(answer_out_of_scope_refs(
                inputs.answer_text, scope, inputs.scope_entities,
                data_text=(inputs.grounding_text if inputs.value_text is None
                           else inputs.value_text))))
            answer_nums = answer_nums_of(inputs.answer_text)
            data_nums = norm_nums(inputs.grounding_text)
            if data_nums:
                # a number the turn's inputs vouch for (the user's words, a KG-declared
                # code or span) is accounted for — it is not a figure the data must hold
                data_nums = data_nums | set(getattr(inputs, "vouched_numbers", ()) or ())
            if answer_nums and data_nums:
                grounded = sum(1 for n in answer_nums if n in data_nums)
                numeric_fidelity = round(grounded / len(answer_nums), 4)
                ungrounded = tuple(sorted(n for n in answer_nums if n not in data_nums))
            scope_trigger = bool(out_of_scope) or bool(answer_refs)
            scope_numeric_concern = (numeric_fidelity is not None
                                     and cfg.min_numeric_fidelity is not None
                                     and numeric_fidelity < cfg.min_numeric_fidelity)

        # ── corpus family (Wave 1) ───────────────────────────────────────────
        cf = None
        corpus_trigger = False
        context_use_low = False
        if corpus_active:
            cf = corpus_findings if corpus_findings is not None else corpus_mod.assess(
                inputs.answer_text, inputs.retrieved_chunks, inputs.question)
            corpus_trigger = cf.triggered
            # ── the span-free blind spot (symmetry map A8) ────────────────────
            # `cf.triggered` fires on SPANS — numbers, verbatim quotes, identifiers. A
            # fluent fabrication carries none, so it passes this family and the honesty
            # RAG check (which requires `claims_checked > 0`) alike. `context_use` needs
            # no spans: it asks whether the answer's words appear in its sources at all.
            #
            # It only ESCALATES — never decides — because it false-positives on brevity: a
            # correct terse summary and an honest refusal both score low. The judge that
            # already adjudicates this family makes the call.
            #
            # OFF unless an admin configured the floor. An absolute overlap floor does not
            # transfer across answer styles, the same reason `assess_rag` ships its
            # absolute branch disabled — so out of the box this is byte-identical.
            context_use_low = self._context_use_low(inputs)
            corpus_trigger = corpus_trigger or context_use_low

        # PROP-003 — a bound third-party module routed to this gate. It ESCALATES, exactly
        # as `context_use` does and for the same reason: a plugin's finding is an
        # assessment, and the judge that already adjudicates this family makes the call. It
        # makes the corpus family ACTIVE for this turn, because a pack routed here IS a
        # corpus-support-family assessment — otherwise a pack could bind, fire, and be
        # silently ignored on any turn where the native family happened to be inactive.
        plugin_findings = self._plugin_findings(inputs)
        if plugin_findings:
            corpus_active = True
            corpus_trigger = True

        # ── per-family decisions on the shared ladder ────────────────────────
        scope_dec, scope_judge = Decision.PASS, False
        if scope_active:
            if mode == "annotate" and (scope_trigger or scope_numeric_concern):
                scope_dec = Decision.ANNOTATE
            elif mode == "enforce":
                if scope_trigger:
                    scope_judge = True  # judge makes the real call
                elif scope_numeric_concern:
                    # Enforce ⊇ annotate: the numeric-fidelity floor is a signal the
                    # scope judge never adjudicates, so climbing to enforce must not
                    # silently DROP the disclosure annotate would have made. Numeric-only
                    # concern stays a 0-token annotation — never a judge escalation
                    # (numbers are noisy: formatting, derived sums/averages).
                    scope_dec = Decision.ANNOTATE

        corpus_dec, corpus_judge = Decision.PASS, False
        if corpus_active and corpus_trigger:
            if corpus_mode == "annotate":
                corpus_dec = Decision.ANNOTATE
            elif corpus_mode == "enforce":
                corpus_judge = True

        decision = max((scope_dec, corpus_dec), key=lambda d: _SEVERITY[d])
        needs_judge = scope_judge or corpus_judge
        # Scope drift is the graver claim — its judge takes precedence when both fired;
        # the corpus findings still ride the event either way.
        judge_family = ("scope_faithfulness" if scope_judge
                        else ("groundedness" if corpus_judge else "scope_faithfulness"))

        ground = self._ground(scope_active, corpus_active,
                              scope_trigger or scope_numeric_concern, corpus_trigger)

        reason = self._reason(mode, corpus_mode, scope_active, corpus_active,
                              needs_judge, decision, scope_trigger, corpus_trigger)

        annotation = None
        if decision is Decision.ANNOTATE:
            annotation = self._annotation(out_of_scope, ungrounded, answer_refs,
                                          scope_sorted, decision, cf)

        return GroundingVerdict(
            decision=decision, reason=reason, scope_databases=scope_sorted,
            out_of_scope_dbs=out_of_scope, ungrounded_numbers=ungrounded,
            numeric_fidelity=numeric_fidelity, annotation_md=annotation,
            needs_judge=needs_judge, answer_oos_refs=answer_refs,
            numeric_concern=scope_numeric_concern,
            ground=ground, judge_family=judge_family,
            unsupported_claims=(cf.unsupported_spans if cf else ()),
            second_chance_cleared=(cf.second_chance_cleared if cf else ()),
            claims_checked=(cf.claims_checked if cf else 0),
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _context_use_low(inputs) -> bool:
        """The `context_use` property, resolved through the assurance registry.

        Never a second implementation of the overlap: the post-hoc measurement reads the
        same assessor, so the gate cannot escalate on a number the Scorecard disagrees
        with. Fails CLOSED-as-quiet — an unresolvable property must not manufacture an
        escalation, and today's behaviour is no escalation.
        """
        try:
            from trusted_data_agent.core.config import APP_CONFIG
            from trusted_data_agent.agent import assurance as _A
            from trusted_data_agent.agent.assurance import registry as _reg
            from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
            from trusted_data_agent.agent.assurance.contract import AssessmentContext as _C

            floor = (getattr(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {}) or {}).get(
                "min_context_utilization")
            if floor is None:
                return False
            if isinstance(floor, dict):          # the 3-band shape {healthy, danger}
                floor = floor.get("danger")
            if floor is None:
                return False
            _A.bootstrap()
            a = _reg.resolve("context_use", _Lane.IN_FLIGHT_TRIGGERED)
            if a is None:
                return False
            f = a.run(_C(answer=inputs.answer_text,
                         chunks=tuple(inputs.retrieved_chunks or ()),
                         config={"min_context_utilization": float(floor)}))
            return bool(f.triggered and not f.abstained)
        except Exception:  # noqa: BLE001 — a gate must never break on wiring
            return False

    @staticmethod
    def _plugin_findings(inputs):
        """Third-party assessments routed to this gate (PROP-003). `[]` when none.

        Fails open to `[]` like every other wiring path here: a gate must never break on
        a plugin, and a plugin that cannot run must not silently change what the gate
        would otherwise have decided.
        """
        try:
            from trusted_data_agent.agent import assurance as _A
            from trusted_data_agent.agent.assurance import inflight as _if
            from trusted_data_agent.agent.assurance.contract import AssessmentContext as _C
            _A.bootstrap()
            return _if.augment(
                "grounding_gate",
                ctx=_C(answer=inputs.answer_text or "",
                   question=getattr(inputs, "question", "") or "",
                   chunks=tuple(inputs.retrieved_chunks or ()),
                   scope_databases=tuple(getattr(inputs, "scope_databases", ()) or ())))
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _ground(scope_active, corpus_active, scope_findings, corpus_findings) -> str:
        if scope_findings and corpus_findings:
            return "both"
        if corpus_findings:
            return "corpus"
        if scope_findings:
            return "scope"
        # no findings — name the family that ran (legacy default: scope)
        if scope_active and corpus_active:
            return "both"
        return "corpus" if corpus_active else "scope"

    @staticmethod
    def _reason(mode, corpus_mode, scope_active, corpus_active,
                needs_judge, decision, scope_trigger, corpus_trigger) -> str:
        if needs_judge:
            fams = []
            if scope_trigger and mode == "enforce":
                fams.append("scope")
            if corpus_trigger and corpus_mode == "enforce":
                fams.append("corpus")
            return f"enforce — judge required ({'+'.join(fams) or 'trigger'})"
        if decision is Decision.ANNOTATE:
            return "flagged"
        if mode == "observe" and scope_active and not corpus_active:
            return "observe"
        if corpus_active and not scope_active:
            return ("corpus-supported" if not corpus_trigger else
                    "observe — unsupported claims recorded")
        if scope_active and corpus_active:
            return "in scope · corpus-checked"
        return "in scope"

    @staticmethod
    def _judge_explanation(reason: str) -> str:
        """The judge's own words, lifted out of the verdict's bookkeeping (#42).

        The reason reads
        ``enforce — judge: unsupported by the knowledge base: panel 0✓/2✗/0? — <why>``.
        The prefix and the tally are for the trace; ``<why>`` is the only part that
        tells a reader WHAT was wrong. Returns "" when there is no adjudication to
        quote, so the caller simply omits the sentence.
        """
        text = (reason or "").strip()
        m = re.search(r"panel\s+\d+\s*[✓✔].*?[—-]\s*(.+)$", text, re.DOTALL)
        if not m:
            return ""
        why = " ".join(m.group(1).split())
        # Judges answer per-dimension and their explanations are concatenated with "; ".
        # The first is the ruling that decided it; the rest elaborate.
        first = why.split(";")[0].strip().rstrip(".")
        if len(first) > 300:
            first = first[:299].rsplit(" ", 1)[0] + "…"
        return first

    @staticmethod
    def _annotation(out_of_scope, ungrounded, answer_refs, scope_sorted, decision,
                    cf: Optional[CorpusFindings] = None,
                    judge_reason: str = "") -> str | None:
        parts = []
        # A WITHHELD answer must lead with WHY it was withheld. This opened with the
        # deterministic trigger's spans — which are a wide net by design, and on the
        # walkthrough's first real block named five red herrings while the judge's
        # actual finding (a roadmap spec presented as shipped configuration) appeared
        # nowhere. The trigger list stays, demoted to what it is: supporting detail.
        if decision == Decision.BLOCK:
            why = GroundingGate._judge_explanation(judge_reason)
            if why:
                parts.append(f"This answer was withheld after review: {why}.")
        sources = sorted(set(out_of_scope) | set(answer_refs or ()))
        if sources:
            verb = "was withheld — it referenced" if decision == Decision.BLOCK else "may reference"
            scope_txt = ", ".join(f"`{s}`" for s in scope_sorted) or "the declared scope"
            src_txt = ", ".join(f"`{s}`" for s in sources)
            parts.append(f"This answer {verb} data outside the model’s declared scope "
                         f"({scope_txt}). Unexpected source(s): {src_txt}.")
        if ungrounded:
            nums = ", ".join(f"`{n}`" for n in ungrounded[:6])
            parts.append(f"Some figures could not be matched to the retrieved data: {nums}.")
        if cf is not None and cf.unsupported_spans:
            spans = ", ".join(f"`{s}`" for s in cf.unsupported_spans[:6])
            if decision == Decision.BLOCK:
                # The answer is already withheld; "unsupported ≠ false" is the hedge that
                # belongs on an ANNOTATE, where the answer was delivered anyway.
                parts.append("The check flagged these statements as unmatched to the "
                             f"knowledge base: {spans}.")
            else:
                parts.append("Some statements could not be matched to the knowledge base "
                             f"(unsupported ≠ false): {spans}.")
        return " ".join(parts) or None
