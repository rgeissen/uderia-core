"""Engine-agnostic entrypoint for the honesty post-condition. Deterministic, 0-token, fail-open."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.agent.honesty import gate as _gate
from trusted_data_agent.agent.honesty import adapter as _adapter

app_logger = logging.getLogger("quart.app")


from trusted_data_agent.core import posture_modes as _modes  # noqa: E402


def _park_for_review(*, source: str, original_text: str, delivered_text: str,
                     reason: str, profile: Optional[Dict[str, Any]],
                     user_uuid: Optional[str], session_id: Optional[str],
                     turn_id: Any) -> Optional[str]:
    """HITL retention for ``review`` mode — park the withheld original for the owner.

    Mirrors ``guardrails/runtime.scan_outbound``: without turn context the gate still
    ENFORCES, it just cannot park (fail toward safety, with a log line). Never raises.
    """
    if not user_uuid:
        app_logger.warning("[honesty] review mode without turn context — "
                           "enforced but could not park for review")
        return None
    try:
        from trusted_data_agent.core import review_queue
        return review_queue.park(
            user_uuid, source=source, original_text=original_text,
            delivered_text=delivered_text, reason=reason, categories=["honesty"],
            session_id=session_id, turn_id=turn_id,
            profile_tag=(profile or {}).get("tag"))
    except Exception as e:  # noqa: BLE001 — retention must never break a turn
        app_logger.warning(f"[honesty] review park failed (enforced anyway): {e}")
        return None


# ── the trace must be self-describing (migration P1) ─────────────────────────
# Honesty behaves like the guardrails: it returns an event only when it FIRES, so
# a gate that ran and found the answer honest left the same silence as one that
# is off and one that failed. `branch` distinguishes the SQL family (absence
# claimed on failed queries) from the RAG family (retrieval sufficiency) — they
# apply to different classes, and collapsing them would report an Ideate turn as
# though the SQL branch had cleared it.
#
# Stamping is unconditional; rendering stays gated on `acted`, so a clean turn is
# byte-identical in the trace and the UI.
def _stamp(branch: str, *, ran: bool, action: str, **extra) -> dict:
    ev = {"gate": "honesty", "branch": branch, "ran": bool(ran),
          "acted": False, "action": action}
    ev.update(extra)
    return ev


def _acted(event: dict, branch: str) -> dict:
    if isinstance(event, dict):
        event.setdefault("branch", branch)
        event["ran"] = True
        event["acted"] = True
    return event


def run_honesty(
    answer_text: str,
    tool_calls: List[Dict[str, Any]],
    *,
    profile: Optional[Dict[str, Any]] = None,
    profile_class: str = "tool_enabled",
    user_uuid: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Any = None,
    query: str = "",
    bridges: Optional[List[Dict[str, Any]]] = None,
    support_text: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Run the honesty gate over one turn's answer + tool calls.

    ``support_text`` is everything the model was GIVEN this turn besides the tool results
    (the knowledge block, the conversation so far) — the whitelist for the unsourced-record
    check. None ⇒ that check abstains.

    Returns ``(text, event | None)``:
      * abstain / ``off`` / error → ``(answer_text, None)`` (answer byte-for-byte unchanged)
      * ``observe`` / ``annotate`` (verdict) → ``(answer_text, event)`` — emit, don't rewrite
      * ``enforce`` (verdict) → ``(honest_message, event)`` — the false negative is replaced

    ``profile_class`` is accepted for parity/logging; gating is driven purely by whether the
    turn actually produced a confirmed false negative, so classes with no raw-SQL data queries
    (Ideate/Focus) abstain by construction.
    """
    try:
        mode = _adapter.resolve_mode(profile)
        review = _adapter.wants_review(profile)
        if mode == "off":
            return answer_text, _stamp("sql", ran=False, action="off",
                                       reason="not enabled for this agent")
        # an act claimed but never taken (no action record this turn) — the answer tells the
        # person a Confirm or a review exists when none does: disclosed (annotate) or
        # replaced (enforce), never passed as Verified
        unbacked = _gate.assess_unbacked_act(answer_text)
        if unbacked is not None:
            event = _gate.build_event(unbacked, mode, contained=(mode == "enforce"))
            if mode == "enforce":
                return unbacked.honest_message.strip()[len("Note: "):], _acted(event, "act")
            if mode == "annotate":
                event["decision"] = "disclosed"
                event["disclosure_appended"] = True
                return answer_text + unbacked.honest_message, _acted(event, "act")
            return answer_text, _acted(event, "act")
        # a record presented with no query behind it: values from nowhere the agent was
        # given (a tool-enabled agent that answered a data question without a data call)
        rec = _gate.assess_unsourced_record(answer_text, tool_calls or [],
                                            support_text=support_text, question=query)
        if rec is not None:
            event = _gate.build_event(rec, mode, contained=(mode == "enforce"))
            event["unsupported"] = list(rec.unsupported or [])
            event["unsupported_count"] = len(rec.unsupported or [])
            if mode == "enforce":
                replacement = _gate._unsourced_record_replacement(rec.unsupported or [], rec.failed_query_count)
                if review:
                    event["mode"] = "review"
                    review_id = _park_for_review(
                        source="honesty", original_text=answer_text, delivered_text=replacement,
                        reason=event.get("reason") or "", profile=profile, user_uuid=user_uuid,
                        session_id=session_id, turn_id=turn_id)
                    if review_id:
                        event["review_id"] = review_id
                return replacement, _acted(event, "sql")
            if mode == "annotate":
                event["decision"] = "disclosed"
                event["disclosure_appended"] = True
                return answer_text + rec.honest_message, _acted(event, "sql")
            return answer_text, _acted(event, "sql")
        verdict = _gate.assess(answer_text, tool_calls or [])
        if verdict is None:
            # No absence-claim verdict — check the OTHER dishonesty shape: silent
            # question substitution through a data product (a refused grain answered
            # at a coarser one without saying so). A danger verdict above takes
            # precedence; this one never replaces the answer, it APPENDS the
            # disclosure — the figure is correctly computed governed data, what is
            # missing is the admission of WHICH question it answers.
            sub = _gate.assess_grain_substitution(answer_text, tool_calls or [],
                                                  query=query)
            if sub is not None:
                # "block"/"contained" would claim a replacement that never happened —
                # this act APPENDS, so it gets its own decision word.
                event = _gate.build_event(sub, mode, contained=False)
                event["trigger"] = sub.trigger or "reject"
                if mode in ("annotate", "enforce"):
                    event["decision"] = "disclosed"
                    event["disclosure_appended"] = True
                    return answer_text + sub.honest_message, _acted(event, "sql")
                return answer_text, _acted(event, "sql")
            # The third append-only shape: a kind the declared class-level correspondence
            # does not bind, presented as if it did (the registry says what the bridge
            # covers; the answer said more). Same act as the two above — disclose, never
            # replace: the evidence rows are real, the attribution is what is missing.
            over = _gate.assess_correspondence_overreach(answer_text, tool_calls or [],
                                                         query=query, bridges=bridges)
            if over is not None:
                event = _gate.build_event(over, mode, contained=False)
                event["overreach"] = list(over.unread or [])
                if mode in ("annotate", "enforce"):
                    event["decision"] = "disclosed"
                    event["disclosure_appended"] = True
                    return answer_text + over.honest_message, _acted(event, "sql")
                return answer_text, _acted(event, "sql")
            # RAN and abstained: no unestablished absence claim in this answer.
            return answer_text, _stamp("sql", ran=True, action="pass",
                                       mode=_modes.stamped_mode(review, mode))
        # A sample-extrapolation verdict APPENDS its disclosure — the sampled figure
        # may still be useful and the rows are real; what is missing is the admission
        # that they are a sample, not the population.  Replacing the whole answer with
        # a note that opens "Note on this figure" would destroy the answer it annotates.
        # Same act, same decision word, as the grain-substitution disclosure above.
        if verdict.kind == "sample_extrapolation":
            event = _gate.build_event(verdict, mode, contained=False)
            if mode in ("annotate", "enforce"):
                event["decision"] = "disclosed"
                event["disclosure_appended"] = True
                return answer_text + verdict.honest_message, _acted(event, "sql")
            return answer_text, _acted(event, "sql")
        if mode == "enforce":
            event = _gate.build_event(verdict, mode, contained=True)
            if review:
                event["mode"] = "review"
                review_id = _park_for_review(
                    source="honesty", original_text=answer_text,
                    delivered_text=verdict.honest_message, reason=event.get("reason") or "",
                    profile=profile, user_uuid=user_uuid, session_id=session_id,
                    turn_id=turn_id)
                if review_id:
                    event["review_id"] = review_id
            return verdict.honest_message, _acted(event, "sql")
        return answer_text, _acted(_gate.build_event(verdict, mode, contained=False), "sql")
    except Exception as e:  # never break a turn
        app_logger.debug(f"[honesty] gate error (fail-open): {e}")
        # Never recorded as a pass — an unknown is not an honest answer.
        return answer_text, _stamp("sql", ran=True, action="error", error=type(e).__name__)


