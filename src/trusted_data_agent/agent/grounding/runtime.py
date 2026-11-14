"""Tier 1.1 — engine-agnostic grounding orchestration.

The single place the full grounding flow lives: deterministic gate → (in ``enforce``, on a
trigger) the scope_faithfulness LLM judge → apply the verdict. Engine-agnostic by design — it
takes explicit inputs rather than reaching into an executor — so EVERY engine drives the same
logic with no duplication (charter #14: functionality must support all engines; "always
modular"). The Optimize/Ideate/Focus/Conversation engines call it via
``executor._run_grounding_gate_full``; the Coordinate (genie) engine, which has no
``PlanExecutor``, calls ``run_grounding`` directly.

Fails OPEN throughout (charter #8/#15): any error returns the original answer untouched. The
judge call is timeout-bounded and its token cost is routed through the cost engine exactly
like the online-eval judges (charter #16).

See docs/roadmap/TIER_1.1_GROUNDING_GATE.md and docs/Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md §6a.
"""
from __future__ import annotations

import asyncio
import logging

from trusted_data_agent.agent.grounding import adapter
from trusted_data_agent.agent.grounding.gate import GroundingGate
from trusted_data_agent.agent.grounding.enforce import resolve_enforced_verdict
from trusted_data_agent.core import posture_modes as _modes

logger = logging.getLogger("quart.app")


# ── the trace must be self-describing (migration P1) ─────────────────────────
# Grounding differs from the guardrails: when the gate RUNS it already returns a
# verdict event, pass included, and the engines render it. What was silent were
# the three paths where it did NOT run — master off, nothing groundable in the
# answer, and a failure. One `None` for all three, so the trace could not say
# whether grounding was off, inapplicable, or broken.
#
# The stamp is pure fact (`ran` / `acted` / `action`); WHETHER to render stays a
# per-surface decision. Grounding renders on the presence of a `decision`, which
# only a real verdict carries — so behaviour is byte-identical and the three new
# stamps are audit-only.
def _verdict_event(verdict, review: bool = False) -> dict:
    """The single place a verdict becomes an event. Every return path in this module
    goes through it, so `ran`/`acted` can never be attached to some verdicts and not
    others — the enforce/judge path was missed exactly that way once.

    ``review`` stamps the CONFIGURED mode (posture_modes.stamped_mode): a review gate
    that ran and found nothing must not stamp the `enforce` it was resolved to, or
    REVIEWED reads "no gate is set to park answers for a human"."""
    ev = verdict.to_event()
    if isinstance(ev, dict):
        ev["ran"] = True
        ev["acted"] = verdict.decision.value != "pass"
        if review:
            ev["mode"] = _modes.REVIEW
    return ev


def _stamp(*, ran: bool, action: str, **extra) -> dict:
    ev = {"ran": bool(ran), "acted": False, "action": action, "gate": "grounding"}
    ev.update(extra)
    return ev