async def run_honesty_async(
    answer_text: str,
    tool_calls,
    *,
    profile=None,
    profile_class: str = "tool_enabled",
    user_uuid=None,
    session_id=None,
    turn_id=None,
    query: str = "",
    event_handler=None,
    bridges: Optional[List[Dict[str, Any]]] = None,
    support_text: Optional[str] = None,
):
    """The PRODUCTION seam (both engines): the sync gate, plus the OPTIONAL
    grain_faithfulness judge confirmation (``honestyConfig.grainJudge`` — off by
    default, UX-configurable in the profile editor's Honesty card).

    The judge adjudicates ONLY the heuristic (``trigger=entity``) grain path —
    it exists to veto the lexical heuristic's false positives, so:
      * judge says FAITHFUL  → the verdict is withdrawn (clean pass stamp that
        NAMES the judge's clearance — a withdrawn conviction is recorded, never
        silent);
      * judge says SUBSTITUTED / abstains / times out / unavailable → the
        deterministic behavior stands (append the disclosure) — the fail-safe
        direction keeps the protection, exactly as the grounding gate's
        judge-unavailable path annotates rather than passes.
    The precise ``trigger=reject`` path is never judge-gated: an observed
    REJECT-then-retry is not a heuristic. Judge cost rides the SAME deferred
    sink as the grounding gate's escalation (one canonical cost path)."""
    text, ev = run_honesty(
        answer_text, tool_calls, profile=profile, profile_class=profile_class,
        user_uuid=user_uuid, session_id=session_id, turn_id=turn_id, query=query,
        bridges=bridges, support_text=support_text)
    try:
        if not (ev and ev.get("kind") == "grain_substitution"
                and ev.get("trigger") == "entity"):
            return text, ev
        if not _adapter.wants_grain_judge(profile):
            return text, ev
        passed, reason, judged = await _adjudicate_grain(
            query=query, answer_text=answer_text, tool_calls=tool_calls,
            profile_class=profile_class, user_uuid=user_uuid,
            session_id=session_id, turn_id=turn_id, event_handler=event_handler)
        if judged and passed is True:
            # The judge cleared the heuristic — withdrawn, and SAID so (a silent
            # withdrawal would make the same turn read differently on reload
            # with no explanation).
            review = _adapter.wants_review(profile)
            mode = _adapter.resolve_mode(profile)
            return answer_text, _stamp(
                "sql", ran=True, action="pass",
                mode=_modes.stamped_mode(review, mode),
                judge_cleared=True, judge_reason=(reason or "")[:400],
                reason="grain heuristic fired; the grain_faithfulness judge "
                       "ruled the answer faithful — verdict withdrawn")
        ev["judged"] = judged
        ev["judge_passed"] = passed
        if reason:
            ev["judge_reason"] = str(reason)[:400]
        return text, ev
    except Exception as e:  # the judge layer must never break a turn
        app_logger.debug(f"[honesty] grain judge escalation failed (fail-safe): {e}")
        return text, ev