async def run_grounding(answer_text: str, *, kg_enrichment_event, profile,
                        profile_class: str = "tool_enabled", trace_extra: dict = None,
                        user_uuid: str = None, session_id: str = None, turn_id=None,
                        query: str = "", event_handler=None, second_chance=None,
                        skip_judge: bool = False, park: bool = True, attached_texts=()):
    """Run the full grounding gate for one turn's answer. Returns ``(gated_text, event|None)``.

    ``skip_judge`` / ``park`` (S7 review): the outcome loop's RE-GRADE of a revision is
    deterministic (the judge already ruled on the first draft — a second judge call on the
    same turn is a second opinion nobody asked for, at a judge's price) and never parks (the
    reviewer disposes of the first draft, which is what the gate withheld).

    ``attached_texts`` — the document text the turn's attachments gave the model (the platform
    extracted it at upload). Evidence the answer may draw on, like a retrieved chunk: a figure
    quoted from the attached contract is not "unsupported". It only joins a turn the gate grades
    anyway — attachments alone never make a turn groundable — and S9's own readings, when present,
    take its place (`RunRecord.support_chunk_texts`).

    ``event`` is ``None`` — and ``answer_text`` byte-for-byte unchanged — when the gate is
    globally off, the turn has no groundable content (no KG scope, or no tool/retrieved data),
    or anything goes wrong (fails open). When non-``None`` the caller yields/emits it live AND
    stamps it onto the persisted turn for reload/audit (#17). ``profile`` is the engine's
    profile dict (its ``groundingConfig`` resolves the mode); ``trace_extra`` carries the
    engine's tool-call source (``execution_trace`` for Optimize, ``conversation_agent_events``
    for ReAct/genie) so the adapter's RunRecord extraction matches every engine shape.
    """
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        if not getattr(APP_CONFIG, "GROUNDING_GATE_ENABLED", False):
            return answer_text, _stamp(ran=False, action="off",
                                       reason="master switch off")
        # Always-on trusted scope (charter #14): union the profile's bound-KG deployed scope so
        # EVERY engine — including Coordinate/genie, which never pre-stamps via the executor's
        # _apply_trusted_scope — sees the query-invariant scope here. Idempotent for the four
        # engines that already stamped it; the only way genie catches out-of-domain drift.
        kg_enrichment_event = await adapter.ensure_trusted_scope(
            kg_enrichment_event, profile, user_uuid, session_id)
        turn = {"kg_enrichment_event": kg_enrichment_event, "final_summary_text": answer_text,
                "user_query": query or ""}
        if trace_extra:
            turn.update(trace_extra)
        # S9 — the turn's attachment sources join the evidence the gate reads (the live
        # registry, so every engine incl. genie sees them before the turn is persisted; the
        # persisted stamp carries the same text for the post-hoc scorer). Off ⇒ no stamp.
        try:
            if "attachment_sources" not in turn:
                from trusted_data_agent.llm import image_sources as _isrc
                _as = _isrc.stamp_for_turn()
                if _as:
                    turn["attachment_sources"] = _as
        except Exception:  # noqa: BLE001
            pass
        # The user's earlier words in this session (a primer states dates and names the
        # answer may repeat) are part of what the turn's inputs vouch for. Fail-open.
        try:
            if session_id and user_uuid and "context_text" not in turn:
                from trusted_data_agent.core import session_manager as _sm
                _sess = await _sm.get_session(user_uuid, session_id) or {}
                _wf = ((_sess.get("last_turn_data") or {}).get("workflow_history") or [])
                turn["context_text"] = adapter.session_context_text(_wf)
        except Exception:  # noqa: BLE001
            pass
        inputs = adapter.build_inputs(turn, answer_text)
        # Abstain cleanly (no event, answer untouched) when there is nothing to ground.
        if not adapter.has_groundable_content(inputs):
            # Inapplicable, not clean: there was nothing in this answer to ground.
            return answer_text, _stamp(ran=False, action="na",
                                       reason="no groundable content in this answer")
        _attached = [t for t in (attached_texts or ()) if t]
        if _attached:
            # the text the model was shown — the same key the post-hoc scorers read (there
            # filled from the conversation's guarded manifest)
            turn["attachment_evidence"] = {"texts": _attached}
            inputs = adapter.build_inputs(turn, answer_text)
        # HITL: ``review`` = enforce WITH RETENTION. A runtime-level composite (the
        # gate/config ladder stays off/observe/annotate/enforce), detected from the RAW
        # profile modes — config normalization would otherwise silently degrade the
        # unknown mode to observe, WEAKENING protection (the opposite of intent). The
        # corpus family's raw mode (groundingConfig.corpus.mode) gets the same guard.
        gc_raw = (profile or {}).get("groundingConfig") or {}
        # Agent-level inheritance for the review composite too: a gate with no own
        # mode inherits postureConfig.mode, so an agent-level `review` parks exactly
        # as a gate-level one (the corpus family follows the resolved gate mode).
        raw_mode = _modes.gate_raw_or_default(
            _modes.raw_mode_of(gc_raw, "mode"), profile)
        raw_corpus_mode = _modes.raw_mode_of(gc_raw, "corpus", "mode") or raw_mode
        review = _modes.any_review(raw_mode, raw_corpus_mode)
        cfg_profile = profile
        if review:
            gc = dict(gc_raw)
            if _modes.is_review(raw_mode):
                gc["mode"] = "enforce"
            if _modes.is_review(raw_corpus_mode):
                gc["corpus"] = {**(gc.get("corpus") or {}), "mode": "enforce"}
            cfg_profile = {**(profile or {}), "groundingConfig": gc}
        # `resolve_config` wants the FLATTENED scalar map (its docstring says so). It
        # used to be handed `APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS` raw, whose values
        # are `{"danger": x, "healthy": y}` dicts — so `_as_float` raised, the failure was
        # swallowed, and the gate kept its hardcoded default while the scorers used the
        # admin's number. ONE flattener, shared with `eval.online.default_assertions`.
        from trusted_data_agent.core.config import resolved_thresholds
        cfg = adapter.resolve_config(cfg_profile, True, resolved_thresholds())

        # ── Wave 1: corpus-support family — assess + C1 second-chance retrieval ──
        corpus_findings = None
        if inputs.retrieved_chunks and cfg.normalized_corpus_mode() != "off":
            from trusted_data_agent.agent.grounding import corpus as corpus_mod
            corpus_findings = corpus_mod.assess(
                inputs.answer_text, inputs.retrieved_chunks, inputs.question,
                vouched_numbers=inputs.vouched_numbers)
            if corpus_findings.unsupported_spans:
                fetch = second_chance or _build_second_chance(profile)
                if fetch:
                    corpus_findings = await _second_chance_clear(
                        corpus_findings, fetch)

        verdict = GroundingGate(cfg).evaluate(inputs, corpus_findings=corpus_findings)
        if verdict.needs_judge and not skip_judge:
            # enforce + trigger fired → the family's judge makes the real call
            # (scope → scope_faithfulness; corpus → groundedness).
            return await _enforce_with_judge(
                answer_text, verdict, turn, profile_class,
                user_uuid, session_id, turn_id, query, event_handler,
                review=review, profile_tag=(profile or {}).get("tag"))
        new_text = adapter.apply_to_answer(answer_text, verdict)
        logger.info(
            f"[GroundingGate] class={profile_class} mode={cfg.normalized_mode()} "
            f"decision={verdict.decision.value} out_of_scope={list(verdict.out_of_scope_dbs)} "
            f"answer_refs={list(verdict.answer_oos_refs)} needs_judge={verdict.needs_judge}")
        event = _verdict_event(verdict, review=review)
        # The session's own numbers the gate accepted travel with the turn, so the post-hoc
        # numeric scorer reads the SAME vouched set (RunRecord.vouched_numbers) — the earlier
        # user turns are not on the persisted turn otherwise.
        try:
            from trusted_data_agent.agent.grounding.primitives import norm_nums as _nn
            # … and the findings block the platform SHOWED the model this turn (the verified
            # results of earlier turns): a figure the platform put in front of the model is
            # one the turn's inputs vouch for. Same key, same post-hoc reader.
            _ctx = sorted(_nn(str(turn.get("context_text") or ""))
                          | _nn(str(turn.get("findings_context") or "")))[:200]
            if _ctx and isinstance(event, dict):
                event["context_numbers"] = _ctx
        except Exception:  # noqa: BLE001
            pass
        if skip_judge and verdict.needs_judge:
            event["judge_skipped"] = True   # a re-grade: the deterministic trigger alone decided
        # PROP-002 — `review` = enforce WITH RETENTION on EVERY acting path. The
        # judge path parked; this deterministic path withheld and silently
        # DISCARDED the original, exactly like `enforce` — the review promise
        # ("nothing lost, nothing silently delivered") broken on one branch.
        if verdict.decision.value == "block" and park and (review or _retain_enforced_blocks()):
            # review → owed work (pending); plain enforce → recovery ledger (retained).
            # A full withholding never silently destroys an answer by default.
            new_text = _park_review(
                event, answer_text, new_text,
                reason=verdict.reason,
                categories=list(verdict.out_of_scope_dbs or []),
                user_uuid=user_uuid, session_id=session_id, turn_id=turn_id,
                profile_tag=(profile or {}).get("tag"),
                status=("pending" if review else "retained"))
        return new_text, event
    except Exception as e:
        logger.warning(f"[GroundingGate] failed open: {e}", exc_info=True)
        # Failed open — recorded, and never mistakable for a pass.
        return answer_text, _stamp(ran=True, action="error", error=type(e).__name__)