async def _adjudicate_grain(*, query, answer_text, tool_calls, profile_class,
                            user_uuid, session_id, turn_id, event_handler):
    """Ask the grain_faithfulness judge panel about ONE flagged answer. Returns
    ``(passed, reason, judged)`` — ``judged`` iff a real panel ruled; timeout /
    empty panel / error → ``(None, reason, False)``. Mirrors the grounding
    gate's ``_enforce_with_judge``: bounded by the online-eval timeout, cost
    DEFERRED via the one canonical sink (the turn is not persisted yet, so
    billing inline would break turn totals — H5)."""
    import asyncio as _asyncio
    from trusted_data_agent.core.config import APP_CONFIG
    from trusted_data_agent.eval.types import RunRecord, EvalCase
    from trusted_data_agent.eval.judge import build_panel
    from trusted_data_agent.eval.scorers.judges import GrainFaithfulnessJudge

    run = RunRecord({"final_summary_text": answer_text or "",
                     "execution_trace": [
                         {"action": {"tool_name": c.get("tool_name"),
                                     "arguments": c.get("arguments") or {}},
                          "result": c.get("result")}
                         for c in (tool_calls or []) if isinstance(c, dict)]})
    case = EvalCase(id="honesty-grain", query=query or "",
                    profile_class=profile_class or "tool_enabled")
    jv = None
    panel = await build_panel(user_uuid=user_uuid)
    if not (panel and len(panel)):
        return None, "no judge panel available", False
    _timeout = getattr(APP_CONFIG, "ONLINE_EVAL_TIMEOUT_SECONDS", 25)
    try:
        jv = await _asyncio.wait_for(
            GrainFaithfulnessJudge().score_async(case, run, panel), timeout=_timeout)
        return jv.passed, jv.explanation or "", True
    except _asyncio.TimeoutError:
        return None, "judge timed out", False
    except Exception as e:  # noqa: BLE001
        return None, f"judge failed: {type(e).__name__}", False
    finally:
        # Bill whatever the panel actually spent, even on timeout — the same
        # deferred sink as the grounding gate (the spend is real either way).
        try:
            from trusted_data_agent.agent.grounding.runtime import _attribute_judge_cost
            await _attribute_judge_cost(panel, user_uuid, session_id, turn_id,
                                        event_handler, judge_verdict=jv)
        except Exception as e:  # noqa: BLE001
            app_logger.debug(f"[honesty] grain judge cost attribution skipped: {e}")