def _park_review(event: dict, original_text: str, delivered_text: str, *,
                 reason, categories, user_uuid, session_id, turn_id,
                 profile_tag, status: str = "pending") -> str:
    """The ONE parking seam for the grounding gate (PROP-002) — the judge-confirmed
    path and the deterministic block, review mode AND the plain-enforce retention
    default, all route through it, so the "nothing lost" promise cannot hold on
    one branch and break on another. ``status='pending'`` (review mode) stamps
    ``review_id`` — a human owes a disposition; ``status='retained'`` (plain
    enforce, full withholding) stamps ``retained_id`` — recoverable, nothing owed,
    and the delivered note says so honestly. Fail-open: no user context ⇒ withheld
    but unparked, logged loudly (fail toward safety, never toward silence)."""
    review_id = None
    if user_uuid:
        from trusted_data_agent.core import review_queue
        review_id = review_queue.park(
            user_uuid, source="grounding",
            original_text=original_text, delivered_text=delivered_text,
            reason=reason, categories=categories,
            session_id=session_id, turn_id=turn_id, profile_tag=profile_tag,
            status=status)
    else:
        logger.warning("[GroundingGate] %s parking without user context — "
                       "withheld but could not park", status)
    if review_id:
        if status == "retained":
            event["retained_id"] = review_id
            delivered_text += ("\n\n> The withheld original was retained — "
                               "recoverable from the Assurance Inbox.")
        else:
            event["review_id"] = review_id
            delivered_text += ("\n\n> The withheld answer was parked for human review "
                               "(Assurance Inbox).")
    return delivered_text


def _retain_enforced_blocks() -> bool:
    """The plain-enforce retention default (config-gated escape hatch for
    data-minimization regimes)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "REVIEW_RETAIN_ENFORCED_BLOCKS", True))
    except Exception:  # noqa: BLE001
        return True


def _build_second_chance(profile):
    """C1 — build the engine-agnostic second-chance retriever from the profile's bound
    knowledge collections + the shared retriever instance. Returns an async
    ``fetch(span) -> [chunk_texts]`` or None (no collections / no retriever ⇒ the trigger
    proceeds on first-pass evidence, honestly). Never raises."""
    try:
        kc = (profile or {}).get("knowledgeConfig") or {}
        coll_ids = {str(c.get("id")) for c in (kc.get("collections") or []) if c.get("id")}
        if not coll_ids:
            return None
        from trusted_data_agent.core.config import APP_STATE
        retriever = APP_STATE.get("rag_retriever_instance")
        if not retriever:
            return None

        async def fetch(span: str):
            docs = await retriever.retrieve_examples(
                query=span, k=3, min_score=0.0,
                allowed_collection_ids=coll_ids, repository_type="knowledge")
            out = []
            for d in docs or []:
                c = (d or {}).get("content") or ""
                if c:
                    out.append(str(c))
            return out
        return fetch
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GroundingGate/corpus] second-chance builder failed: {e}")
        return None


async def _second_chance_clear(findings, fetch):
    """C1 — run the capped, targeted second-chance retrievals and clear the spans the
    corpus does support. One batched pass, ≤SECOND_CHANCE_CAP spans, 0 LLM tokens.
    Fail-open: any retrieval error keeps the first-pass findings."""
    from trusted_data_agent.agent.grounding import corpus as corpus_mod
    try:
        spans = corpus_mod.spans_for_second_chance(findings)
        if not spans:
            return findings
        results = await asyncio.gather(*(fetch(s) for s in spans),
                                       return_exceptions=True)
        cleared = []
        for span, texts in zip(spans, results):
            if isinstance(texts, Exception) or not texts:
                continue
            support = corpus_mod._norm_text("\n".join(texts))
            from trusted_data_agent.agent.grounding.primitives import norm_nums
            if corpus_mod.span_supported(span, support, norm_nums("\n".join(texts))):
                cleared.append(span)
        # Audible either way: this path was silently dead for every profile (an id-type
        # mismatch skipped every collection) and nothing said so (prod 727292c3).
        returned = sum(len(t) for t in results if isinstance(t, list))
        logger.info(f"[GroundingGate/corpus] second-chance retrieval: {len(spans)} span(s), "
                    f"{returned} chunk(s) returned, {len(cleared)} cleared")
        return corpus_mod.apply_second_chance(findings, cleared)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GroundingGate/corpus] second-chance failed open: {e}")
        return findings


async def _enforce_with_judge(answer_text, verdict, turn, profile_class,
                              user_uuid, session_id, turn_id, query, event_handler,
                              review: bool = False, profile_tag: str = None):
    """Judge-backed enforce: the triggering family's judge rules unfaithful→BLOCK /
    faithful→PASS / abstain-unavailable-timeout→ANNOTATE (fail-safe). Scope trigger →
    ``scope_faithfulness``; corpus trigger → ``groundedness`` (C2). Timeout-bounded;
    judge cost routed through the cost engine. Never withholds on uncertainty.

    ``review`` (HITL) = identical adjudication + withholding, but a confirmed
    BLOCK parks the ORIGINAL answer in the review queue for the owner to disposition
    (nothing lost, nothing silently delivered); the event is re-tagged mode=review."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        from trusted_data_agent.eval.types import RunRecord, EvalCase
        from trusted_data_agent.eval.judge import build_panel
        from trusted_data_agent.eval.scorers.judges import (
            ScopeFaithfulnessJudge, GroundednessJudge,
        )

        judge_cls = (GroundednessJudge
                     if getattr(verdict, "judge_family", "") == "groundedness"
                     else ScopeFaithfulnessJudge)
        # The trigger's findings lead the judge's adjudication (recall → precision):
        # the spans it could not find verbatim are handed over, with the WHOLE corpus
        # as evidence. Local to this call — `turn` is the gate's working copy.
        turn = dict(turn, grounding_trigger_spans=list(
            getattr(verdict, "unsupported_claims", ()) or ()))
        run = RunRecord(turn)
        case = EvalCase(id="grounding-enforce", query=query or "",
                        profile_class=profile_class or "tool_enabled")
        judge_passed = None
        judge_reason = "judge unavailable"
        jv = None
        panel = await build_panel(user_uuid=user_uuid)
        if panel and len(panel):
            _timeout = getattr(APP_CONFIG, "ONLINE_EVAL_TIMEOUT_SECONDS", 25)
            try:
                jv = await asyncio.wait_for(
                    judge_cls().score_async(case, run, panel), timeout=_timeout)
                judge_passed = jv.passed       # True faithful / False unfaithful / None abstain
                judge_reason = jv.explanation or ""
            finally:
                # Attribute whatever the panel actually spent, even on timeout/error — a
                # partially-completed judge already consumed real tokens (bill-or-lose).
                # `jv` rides along so the gate's judged card carries its RULING + the
                # per-model votes, like every other judge path (None on timeout — the
                # spend is real either way).
                await _attribute_judge_cost(panel, user_uuid, session_id, turn_id,
                                            event_handler, judge_verdict=jv)
        # `judged` iff a real panel existed. An empty panel reaches here with
        # judge_passed=None just as an abstention does, and claiming an adjudication
        # that never happened is exactly the dishonesty #37 records.
        final_verdict = resolve_enforced_verdict(
            verdict, judge_passed, judge_reason, judged=bool(panel and len(panel)))
        # Carry the judge's full ruling onto the verdict AFTER resolution: the resolver is
        # a pure, separately-tested function and widening its signature to ferry detail it
        # does not use would couple the decision to the record.
        if jv is not None:
            import dataclasses as _dc
            final_verdict = _dc.replace(
                final_verdict,
                judge_score=getattr(jv, "score", None),
                judge_votes=tuple(getattr(jv, "votes", None) or ()) or None)
        logger.info(f"[GroundingGate/judge] class={profile_class} judge_passed={judge_passed} "
                    f"decision={final_verdict.decision.value}")
        new_text = adapter.apply_to_answer(answer_text, final_verdict)
        event = _verdict_event(final_verdict)
        if review:
            event["mode"] = "review"
        if final_verdict.decision.value == "block" and (review or _retain_enforced_blocks()):
            new_text = _park_review(
                event, answer_text, new_text,
                reason=final_verdict.reason,
                categories=list(final_verdict.out_of_scope_dbs or []),
                user_uuid=user_uuid, session_id=session_id, turn_id=turn_id,
                profile_tag=profile_tag,
                status=("pending" if review else "retained"))
        return new_text, event
    except asyncio.TimeoutError:
        logger.warning("[GroundingGate/judge] timed out — fail-safe to annotate")
    except Exception as e:
        logger.warning(f"[GroundingGate/judge] failed: {e}", exc_info=True)
    # Fail-safe: judge unavailable/timeout/error → ANNOTATE (keep answer, attach note).
    final_verdict = resolve_enforced_verdict(
        verdict, None, "judge unavailable", judged=False)
    return adapter.apply_to_answer(answer_text, final_verdict), _verdict_event(final_verdict)