def run_honesty_rag(
    answer_text: str,
    *,
    qualifying_chunks: int,
    scores=(),
    top_score: float = 0.0,
    searched: bool = False,
    support_texts=(),
    unreadable_material: bool = False,
    profile: Optional[Dict[str, Any]] = None,
    profile_class: str = "rag_focused",
    user_uuid: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Any = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """RAG Trust Hardening Wave 1 §1.3 — retrieval-sufficiency honesty. The knowledge
    twin of ``run_honesty``: when retrieval ran and found (near-)nothing yet the answer
    asserts specifics, flag (observe/annotate) or replace with the honest
    "the knowledge base does not cover this" form (enforce; never new content).

    Returns ``(text, event | None)``; abstains (byte-identical) when retrieval didn't run,
    found sufficient support, or the answer makes no extractable claims. The optional
    per-profile ``knowledgeConfig.sufficiencyThreshold`` tightens the condition (S2 —
    off by default: absolute similarity thresholds are backend-relative).

    ``support_texts`` / ``unreadable_material`` — what the turn's attachments gave the model
    (``PlanExecutor.attached_material``): a specific the attached documents carry is not a
    knowledge-base claim; empty / False ⇒ byte-identical to before."""
    try:
        mode = _adapter.resolve_mode(profile)
        review = _adapter.wants_review(profile)
        if mode == "off":
            return answer_text, _stamp("rag", ran=False, action="off",
                                       reason="not enabled for this agent")
        threshold = None
        try:
            kc = (profile or {}).get("knowledgeConfig") or {}
            if kc.get("sufficiencyThreshold") is not None:
                threshold = float(kc.get("sufficiencyThreshold"))
        except (TypeError, ValueError):
            threshold = None
        # ONE assessment, two lanes (symmetry map A7). The assurance registry resolves who
        # assesses `retrieval_sufficiency`; the gate keeps the REMEDY, which is its own
        # job and the only half that can move an answer. Falls back to the gate's own
        # composition when the property is unbound, so a broken binding degrades to
        # today's behaviour rather than silently disarming the gate.
        _findings = None
        try:
            from trusted_data_agent.agent import assurance as _A
            from trusted_data_agent.agent.assurance import registry as _reg
            from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
            from trusted_data_agent.agent.assurance.contract import AssessmentContext as _Ctx
            _A.bootstrap()
            _asr = _reg.resolve("retrieval_sufficiency", _Lane.IN_FLIGHT)
            if _asr is not None and scores:
                _f = _asr.run(_Ctx(answer=answer_text, retrieval_scores=tuple(scores),
                                   config={"knowledgeConfig": (
                                       {"sufficiencyThreshold": threshold}
                                       if threshold is not None else {})}))
                if not _f.abstained:
                    _findings = _gate.scan_rag_sufficiency(
                        answer_text,
                        qualifying_chunks=int(_f.metrics.get("qualifying_chunks", 0)),
                        top_score=float(_f.metrics.get("top_score", 0.0)),
                        sufficiency_threshold=threshold, searched=True,
                        support_texts=support_texts, unreadable_material=unreadable_material)
        except Exception:  # noqa: BLE001 — a gate must never break on wiring
            _findings = None
        if _findings is None:
            _findings = _gate.scan_rag_sufficiency(
                answer_text, qualifying_chunks=qualifying_chunks, top_score=top_score,
                sufficiency_threshold=threshold, searched=searched,
                support_texts=support_texts, unreadable_material=unreadable_material)
        verdict = _gate.decide_rag(_findings)
        if verdict is None:
            return answer_text, _stamp("rag", ran=True, action="pass",
                                       mode=_modes.stamped_mode(review, mode))
        if mode == "enforce":
            event = _gate.build_event_rag(verdict, mode, True, qualifying_chunks, top_score)
            if review:
                event["mode"] = "review"
                review_id = _park_for_review(
                    source="honesty_rag", original_text=answer_text,
                    delivered_text=verdict.honest_message, reason=event.get("reason") or "",
                    profile=profile, user_uuid=user_uuid, session_id=session_id,
                    turn_id=turn_id)
                if review_id:
                    event["review_id"] = review_id
            # `_acted` like every other branch: the engines render the replacement into
            # the delivered HTML and emit the live card only for an ACTED event — without
            # it an enforce turn stored the honest text but showed the original answer.
            return verdict.honest_message, _acted(event, "rag")
        event = _gate.build_event_rag(verdict, mode, False, qualifying_chunks, top_score)
        if mode == "annotate":
            # the shared ladder: annotate APPENDS the disclosure (observe records, enforce
            # replaces) — it once behaved like observe here, flagging without a word
            event["decision"] = "disclosed"
            event["disclosure_appended"] = True
            return (answer_text.rstrip() + "\n\nNote: " + verdict.honest_message,
                    _acted(event, "rag"))
        return answer_text, _acted(event, "rag")
    except Exception as e:  # never break a turn
        app_logger.debug(f"[honesty/rag] gate error (fail-open): {e}")
        return answer_text, _stamp("rag", ran=True, action="error")


def run_honesty_citation(
    answer_text: str,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    *,
    known_anchors=(),
    events=(),
    vocabulary=(),
    valid_identifiers=(),
    vocabulary_label: str = "identifier",
    profile: Optional[Dict[str, Any]] = None,
    profile_class: str = "llm_only",
    user_uuid: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Any = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """F-001 — the CITED-BUT-UNREAD post-condition. Deterministic, 0-token, fail-open.

    The third honesty branch, and its own branch for the reason the ``_stamp`` note gives
    for the first two: it applies to a different population again (agents carrying a
    progressive skill). Folding it into ``sql`` would report a library turn as though the
    SQL branch had cleared it.

    Modes follow the shared ladder. The act is an APPEND, never a replacement — see
    ``_unread_citation_message`` — so ``enforce`` and ``annotate`` do the same thing here
    and ``review`` parks the pre-disclosure original for the owner.

    Returns ``(text, event)``. Abstains byte-identically when the profile carries no
    progressive library, when the answer cites nothing, or when every citation is backed.
    """
    try:
        if not known_anchors and not vocabulary:
            # Structurally inapplicable — this agent has no library to cite. Distinct
            # from "checked and clean": a stamp claiming a pass would put a reading on a
            # turn where the question never arose.
            return answer_text, None
        mode = _adapter.resolve_mode(profile)
        review = _adapter.wants_review(profile)
        if mode == "off":
            return answer_text, _stamp("citation", ran=False, action="off",
                                       reason="not enabled for this agent")
        # ONE assessment, two lanes. The registry resolves WHO assesses
        # `citation_provenance`; the gate keeps the REMEDY, which is its own job and the
        # only half that can move an answer. Falls back to the gate's own composition
        # when the property is unbound, so a broken binding degrades to today's
        # behaviour rather than silently disarming the gate.
        verdict = None
        _decided = False
        try:
            from trusted_data_agent.agent import assurance as _A
            from trusted_data_agent.agent.assurance import registry as _reg
            from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
            from trusted_data_agent.agent.assurance.contract import AssessmentContext as _Ctx
            _A.bootstrap()
            _asr = _reg.resolve("citation_provenance", _Lane.IN_FLIGHT)
            if _asr is not None:
                _f = _asr.run(_Ctx(answer=answer_text or "",
                                   tool_calls=tuple(tool_calls or ()),
                                   config={"known_anchors": list(known_anchors)}))
                if not _f.abstained:
                    _ev = {e.split(":", 1)[0].strip():
                           [x.strip() for x in e.split(":", 1)[1].split(",") if x.strip()]
                           for e in (_f.evidence or ()) if ":" in e}
                    verdict = _gate.decide_unread_citation(
                        _ev.get("cited") or [], _ev.get("unread") or [])
                    _decided = True
        except Exception:  # noqa: BLE001 — a gate must never break on wiring
            _decided = False
        if not _decided:
            verdict = _gate.assess_unread_citation(
                answer_text, tool_calls or [], known_anchors, events or [])
        disclose = mode in ("annotate", "enforce")
        if verdict is None:
            # No unbacked citation. Now the OTHER provenance failure, and the one the
            # citation check structurally cannot see: an identifier the answer presents as
            # established that the corpus does not contain (F-002, session ab53a8f6 —
            # five decisions settled, four of the ids invented, not one read claim made).
            fab = _gate.assess_fabricated_identifier(
                answer_text, vocabulary, valid_identifiers, vocabulary_label)
            if fab is None:
                return answer_text, _stamp("citation", ran=True, action="pass",
                                           mode=_modes.stamped_mode(review, mode))
            ev_f = _gate.build_event_fabricated(fab, mode, disclosed=disclose)
            if not disclose:
                return answer_text, _acted(ev_f, "citation")
            if review:
                ev_f["mode"] = "review"
                rid = _park_for_review(
                    source="honesty_citation", original_text=answer_text,
                    delivered_text=answer_text + fab.honest_message,
                    reason=ev_f.get("reason") or "", profile=profile,
                    user_uuid=user_uuid, session_id=session_id, turn_id=turn_id)
                if rid:
                    ev_f["review_id"] = rid
            return answer_text + fab.honest_message, _acted(ev_f, "citation")
        # PRECEDENCE, stated: the citation verdict wins when both could fire. Its
        # disclosure already tells the reader that statements attributed to the cited
        # sections are unverified — which covers an invented id inside them — whereas the
        # identifier note alone would not mention the unbacked citation at all.
        event = _gate.build_event_citation(verdict, mode, disclosed=disclose)
        if not disclose:
            return answer_text, _acted(event, "citation")
        if review:
            event["mode"] = "review"
            review_id = _park_for_review(
                source="honesty_citation", original_text=answer_text,
                delivered_text=answer_text + verdict.honest_message,
                reason=event.get("reason") or "", profile=profile,
                user_uuid=user_uuid, session_id=session_id, turn_id=turn_id)
            if review_id:
                event["review_id"] = review_id
        return answer_text + verdict.honest_message, _acted(event, "citation")
    except Exception as e:  # never break a turn
        app_logger.debug(f"[honesty/citation] gate error (fail-open): {e}")
        return answer_text, _stamp("citation", ran=True, action="error",
                                   error=type(e).__name__)