async def _attribute_judge_cost(panel, user_uuid, session_id, turn_id, event_handler,
                                judge_verdict=None):
    """Attribute the scope_faithfulness judge's token cost. The grounding gate runs INLINE
    during answer assembly — before the turn is persisted — so billing here would lose the
    turn-total accounting and the persisted judge step (the turn row doesn't exist yet) while
    session totals still increment, breaking session=Σturns (H5). Instead we DEFER: stash the
    usage and let execution_service flush it after the turn is saved. Never raises."""
    try:
        usage = list(getattr(panel, "usage", []) or [])
        if not usage:
            return
        in_tok = sum((u[2] or 0) for u in usage)
        out_tok = sum((u[3] or 0) for u in usage)
        if not (in_tok or out_tok):
            return
        cost_usd = 0.0
        models = []
        try:
            from trusted_data_agent.core.cost_manager import get_cost_manager
            cm = get_cost_manager()
            for _p, _m, _i, _o in usage:
                cost_usd += cm.calculate_cost(_p, _m, _i or 0, _o or 0)
                if _m:
                    models.append(_m)
        except Exception as e:
            logger.debug(f"[GroundingGate/judge] cost calc failed: {e}")
        from trusted_data_agent.eval.online import (
            defer_judge_usage, judge_usage_breakdown, judge_verdict_summary)
        # #46 — the grounding gate's judge panel spends on the user's credentials just as
        # the online hook's does, so its per-model breakdown travels the same route. One
        # pricing source (the shared CostManager) for both the aggregate and the split,
        # or the parts would not sum to the whole.
        breakdown = []
        try:
            from trusted_data_agent.core.cost_manager import get_cost_manager
            _cm = get_cost_manager()
            breakdown = judge_usage_breakdown(
                usage, cost_fn=lambda p, m, i, o: _cm.calculate_cost(p, m, i, o))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[GroundingGate/judge] usage breakdown failed: {e}")
            breakdown = judge_usage_breakdown(usage)
        defer_judge_usage(user_uuid, session_id, turn_id, in_tok, out_tok, cost_usd,
                          judge_models=sorted(set(models)) or None,
                          judge_usage=breakdown or None,
                          judge_verdicts=(judge_verdict_summary([judge_verdict])
                                          if judge_verdict is not None else None))
    except Exception as e:
        logger.debug(f"[GroundingGate/judge] cost attribution skipped: {e}")
