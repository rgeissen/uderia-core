"""Online evaluation (WS3 W3.4) — tail-sampled, in-production quality scoring.

The same deterministic scorers that gate offline runs also grade a *sample* of
live turns, so quality regressions surface from real traffic — not only from the
curated golden set. Design constraints (mirrors the next-step recommender seam):

  - **Never blocks, never mutates a turn.** Fully wrapped; any failure is logged
    and swallowed. Deterministic + metric scorers only (no LLM, no added latency).
  - **Tail sampling.** Anomalous turns (errors / heavy self-correction) are always
    scored; the rest are sampled at a low rate.
  - **Quality-annotated, OTLP-shaped export.** Each scored turn is emitted as a
    vendor-neutral span dict (attributes = per-dimension verdicts) through a
    pluggable exporter — swap the default JSONL sink for a real OTLP exporter
    without touching the hook.
  - **Signed attestation (EPC tie-in).** The verdict digest is bound to the turn's
    provenance chain-tip and signed with the Ed25519 provenance key, so a quality
    annotation is tamper-evident and auditable alongside the trajectory it grades.
"""
from __future__ import annotations

import os
import asyncio
import json
import time
import base64
import hashlib
import logging
import random
from abc import ABC, abstractmethod
from typing import Optional

from trusted_data_agent.eval.gate_outcome import gate_attributes

logger = logging.getLogger("quart.app")


# ── exporter abstraction ──────────────────────────────────────────────────────
class OnlineEvalExporter(ABC):
    @abstractmethod
    def export(self, span: dict) -> None:
        """Ship one OTLP-shaped span record."""


class JsonlExporter(OnlineEvalExporter):
    """Append spans as NDJSON. The durable online sink by default."""
    def __init__(self, path: str):
        self.path = path

    def export(self, span: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(span, default=str) + "\n")


class MemoryExporter(OnlineEvalExporter):
    """Collects spans in memory — for tests."""
    def __init__(self):
        self.spans: list = []

    def export(self, span: dict) -> None:
        self.spans.append(span)


_exporter: Optional[OnlineEvalExporter] = None


def set_exporter(exporter: Optional[OnlineEvalExporter]) -> None:
    global _exporter
    _exporter = exporter


def _get_exporter() -> OnlineEvalExporter:
    global _exporter
    if _exporter is None:
        from trusted_data_agent.core.config import APP_CONFIG
        _exporter = JsonlExporter(getattr(APP_CONFIG, "ONLINE_EVAL_SINK", "tda_eval/online_eval.jsonl"))
    return _exporter


# ── judge sampling ─────────────────────────────────────────────────────────────
# Deterministic scoring is free, so EVERY turn/run is scored when enabled. Only the
# LLM-judge layer (token cost) is stochastic — gated by should_judge.
def default_assertions() -> dict:
    """Admin-set global default thresholds → applied as the case's assertions for
    live/manual scoring so threshold scorers gate (and band) instead of abstaining.

    Delegates to ``core.config.resolved_thresholds`` — the ONE flattener, beside the
    value it reads. The rule used to live here alone, and the grounding gate (the other
    consumer of the same admin map) never went through it: it was handed the raw nested
    shape, failed to parse it, and silently fell back to its hardcoded default while
    these assertions carried the admin's number. Same key, two answers."""
    from trusted_data_agent.core.config import resolved_thresholds
    return resolved_thresholds()


def _is_anomalous(run) -> bool:
    from trusted_data_agent.core import turn_states as _ts
    # a turn that stopped to ask (PROP-011) is a clean stop, not an anomaly
    if not (_ts.is_success(run.status) or _ts.is_interrupted(run.status)) or not run.is_valid or run.error_message:
        return True
    return getattr(run, "self_correction_count", 0) >= 2


def deterministic_flagged(verdicts) -> bool:
    """Did the free deterministic layer showcase a problem — a failed gating check
    or a danger-band metric? The judge trigger the policy intends: judges refine
    what a code grader flagged; a turn every deterministic check calls clean earns
    a judge only through the (opt-in) random sample."""
    for v in verdicts or []:
        if getattr(v, "passed", None) is False:
            return True
        if getattr(v, "band", None) == "danger":
            return True
    return False


def should_judge(run, *, judge_rate: float, always_anomalous: bool,
                 rng: Optional[random.Random] = None,
                 flagged: bool = False) -> tuple[bool, bool]:
    """Decide whether the (token-costing) judges run for this turn. Deterministic
    scoring is unconditional — this only gates judges. Returns (judge, anomalous).

    ``flagged`` = the deterministic verdicts showcased a problem (a failed check or
    a danger band). With ``always_anomalous`` it triggers judges exactly like a
    run-level anomaly — so ``judge_rate: 0`` now means the honest policy "judges
    only when something is wrong": previously the trigger saw only status/error/
    self-corrections and a deterministic danger verdict earned no judge at rate 0."""
    anomalous = _is_anomalous(run)
    if always_anomalous and (anomalous or flagged):
        return True, anomalous
    r = (rng or random).random()
    return (r < max(0.0, min(1.0, judge_rate))), anomalous


# ── signed attestation (EPC tie-in) ──────────────────────────────────────────
def _verdict_digest(verdicts: list) -> str:
    canonical = json.dumps(
        [{"d": v.dimension, "p": v.passed, "s": v.score} for v in verdicts],
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sign_with_deployment_key(payload: str):
    """``(signature_b64, fingerprint, signed)`` through the one signer seam (PROP-009 §4.6,
    ``core/signing.py``). The default local signer is the key file — byte-identical to the
    direct key call. A signer that fails or returns a wrong signature yields none, never a
    bad one."""
    try:
        from trusted_data_agent.core.signing import get_signer
        signer = get_signer()
        signature = signer.sign_b64(payload)
        return signature, signer.fingerprint(), bool(signature)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"signing unavailable: {e}")
        return "", None, False


def sign_payload(payload: str) -> dict:
    """Sign an arbitrary payload string with the provenance Ed25519 key.

    The shared signing substrate behind both turn-verdict attestations and
    governance attestations (Tier 4.3 publish). Degrades gracefully (signed=False,
    signature="") if the key is unavailable. Returns the verifiable envelope.
    """
    signature, fingerprint, signed = _sign_with_deployment_key(payload)
    return {"payload": payload, "key_fingerprint": fingerprint,
            "signature": signature, "signed": signed}


def emit_governance_span(event: dict) -> None:
    """Ship one governance lifecycle event through the online-eval exporter
    (the same OTLP-shaped sink as turn-quality spans). Never raises."""
    try:
        _get_exporter().export(event)
    except Exception as e:
        logger.debug(f"emit_governance_span: export failed: {e}")


def sign_attestation(turn_tip_hash: Optional[str], verdicts: list,
                     profile_class: str) -> dict:
    """Bind the verdict digest to the turn's chain-tip and sign it (Ed25519).

    Degrades gracefully (signature="" + signed=False) if the provenance key is
    unavailable — the attestation is still emitted, just unsigned.
    """
    digest = _verdict_digest(verdicts)
    payload = f"{turn_tip_hash or ''}|{digest}|{profile_class}"
    signature, fingerprint, signed = _sign_with_deployment_key(payload)
    return {
        "turn_tip_hash": turn_tip_hash,
        "verdict_digest": digest,
        "profile_class": profile_class,
        "key_fingerprint": fingerprint,
        "signature": signature,
        "signed": signed,
    }


# ── OTLP-shaped span ──────────────────────────────────────────────────────────
def _span_id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


async def record_blocked_turn(*, user_uuid: str, session_id: str, turn_id,
                              profile: Optional[dict] = None,
                              invocation: str = "direct") -> None:
    """I1 — emit a GATE-ONLY span for a turn the guard refused.

    A refused turn is deliberately never scored: feeding an injected prompt to a judge
    hands the attack a second door. But "not scored" became "never happened" — the turn
    was absent from the span population entirely, so GUARDED read `11/11 clean` on a day
    the guard refused an attack (#50). The measurement is legitimately missing; the
    EVENT is not.

    So this span carries the gate outcome and NOTHING else: no dimensions, no verdicts,
    no attestation. `eval.gate_only` marks it so the aggregator counts it toward the
    gate's record while excluding it from `turns`/`series`/pass-rate — a refused turn
    must never dilute a quality denominator it was never measured against.

    Fail-open and best-effort, like every other observability path here.
    """
    try:
        from trusted_data_agent.core import session_manager
        sess = await session_manager.get_session(user_uuid, session_id)
        wf = ((sess or {}).get("last_turn_data") or {}).get("workflow_history") or []
        turn = None
        if turn_id is not None:
            turn = next((t for t in wf if t.get("turn") == turn_id), None)
        if turn is None and wf:
            turn = wf[-1]
        attrs = gate_attributes(turn)
        if not attrs:
            return  # nothing was recorded — emit nothing rather than an empty marker
        prof = profile or {}
        span = {
            "name": "agent.turn.eval",
            "attributes": {
                "eval.gate_only": True,
                "eval.profile_class": prof.get("profile_type") or (turn or {}).get("profile_type"),
                "agent.profile_tag": prof.get("tag") or (turn or {}).get("profile_tag"),
                "agent.profile_id": prof.get("id") or (turn or {}).get("profile_id"),
                "agent.invocation": invocation,
                "user.uuid": user_uuid,
                "session.id": session_id,
                "turn.id": str(turn_id) if turn_id is not None else None,
                **attrs,
            },
        }
        _get_exporter().export(span)
    except Exception as e:  # noqa: BLE001 — must never affect the turn
        logger.debug(f"online-eval: blocked-turn span failed: {e}")


def gating_failures(verdicts: list) -> int:
    """#43 — how many GATING dimensions failed. The one definition, used by every
    span builder.

    `Verdict.gating` exists precisely so an advisory signal can inform without
    convicting: `grounded_overlap` is a coarse lexical pre-signal whose own docstring
    calls the LLM judge its refiner, and every LLM judge is non-gating until its
    Cohen's-kappa calibration clears. Both span builders summed `passed is False`
    over ALL verdicts, so those advisory dimensions failed whole turns — and that
    number is the denominator behind JUDGED, the roster pass-rate, the sparklines and
    the Behavioral Trust Score.

    Live proof: the Redis turn read "failed" via advisory `grounded_overlap` and
    `context_utilization` alone. It SHOULD have failed — the grounding gate blocked it
    — but it failed for the wrong reason, and a turn blocked by a gate with no failing
    gating dimension would have read clean. A runtime gate block is now itself a gating
    failure, which is what makes the count mean what its name says.

    `_summary()` has always filtered correctly; the span builders did not. Same rule,
    unswept axis — the third instance of that shape this session.
    """
    from trusted_data_agent.eval.scorer import is_gating_dimension
    return sum(1 for v in verdicts
               if getattr(v, "passed", None) is False
               # Prefer an explicitly-set verdict flag; otherwise resolve the scorer's
               # own declaration from the registry. `Verdict.gating` defaults True and
               # no construction site sets it, so reading the verdict alone made every
               # advisory dimension gate.
               and (getattr(v, "gating", True) is not False)
               and is_gating_dimension(getattr(v, "dimension", None)))


def build_quality_span(*, session_id: str, turn_id, profile_class: str,
                       verdicts: list, sampled: bool, anomalous: bool,
                       attestation: dict, started_at: float,
                       profile_tag: Optional[str] = None,
                       profile_id: Optional[str] = None,
                       user_uuid: Optional[str] = None,
                       invocation: str = "direct",
                       experts: Optional[list] = None,
                       parent_session_id: Optional[str] = None,
                       library_use: Optional[dict] = None) -> dict:
    """Vendor-neutral OTLP-style span with per-dimension quality attributes.

    Carries the agent identity (profile tag/id) so the Agent-performance lens can
    aggregate quality per individual agent, and the owning user so My-vs-System
    scoping can filter to the requesting user's own agents. ``invocation`` is
    'direct' (user invoked the agent) or 'expert' (the agent ran as a genie
    coordinator's sub-agent) — lets the roster distinguish the two without
    double-counting (self-cost accounting; see Agent Ops & Eval §12a)."""
    attrs = {
        "eval.profile_class": profile_class,
        "agent.profile_tag": profile_tag,
        "agent.profile_id": profile_id,
        "agent.invocation": invocation,
        # Coordinator→expert linkage for the master/slave trace tree: coordinator
        # spans carry the per-expert summary (tag/session/status/cost/timing);
        # expert spans carry their parent coordinator session.
        "agent.parent_session_id": parent_session_id,
        "eval.experts": experts or None,
        "eval.expert_count": len(experts) if experts else 0,
        "user.uuid": user_uuid,
        "eval.sampled": sampled,
        "eval.anomalous": anomalous,
        "eval.dimensions": len(verdicts),
        "eval.gating_failures": gating_failures(verdicts),
        # Kept alongside, so a dimension that merely wants a look is still visible.
        "eval.advisory_failures": sum(
            1 for v in verdicts if getattr(v, "passed", None) is False
            and getattr(v, "gating", True) is False),
        "session.id": session_id,
        "turn.id": str(turn_id) if turn_id is not None else None,
        "attestation.signed": attestation.get("signed"),
        "attestation.verdict_digest": attestation.get("verdict_digest"),
    }
    for v in verdicts:
        attrs[f"eval.dim.{v.dimension}.passed"] = v.passed
        if v.score is not None:
            attrs[f"eval.dim.{v.dimension}.score"] = round(float(v.score), 4)
        # Task composition split (e.g. "1 tool", "2 reasoning") → per-class counts,
        # so the Agent-performance lens can aggregate average composition per agent.
        if v.dimension == "tasks" and getattr(v, "evidence", None):
            for part in v.evidence:
                try:
                    n_str, cls = str(part).split(" ", 1)
                    attrs[f"eval.task.{cls}"] = int(n_str)
                except (ValueError, AttributeError):
                    pass
    # Read → ran per library section (`skills.library_use.record`), flattened so the
    # per-section aggregate (`analysis.library_outcomes`) reads spans like every other
    # Agent-performance view. Absent record ⇒ no attributes ⇒ identical span.
    if isinstance(library_use, dict):
        for anchor, cells in (library_use.get("sections") or {}).items():
            for cell, n in (cells or {}).items():
                if n:
                    attrs[f"eval.library.{anchor}.{cell}"] = int(n)
        for anchor in library_use.get("read_unused") or []:
            attrs[f"eval.library.{anchor}.read_unused"] = 1
    return {
        "name": "agent.turn.eval",
        "trace_id": _span_id(session_id, turn_id),
        "span_id": _span_id(session_id, turn_id, "eval"),
        "start_time_unix_nano": int(started_at * 1e9),
        "end_time_unix_nano": int(time.time() * 1e9),
        "attributes": attrs,
        "events": [{"name": "verdict", "attributes": v.to_dict()} for v in verdicts],
        "attestation": attestation,
    }


def build_flow_quality_span(*, run_id, flow_id, flow_name, profile_tag, verdicts: list,
                            sampled: bool, anomalous: bool, attestation: dict,
                            started_at: float, user_uuid=None) -> dict:
    """OTLP-style span for a flow-agent run. Keyed by flow identity (a flow is its
    own agent), with `agent.kind='flow'` so the roster lists it distinctly."""
    attrs = {
        "eval.profile_class": "flow",
        "agent.kind": "flow",
        "agent.flow_id": flow_id,
        "agent.flow_name": flow_name,
        "agent.profile_tag": profile_tag,   # effective run profile (may be None / one of many)
        "user.uuid": user_uuid,
        "eval.sampled": sampled,
        "eval.anomalous": anomalous,
        "eval.dimensions": len(verdicts),
        "eval.gating_failures": gating_failures(verdicts),
        "flow.run_id": run_id,
        "attestation.signed": attestation.get("signed"),
        "attestation.verdict_digest": attestation.get("verdict_digest"),
    }
    for v in verdicts:
        attrs[f"eval.dim.{v.dimension}.passed"] = v.passed
        if v.score is not None:
            attrs[f"eval.dim.{v.dimension}.score"] = round(float(v.score), 4)
        # Task composition split (e.g. "1 tool", "2 reasoning") → per-class counts,
        # so the Agent-performance lens can aggregate average composition per agent.
        if v.dimension == "tasks" and getattr(v, "evidence", None):
            for part in v.evidence:
                try:
                    n_str, cls = str(part).split(" ", 1)
                    attrs[f"eval.task.{cls}"] = int(n_str)
                except (ValueError, AttributeError):
                    pass
    return {
        "name": "agent.flow.eval",
        "trace_id": _span_id(flow_id, run_id),
        "span_id": _span_id(flow_id, run_id, "eval"),
        "start_time_unix_nano": int(started_at * 1e9),
        "end_time_unix_nano": int(time.time() * 1e9),
        "attributes": attrs,
        "events": [{"name": "verdict", "attributes": v.to_dict()} for v in verdicts],
        "attestation": attestation,
    }


async def evaluate_flow_run_online(*, user_uuid: str, flow_id: str, run_id: str,
                                   flow_name: str = None, profile_tag: str = None,
                                   event_handler=None, rng=None) -> Optional[dict]:
    """Tail-sample and score a completed flow run (deterministic flow scorers).
    NEVER raises. Off unless ONLINE_EVAL_ENABLED."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        if not getattr(APP_CONFIG, "ONLINE_EVAL_ENABLED", False):
            return None
        from trusted_data_agent.core import flow_db
        from trusted_data_agent.eval.flow_types import FlowRunRecord
        from trusted_data_agent.eval.types import EvalCase
        from trusted_data_agent.eval.runner import score_run_record

        run = flow_db.get_run(run_id)
        if not run:
            return None
        try:
            run["node_runs"] = flow_db.get_node_runs(run_id) or run.get("node_runs") or []
        except Exception:
            run["node_runs"] = run.get("node_runs") or []
        rec = FlowRunRecord(run)

        # Flows are graded by deterministic flow scorers only (no judges), so every
        # run is scored when enabled — there's nothing token-costing to sample.
        anomalous = (rec.status != "complete") or bool(rec.failed_nodes())

        case = EvalCase(id=f"online-flow-{run_id}", query=flow_name or flow_id,
                        profile_class="flow", flow_id=flow_id, assertions=default_assertions())
        started = time.time()
        verdicts = await score_run_record(case, rec)

        # Flows have no per-run EPC chain tip; bind the attestation to the run id.
        attestation = sign_attestation(f"flow:{run_id}", verdicts, "flow")
        span = build_flow_quality_span(
            run_id=run_id, flow_id=flow_id, flow_name=flow_name, profile_tag=profile_tag,
            verdicts=verdicts, sampled=True, anomalous=anomalous,
            attestation=attestation, started_at=started, user_uuid=user_uuid)
        try:
            _get_exporter().export(span)
        except Exception as e:
            logger.warning(f"online-eval(flow): export failed: {e}")

        summary = _summary(verdicts)
        summary.update({"sampled": True, "anomalous": anomalous, "agent_kind": "flow",
                        "flow_id": flow_id, "signed": attestation["signed"]})
        if event_handler is not None:
            try:
                await event_handler(
                    {"type": "online_eval", "payload": {
                        "flow_id": flow_id, "run_id": run_id, "agent_kind": "flow", **summary,
                        "verdicts": [v.to_dict() for v in verdicts]}}, "notification")
            except Exception:
                pass
        return summary
    except Exception as e:
        logger.warning(f"online-eval(flow) hook failed (run unaffected): {e}")
        return None


def _summary(verdicts: list) -> dict:
    gating = [v for v in verdicts if getattr(v, "gating", True) is not False]
    failures = [v.dimension for v in verdicts if v.passed is False]
    return {
        "dimensions": len(verdicts),
        "passed": all(v.passed for v in gating if v.passed is not None),
        "failures": failures,
    }


_SCORE_CALL_ID = "turn_score"

# Monotonic nonce so each judge RUN gets a distinct ledger call_id. The frontend live-cost
# ledger keys entries by call_id: identical id = idempotent re-delivery (a single run's
# token_update reaches the browser via BOTH the request stream and the notification channel), but
# a CONSTANT id across different runs made a second judge run (grounding + online same turn, or a
# re-score) overwrite the first in the session ledger — dropping all but the latest judge spend
# from the live display. A per-run id fixes the collapse while keeping dual-delivery idempotent.
import itertools as _itertools
_SCORE_CALL_SEQ = _itertools.count(1)


def _next_score_call_id(turn_id) -> str:
    return f"{_SCORE_CALL_ID}:{turn_id}:{next(_SCORE_CALL_SEQ)}"

# Deferred judge-cost accounting (H5). The grounding gate's judge runs INLINE during answer
# assembly — BEFORE the turn is persisted to workflow_history — so accounting it there loses the
# turn-total billing and the persisted judge step (the turn row doesn't exist yet), while session
# totals still increment → session ≠ Σ turns. So the grounding path stashes its usage here and
# execution_service flushes it AFTER the turn is saved. Keyed by (user_uuid, session_id); turns
# are sequential per session, so a flush drains that session's pending entries safely.
_DEFERRED_JUDGE_USAGE: dict = {}


def defer_judge_usage(user_uuid, session_id, turn_id, in_tok, out_tok, cost_usd,
                      *, judge_models=None, judge_usage=None, judge_verdicts=None,
                      profile_class=None, layer="in_flight") -> None:
    """Stash judge usage to be flushed after the turn is persisted (grounding-gate path).

    ``layer`` defaults to ``in_flight`` because the deferral EXISTS for the in-path
    case — a gate's judge rules before the turn is saved. It is still carried per
    entry rather than assumed at the flush, so a future deferring caller declares its
    own layer instead of inheriting one that happens to be right today."""
    if not (in_tok or out_tok):
        return
    try:
        _DEFERRED_JUDGE_USAGE.setdefault((user_uuid, session_id), []).append({
            "turn_id": turn_id, "in_tok": in_tok, "out_tok": out_tok, "cost_usd": cost_usd,
            "judge_models": judge_models, "judge_usage": judge_usage,
            "judge_verdicts": judge_verdicts,
            "profile_class": profile_class,
            "layer": layer,
        })
    except Exception as e:
        logger.debug(f"defer_judge_usage skipped: {e}")


async def flush_deferred_judge_usage(user_uuid, session_id, event_handler) -> None:
    """Drain and bill any deferred judge usage for this (user, session) now the turn is saved.
    Called from the execution-service post-engine seam. Never raises."""
    try:
        pending = _DEFERRED_JUDGE_USAGE.pop((user_uuid, session_id), None)
    except Exception:
        pending = None
    if not pending:
        return
    for entry in pending:
        try:
            await _record_score_usage(
                user_uuid, session_id, entry["turn_id"], entry["in_tok"], entry["out_tok"],
                entry["cost_usd"], event_handler,
                judge_models=entry.get("judge_models"),
                judge_usage=entry.get("judge_usage"),
                judge_verdicts=entry.get("judge_verdicts"),
                profile_class=entry.get("profile_class"),
                layer=entry.get("layer", "in_flight"))
        except Exception as e:
            logger.debug(f"flush_deferred_judge_usage entry skipped: {e}")


#: Head-room between a single judge's per-call timeout and the cap on the whole judge
#: layer. Judges run concurrently, so the layer is one call window plus panel assembly
#: and parsing; anything less than this makes a single hung model a guaranteed timeout.
_JUDGE_LAYER_MARGIN_SECONDS = 10


def _is_judge_verdict(v) -> bool:
    """Is this a JUDGE-kind verdict? (Verdict objects and already-serialized dicts.)"""
    try:
        if isinstance(v, dict):
            kind = v.get("kind")
        else:
            kind = getattr(getattr(v, "kind", None), "value", None) or getattr(v, "kind", None)
        return str(kind) in ("judge", "ScoreKind.JUDGE")
    except Exception:  # noqa: BLE001 — a malformed verdict is simply not a judge verdict
        return False


def judged_from_record(verdicts: list) -> bool:
    """Does the RECORD hold a judge ruling? — the one rule behind `eval.judged`.

    The runs ledger prints "judged" vs "deterministic scoring" from this flag while
    rendering judge CHIPS from the verdicts, so the two must be derived from the same
    fact or a row can contradict itself. Stated as a function, and used by both the
    online hook and the manual re-score, so neither can drift toward "a panel was
    assembled" — which is a claim about the attempt, not about what can be shown.
    """
    return any(_is_judge_verdict(v) for v in verdicts or [])


def _take_judge_partial(sink: list, verdicts: list) -> int:
    """Move the judge verdicts that completed before a cancellation into ``verdicts``.

    De-duplicated by dimension against what is already there, so a caller that ends up
    holding both the sink and a partially-extended result list cannot record a
    dimension twice. Returns how many were recovered.
    """
    have = {getattr(v, "dimension", None) for v in verdicts}
    kept = 0
    for v in list(sink or []):
        if not _is_judge_verdict(v):
            continue
        dim = getattr(v, "dimension", None)
        if dim in have:
            continue
        have.add(dim)
        verdicts.append(v)
        kept += 1
    return kept


def _mark_judges_cut(profile_class: str, verdicts: list, reason: str) -> int:
    """Record an explicit abstention for every applicable judge the cut-off silenced.

    Without this a timed-out judge simply VANISHES from the record: the span shows the
    dimensions that happened to finish and says nothing about the ones that were
    attempted, consulted a model, and were cancelled. That is the same invisible loss
    the partial-recovery fix exists to end, one level down — a reader cannot distinguish
    "this dimension was never applicable" from "we tried and lost it". An abstention that
    NAMES the cut-off is honest; silence is not. Abstentions never assert a pass.
    """
    try:
        from trusted_data_agent.eval.scorer import scorers_for, ScoreKind as _SK
        from trusted_data_agent.eval.types import Verdict as _V
        have = {getattr(v, "dimension", None) for v in verdicts if _is_judge_verdict(v)}
        added = 0
        for s in scorers_for(profile_class, kinds=[_SK.JUDGE]):
            if s.dimension in have:
                continue
            verdicts.append(_V(dimension=s.dimension, kind=_SK.JUDGE, passed=None,
                               explanation=reason))
            added += 1
        return added
    except Exception as e:  # noqa: BLE001 — observability must never break a turn
        logger.debug(f"online-eval: could not mark cut judges: {e}")
        return 0


def judge_verdict_summary(verdicts: list) -> list:
    """The judge RESULTS for the "Agent Quality Judged" card — final consensus per
    dimension + the per-model votes behind it: ``[{dimension, passed, score?,
    explanation?, votes: [{model, verdict, score?}]}]``.

    The card previously carried only the ACCOUNTING face (models, tokens, cost) —
    a judge run whose ruling was invisible on its own step. Only judge-kind
    verdicts qualify (deterministic scorers have no panel); pure and defensive so
    every caller (online hook, score-turn, grounding gate) summarizes identically.
    """
    out = []
    for v in verdicts or []:
        try:
            if not _is_judge_verdict(v):
                continue
            row = {"dimension": getattr(v, "dimension", "?"),
                   "passed": getattr(v, "passed", None)}
            if getattr(v, "score", None) is not None:
                row["score"] = v.score
            expl = getattr(v, "explanation", "") or ""
            if expl:
                row["explanation"] = expl[:220]
            votes = getattr(v, "votes", None)
            if votes:
                row["votes"] = votes
            out.append(row)
        except Exception:  # noqa: BLE001 — a malformed verdict never breaks the card
            continue
    return out


def judge_usage_lines(judge_usage) -> list:
    """The per-model lines to book, whatever shape the caller holds: the online hook passes the
    ALREADY-broken-down rows (``[{provider, model, input_tokens, output_tokens, cost_usd}]``); a
    raw panel meter is ``[(provider, model, in, out)]``. Breaking the rows down a second time
    dropped every entry, so no judge line was ever booked and the sink fell back to adding the
    panel into the turn's row (found by the first full run after the change: 132 judge runs,
    0 judge lines)."""
    rows = list(judge_usage or [])
    if not rows:
        return []
    if all(isinstance(r, dict) for r in rows):
        out = [dict(r) for r in rows if r.get("model")]
        fn = _judge_cost_fn()
        for r in out:
            if r.get("cost_usd") is None and fn:
                try:
                    r["cost_usd"] = float(fn(r.get("provider"), r.get("model"),
                                             int(r.get("input_tokens") or 0), int(r.get("output_tokens") or 0)))
                except Exception:  # noqa: BLE001
                    r["cost_usd"] = 0.0
        return out
    return judge_usage_breakdown(rows, cost_fn=_judge_cost_fn())


def _judge_cost_fn():
    """The one price function for a judge line (the cost manager's, fallback-aware)."""
    try:
        from trusted_data_agent.core.cost_manager import get_cost_manager
        return get_cost_manager().calculate_cost
    except Exception:  # noqa: BLE001
        return None


def judge_usage_breakdown(usage: list, cost_fn=None) -> list:
    """Per-model judge spend: ``[{model, provider, input_tokens, output_tokens, cost_usd}]``.

    The panel already meters per member (`LLMHandlerPanel.usage`), but only the SUM ever
    reached the UI, so a two-model panel rendered "2 models" and one aggregate figure —
    exactly when composition starts to matter (#46, and #39: a panel of one is
    indistinguishable from a real panel).

    Rows are aggregated per (provider, model) because a panel member is called once per
    judged DIMENSION, so the same model legitimately appears several times. `cost_fn`
    prices a row; omitted, costs are omitted rather than guessed at.
    """
    agg: dict = {}
    for entry in usage or []:
        try:
            prov, model, in_tok, out_tok = entry
        except (TypeError, ValueError):
            continue
        key = (prov or "", model or "")
        row = agg.setdefault(key, {"provider": prov or None, "model": model or None,
                                   "input_tokens": 0, "output_tokens": 0, "calls": 0})
        row["input_tokens"] += int(in_tok or 0)
        row["output_tokens"] += int(out_tok or 0)
        row["calls"] += 1
    out = []
    for row in agg.values():
        if cost_fn:
            try:
                row["cost_usd"] = round(float(cost_fn(row["provider"], row["model"],
                                                      row["input_tokens"],
                                                      row["output_tokens"])), 8)
            except Exception:  # noqa: BLE001 — a pricing gap must not lose the row
                row["cost_usd"] = None
        out.append(row)
    # Costliest first: the reader's question is "what did this panel cost me, and who".
    out.sort(key=lambda r: (-(r.get("cost_usd") or 0), -(r["input_tokens"] + r["output_tokens"])))
    return out


async def _record_score_usage(user_uuid, session_id, turn_id, in_tok, out_tok,
                              cost_usd, event_handler, *, judge_models=None,
                              judge_usage=None, judge_verdicts=None,
                              profile_class=None, layer="post_hoc"):
    """Route the (judge) token cost through the same rigorous path as the
    recommender's Tier C — session totals, turn totals, the idempotent live-KPI
    token_update, and consumption — so the title-bar KPIs reflect it live and the
    turn cost survives a reload. Deterministic scoring is free → no-op. (The
    Quality card itself survives reload via the persisted span / turn-score.)

    It also emits ONE cost-bearing ``judge_evaluation_complete`` Live Status STEP
    per judge run — the transparency element that makes the panel's event costs
    sum to the turn total, persisted so it survives a plan reload. Because this is
    the single canonical sink for every judge path (online hook, grounding gate,
    manual re-score), the step is produced identically for all of them."""
    if not (in_tok or out_tok):
        return  # deterministic — nothing to bill
    from trusted_data_agent.core import session_manager

    # One id for this judge run, shared by both deliveries below (idempotent) but distinct from
    # any other judge run in the session (so the live ledger no longer collapses them).
    call_id = _next_score_call_id(turn_id)

    await session_manager.update_token_count(user_uuid, session_id, in_tok, out_tok)
    refreshed = await session_manager.get_session(user_uuid, session_id)
    total_in = (refreshed or {}).get("input_tokens", 0)
    total_out = (refreshed or {}).get("output_tokens", 0)

    new_turn_in, new_turn_out = in_tok, out_tok
    if turn_id is not None and refreshed:
        wh = refreshed.get("last_turn_data", {}).get("workflow_history", [])
        turn = next((t for t in wh if t.get("turn") == turn_id), None)
        if turn:
            new_turn_in = (turn.get("turn_input_tokens", 0) or 0) + in_tok
            new_turn_out = (turn.get("turn_output_tokens", 0) or 0) + out_tok
        # Add the judge's ACCURATE per-model cost (not a session-model reprice) so the persisted
        # turn_cost stays equal to the sum of the per-event costs the Live Status panel shows.
        # additive=True → the token deltas are accumulated atomically inside the session lock so a
        # concurrent background biller (memory extraction) can't clobber this update.
        await session_manager.update_turn_token_counts(
            user_uuid, session_id, turn_id, in_tok, out_tok,
            added_cost_usd=cost_usd, additive=True)

    if event_handler is not None:
        try:
            await event_handler({
                "statement_input": in_tok, "statement_output": out_tok,
                "turn_input": new_turn_in, "turn_output": new_turn_out,
                "total_input": total_in, "total_output": total_out,
                "call_id": call_id, "cost_usd": cost_usd, "turn_id": turn_id,
                "session_id": session_id,
            }, "token_update")
        except Exception as e:
            logger.debug(f"score usage: token_update skipped: {e}")

    # Live KPI update over the per-user notification channel — the RELIABLE path for
    # judge accounting that runs OUTSIDE an open request stream: the online hook is
    # backgrounded (its /ask_stream event_handler has already closed) and the manual
    # "Score this turn" button is a REST call with no event_handler at all. The
    # token_update carries ABSOLUTE totals (updateTokenDisplay sets, not adds), so
    # delivering it here in addition to the stream is idempotent. Session-tagged so
    # the frontend can guard it — it must never touch another session's counters.
    try:
        from trusted_data_agent.core.config import APP_STATE
        _tok_evt = {
            "statement_input": in_tok, "statement_output": out_tok,
            "turn_input": new_turn_in, "turn_output": new_turn_out,
            "total_input": total_in, "total_output": total_out,
            "call_id": call_id, "cost_usd": cost_usd, "turn_id": turn_id,
            "session_id": session_id,
        }
        _queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
        for _q in list(_queues):
            _q.put_nowait({"type": "token_update", "payload": _tok_evt})   # TC-70: no unreferenced task
    except Exception as e:
        logger.debug(f"score usage: token_update (notification) skipped: {e}")


    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.consumption_manager import ConsumptionManager
        with get_db_session() as db:
            cm_ = ConsumptionManager(db)
            # Each judge model is booked as its OWN line under its own provider/model, priced at its
            # own rate — never added into the turn's row, which billed it to the turn's model.
            # Without a per-model breakdown, the old aggregate booking stands.
            _lines = judge_usage_lines(judge_usage)
            if _lines:
                for ln in _lines:
                    cm_.record_usage_line(
                        user_id=user_uuid, session_id=session_id, turn_number=turn_id or 0,
                        provider=ln.get("provider"), model=ln.get("model"),
                        input_tokens=ln.get("input_tokens") or 0, output_tokens=ln.get("output_tokens") or 0,
                        cost_usd_cents=int(float(ln.get("cost_usd") or 0) * 1000000), kind="judge",
                        user_query=f"[judge · {layer}]")
            else:
                cm_.update_turn_tokens(
                    user_id=user_uuid, session_id=session_id, turn_number=turn_id or 0,
                    additional_input_tokens=in_tok, additional_output_tokens=out_tok,
                    additional_cost_usd_cents=int((cost_usd or 0) * 1000000))
    except Exception as e:
        logger.debug(f"score usage: consumption skipped: {e}")

    # Live Status STEP for the judge run — the transparency element that makes the
    # panel's per-event costs sum to the turn total (charter #17: what the turn was
    # billed must be visible as discrete events), and — persisted below — survives a
    # plan reload. Emitted from the ONE canonical sink so every judge path (online
    # hook, grounding-gate judge, manual "Score this turn") produces exactly one
    # cost-bearing judge step, consistently. No double-count: grounding_gate.to_event()
    # carries no cost, turn_score_complete is not persisted — this is the sole
    # cost-bearing judge event `_getTurnCost` sees.
    try:
        step_evt = {
            "step": ("Gate Consulted a Judge" if layer == "in_flight"
                     else "Agent Quality Judged"),
            "type": "judge_evaluation_complete",
            "details": {
                "session_id": session_id, "turn_id": turn_id,
                "profile_class": profile_class,
                # WHICH judge layer spent this. The same event serves both — the
                # scoring panel and a gate's in-path escalation — and without this
                # they were indistinguishable, though only one of them could change
                # what the user received. Declared by the caller, never inferred.
                "layer": layer if layer in ("in_flight", "post_hoc") else "post_hoc",
                "judge_models": judge_models or [],
                # #46 — per-model tokens + cost, so the panel's composition and its
                # spend are inspectable rather than a single "2 models" summary. Rides
                # the SAME persisted event, so it renders identically on a plan reload;
                # it is display detail INSIDE the one cost-bearing event, never a second
                # one (a second would double-count in `_getTurnCost`).
                "judge_usage": judge_usage or [],
                # The RESULTS face: final consensus per dimension + per-model votes
                # (judge_verdict_summary) — the transparency the card owed. Rides the
                # SAME persisted event, so it renders identically on a plan reload.
                "verdicts": judge_verdicts or [],
                "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost_usd,
                # The summary leads with the RULING (what the panel decided), then the
                # bill — a judgement step whose one-liner only ever said what it cost
                # was the accounting face wearing the whole card.
                "summary": ((lambda _v: (
                    (f"LLM judge panel: "
                     f"{sum(1 for x in _v if x.get('passed') is True)} pass · "
                     f"{sum(1 for x in _v if x.get('passed') is False)} fail"
                     + (f" · {sum(1 for x in _v if x.get('passed') is None)} abstained"
                        if any(x.get('passed') is None for x in _v) else "")
                     if _v else "LLM judge panel evaluated the turn")
                    + f" ({in_tok} in / {out_tok} out, ${(cost_usd or 0):.6f})"))(judge_verdicts or [])),
            },
        }
        # Single LIVE channel = the always-open per-user notification EventSource,
        # which covers every judge caller (backgrounded online hook, REST re-score
        # button, inline grounding-gate judge) with one delivery. Session-tagged so
        # the frontend guards it; NOT also emitted via event_handler → no double-render.
        try:
            from trusted_data_agent.core.config import APP_STATE
            _queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
            for _q in list(_queues):
                _q.put_nowait({"type": "judge_evaluation_complete",
                               "payload": step_evt})   # TC-70: no unreferenced task
        except Exception as e:
            logger.debug(f"score usage: judge step notify skipped: {e}")
        # Persist into the turn so it re-renders in Live Status on a plan reload
        # (and its cost re-sums into the turn total via _getTurnCost).
        if turn_id is not None:
            await session_manager.append_system_events_to_turn(
                user_uuid, session_id, turn_id,
                [{"type": "judge_evaluation_complete", "payload": step_evt}])
    except Exception as e:
        logger.debug(f"score usage: judge step skipped: {e}")


# ── the hook ──────────────────────────────────────────────────────────────────
async def evaluate_turn_online(*, user_uuid: str, session_id: str,
                               profile: Optional[dict], turn_id=None,
                               event_handler=None, rng=None,
                               invocation: str = "direct",
                               parent_session_id: Optional[str] = None,
                               force_judges: bool = False) -> Optional[dict]:
    """Deterministically score the just-completed turn (every turn when enabled);
    the token-costing judges run only on the stochastic sample. NEVER raises.

    ``invocation`` tags the span 'direct' (user-invoked) or 'expert' (a genie
    coordinator's sub-agent), so expert sub-turns are scored for their own quality
    yet distinguished in the roster.

    ``force_judges`` skips the sampling decision and runs the judge layer — used by
    the vote signal when a user downvotes a turn (a human saying "this was wrong" is
    a stronger anomaly signal than any sampler). It does NOT override the profile's
    own judge opt-out, and everything else is unchanged: the same timeouts, the same
    canonical cost sink, the same span. Default False ⇒ byte-identical baseline."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        if not getattr(APP_CONFIG, "ONLINE_EVAL_ENABLED", False):
            return None
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.eval.types import EvalCase, RunRecord
        from trusted_data_agent.eval.runner import score_run_record

        wf = await session_manager.get_workflow_history(user_uuid, session_id)
        if not wf:
            return None
        # Select the turn by id — the hook is backgrounded, so a fast next turn could
        # already have appended; `wf[-1]` would then score the wrong turn. Fall back
        # to the last turn when no id was supplied / no match (legacy behaviour).
        turn = None
        if turn_id is not None:
            turn = next((t for t in wf
                         if str(t.get("turn", t.get("turn_number"))) == str(turn_id)), None)
        if turn is None:
            turn = wf[-1]
        # the attachments the turn read are evidence — read now, through the guarded manifest
        from trusted_data_agent.filestore import session_files as _sf
        turn = await asyncio.to_thread(_sf.with_attachment_evidence, user_uuid, session_id, turn)
        run = RunRecord(turn)
        anomalous = _is_anomalous(run)

        # Deterministic scoring runs unconditionally; judges are the only stochastic
        # part. PROP-002 follow-up — the JUDGED station is per-profile configurable
        # (`judgeConfig`); resolution lives in the pure `resolve_judge_prefs` so it is
        # CI-testable. ONLINE_EVAL_ENABLED stays the hard deployment master (already
        # gated above) — a profile can choose judging for ITS turns, it can never
        # turn scoring on for a deployment that switched it off.
        profile_class = (run.profile_type
                         or (profile or {}).get("profile_type") or "tool_enabled")
        case = EvalCase(id=f"online-{session_id}-{turn_id}", query=turn.get("user_query", ""),
                        profile_class=profile_class, assertions=default_assertions())
        started = time.time()
        # Deterministic scoring FIRST — instant, free, cannot hang — so the Quality
        # bands persist even if the judges stall, AND so the judge decision can read
        # what the deterministic layer found. The policy the reorder enables: judges
        # run when *something is wrong* (a failed check / a danger band / a run-level
        # anomaly), and a clean turn pays for a judge only through the opt-in random
        # sample. Previously the decision preceded scoring, so at rate 0 a turn every
        # deterministic check condemned earned no judge — the exact turn a judge is
        # FOR — while rate 1.0 judged every clean turn to compensate.
        verdicts = await score_run_record(case, run, include_judges=False)

        jprefs = resolve_judge_prefs(
            profile,
            include_default=getattr(APP_CONFIG, "ONLINE_EVAL_INCLUDE_JUDGES", False),
            default_rate=getattr(APP_CONFIG, "ONLINE_EVAL_JUDGE_SAMPLE_RATE", 0.05))
        include_judges = False
        if jprefs["wanted"]:
            if force_judges:
                # A user downvoted this turn (VOTE_SIGNAL_PLAN §11). A human saying
                # "this was wrong" is the strongest anomaly signal there is, so the
                # sampling decision is skipped — but the per-profile opt-out above
                # still wins: an agent whose judges are OFF is never judged, whatever
                # the trigger.
                include_judges = True
            else:
                judge, _ = should_judge(
                    run, judge_rate=jprefs["rate"],
                    always_anomalous=getattr(APP_CONFIG, "ONLINE_EVAL_ALWAYS_ANOMALOUS", True),
                    rng=rng, flagged=deterministic_flagged(verdicts))
                include_judges = judge
        # A turn that FAILED delivered no answer to grade — only the honest sentence naming the
        # failure. The deterministic layer reads it (clean_termination fails); a judge would be
        # paid to grade an error message (trust campaign C18). A downvote does not change that.
        from trusted_data_agent.core import turn_states as _ts
        if _ts.is_failure(run.status) or _ts.is_interrupted(run.status):
            # a failed turn delivered no answer; an interrupted one delivered a QUESTION — neither
            # is an answer a judge should be paid to grade (PROP-011)
            include_judges = False
        panel = None
        judge_fn = None
        # Receives each judge verdict as it completes, so a layer timeout can keep
        # what was already computed (and billed) instead of discarding all of it.
        _judge_partial: list = []
        if include_judges:
            from trusted_data_agent.eval.judge import build_panel
            from trusted_data_agent.eval.scorers.judges import make_judge_fn
            panel = await build_panel(user_uuid=user_uuid)
            if len(panel):
                judge_fn = make_judge_fn(panel, sink=_judge_partial)
            else:
                include_judges = False
        # The judge layer (the only token-costing, network-bound part) is bounded by
        # a hard timeout below: a stalled provider abandons the judges for this turn
        # but never the deterministic score.
        #
        # The budget must be SATISFIABLE, not merely configured. `run_judges` now runs
        # the judges concurrently, so the layer costs about ONE panel call window — but
        # a per-call timeout configured at or above the layer cap would still guarantee
        # a timeout whenever a single model hangs (production shipped 20s inside 25s, and
        # every judge run it ever paid for was discarded). The floor makes the relationship
        # hold by construction rather than by whoever last edited the two numbers.
        if include_judges and judge_fn is not None:
            _call_to = getattr(APP_CONFIG, "ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS", 20)
            _timeout = max(getattr(APP_CONFIG, "ONLINE_EVAL_TIMEOUT_SECONDS", 25),
                           _call_to + _JUDGE_LAYER_MARGIN_SECONDS)
            try:
                verdicts.extend(await asyncio.wait_for(judge_fn(case, run), timeout=_timeout))
            except asyncio.TimeoutError:
                # Keep the rulings that COMPLETED. They were computed and, more to the
                # point, already billed — discarding them left the roster reading
                # "deterministic scoring" on a turn the user had paid to have judged.
                _kept = _take_judge_partial(_judge_partial, verdicts)
                _cut = _mark_judges_cut(
                    profile_class, verdicts,
                    f"judge did not return within the {_timeout:g}s evaluation window — abstained")
                logger.warning(
                    f"online-eval: judges timed out after {_timeout}s — "
                    f"kept {_kept} completed verdict(s), {_cut} abstained on the cut-off")
            except Exception as e:
                _kept = _take_judge_partial(_judge_partial, verdicts)
                _cut = _mark_judges_cut(profile_class, verdicts,
                                        f"judge layer failed ({e}) — abstained")
                logger.warning(f"online-eval: judge layer failed: {e} — "
                               f"kept {_kept} completed verdict(s), {_cut} abstained")
        # `eval.judged` is now a statement about the RECORD, not about the attempt:
        # true iff a judge ruling actually reached the span. A partial panel counts —
        # the surfaces read verdicts, and a turn carrying real judge chips must not be
        # labelled "deterministic scoring".
        include_judges = judged_from_record(verdicts)

        # Judge token cost. Bill whatever the panel ACTUALLY spent — NOT gated on
        # include_judges. A judge timeout/error clears include_judges (we drop those verdicts),
        # but dimensions that completed before the timeout already spent real tokens on the
        # user's credentials; gating the summation on include_judges billed that spend as $0.
        judge_in = judge_out = 0
        cost_usd = 0.0
        _usage = list(getattr(panel, "usage", []) or []) if panel else []
        judge_models = sorted({_m for _p, _m, _i, _o in _usage if _m}) or (
            panel.models if (panel and include_judges) else [])
        judge_usage = []
        if _usage:
            for _p, _m, _i, _o in _usage:
                judge_in += _i or 0
                judge_out += _o or 0
            try:
                from trusted_data_agent.core.cost_manager import CostManager
                _cm = CostManager()
                judge_usage = judge_usage_breakdown(
                    _usage, cost_fn=lambda p, m, i, o: _cm.calculate_cost(p, m, i, o))
            except Exception as e:  # noqa: BLE001 — a pricing gap must not lose the panel
                logger.debug(f"online-eval: judge usage breakdown failed: {e}")
                judge_usage = judge_usage_breakdown(_usage)
            if judge_in or judge_out:
                try:
                    from trusted_data_agent.core.cost_manager import CostManager
                    cm = CostManager()
                    for _p, _m, _i, _o in _usage:
                        cost_usd += cm.calculate_cost(_p, _m, _i or 0, _o or 0)
                except Exception as e:
                    logger.debug(f"online-eval: judge cost calc failed: {e}")

        tip = run.provenance_meta.get("chain_tip_hash")
        attestation = sign_attestation(tip, verdicts, profile_class)
        prof = profile or {}
        prof_tag = prof.get("tag") or turn.get("profile_tag")
        prof_id = prof.get("id") or turn.get("profile_id")
        span = build_quality_span(
            session_id=session_id, turn_id=turn_id, profile_class=profile_class,
            verdicts=verdicts, sampled=True, anomalous=anomalous,
            attestation=attestation, started_at=started,
            profile_tag=prof_tag, profile_id=prof_id, user_uuid=user_uuid,
            invocation=invocation, experts=run.slaves_invoked() or None,
            parent_session_id=parent_session_id, library_use=run.library_use())
        span["attributes"]["eval.judged"] = include_judges
        # I1 — lift the GATE's outcome onto the span. Scorers describe what was
        # measured; without this the span cannot say what the gates DID, and every
        # consumer surface reads only the span (see eval/gate_outcome.py for the five
        # findings this closes). Absent gate stamps ⇒ no attributes ⇒ identical span.
        try:
            _gattrs = gate_attributes(turn)
            span["attributes"].update(_gattrs)
            # #43 — a runtime gate that WITHHELD or redacted an answer is the clearest
            # failure a turn can have, and it is invisible to the scorers (they see the
            # delivered text). Without this a blocked turn could read as clean, while
            # advisory dimensions convicted turns that were merely paraphrased.
            if _gattrs.get("eval.gate.acted"):
                span["attributes"]["eval.gating_failures"] = (
                    span["attributes"].get("eval.gating_failures", 0) + 1)
                span["attributes"]["eval.gate.caused_failure"] = True
        except Exception as e:  # noqa: BLE001 — observability must never break a turn
            logger.debug(f"online-eval: gate attributes failed: {e}")
        span["attributes"]["eval.cost_usd"] = cost_usd
        span["attributes"]["eval.input_tokens"] = judge_in
        span["attributes"]["eval.output_tokens"] = judge_out
        try:
            _get_exporter().export(span)
        except Exception as e:
            logger.warning(f"online-eval: export failed: {e}")

        summary = _summary(verdicts)
        summary.update({"sampled": True, "judged": include_judges, "anomalous": anomalous,
                        "profile_class": profile_class, "signed": attestation["signed"],
                        "input_tokens": judge_in, "output_tokens": judge_out,
                        "cost_usd": cost_usd, "judge_models": judge_models})

        # The Live-Status "Quality" card after execution: same card as on reload,
        # carrying verdicts + the judge cost. Mirrors next_step_generation_complete.
        score_evt = {
            "step": "Agent Quality Scored",
            "type": "turn_score_complete",
            "details": {
                "session_id": session_id, "turn_id": turn_id,
                "profile_class": profile_class, "profile_tag": prof_tag,
                "judged": include_judges, "judge_models": judge_models,
                "input_tokens": judge_in, "output_tokens": judge_out, "cost_usd": cost_usd,
                "passed": summary.get("passed"), "failures": summary.get("failures", []),
                "verdicts": [v.to_dict() for v in verdicts],
                "summary": (f"Scored {len(verdicts)} dimension(s) — "
                            + (f"judged, {judge_in} in / {judge_out} out, ${cost_usd:.6f}"
                               if include_judges else "deterministic, $0.000000")),
            },
        }
        if event_handler is not None:
            try:
                await event_handler(score_evt, "turn_score_complete")
            except Exception as e:
                logger.debug(f"online-eval: event emit failed: {e}")
        # The hook is backgrounded, so by the time scoring finishes the per-request
        # /ask_stream (which `event_handler` writes to) has already closed — that path
        # only renders the live Quality card when the turn was still streaming. ALSO
        # broadcast over the per-user notification channel (an always-open EventSource),
        # which is what makes the card appear live after execution rather than only on a
        # plan reload. The frontend handler is session-guarded + the overlay is idempotent.
        try:
            from trusted_data_agent.core.config import APP_STATE
            _queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
            for _q in list(_queues):
                _q.put_nowait({"type": "turn_score_complete",
                               "payload": score_evt["details"]})   # TC-70: no unreferenced task
        except Exception as e:
            logger.debug(f"online-eval: notification emit failed: {e}")

        # Rigorous cost allocation (only when judges actually spent tokens): roll
        # into live KPIs + turn totals + consumption, and persist the step so the
        # card + cost survive a plan reload. Mirrors the recommender's Tier C.
        try:
            await _record_score_usage(
                user_uuid, session_id, turn_id, judge_in, judge_out, cost_usd, event_handler,
                judge_models=judge_models, judge_usage=judge_usage,
                judge_verdicts=judge_verdict_summary(verdicts),
                profile_class=profile_class, layer="post_hoc")
        except Exception as e:
            logger.debug(f"online-eval: score accounting skipped: {e}")

        # PROP-001 / PROP-002 — the judge condemnation must not die as a number on
        # a card: a DELIVERED answer that a post-hoc judge convicts of being
        # ungrounded becomes a visible, ack-able incident. Per-profile opt-out via
        # `judgeConfig.inboxOnCondemnation: false` (default ON — the incident is the
        # point of judging).
        try:
            if jprefs["inbox"]:
                _record_judge_condemnation(user_uuid, session_id, turn_id, prof_tag,
                                           verdicts, turn, judged=include_judges)
        except Exception as e:
            logger.debug(f"online-eval: judge condemnation check skipped: {e}")

        # Trust-budget circuit breaker (opt-in, default off) — after the span is
        # exported so this turn's verdict counts toward the agent's trailing record.
        try:
            await _run_trust_breaker(user_uuid, prof_tag, prof_id)
        except Exception as e:
            logger.debug(f"online-eval: trust breaker check skipped: {e}")
        return summary
    except Exception as e:
        logger.warning(f"online-eval hook failed (turn unaffected): {e}")
        return None


# ── Per-profile JUDGED-station preferences (PROP-002 follow-up) ──────────────
def resolve_judge_prefs(profile, *, include_default: bool, default_rate: float) -> dict:
    """Resolve a profile's ``judgeConfig`` against the admin defaults — pure.

    ``judges``: 'inherit' (default) follows the admin INCLUDE_JUDGES setting;
    'on'/'off' override it per agent (a high-stakes agent judged at 1.0 beside a
    scratch agent never judged). ``sampleRate`` overrides the admin rate when set
    (clamped to [0,1]; malformed values fall back — a typo must not disable
    judging). ``inbox`` is whether a judge condemnation files an Assurance-Inbox
    incident (default ON — the feedback loop is the point of judging).

    The deployment scoring master (ONLINE_EVAL_ENABLED) is NOT consulted here —
    it hard-gates the whole hook above this resolution, so a profile can never
    turn scoring on for a deployment that switched it off.
    """
    jc = (profile or {}).get("judgeConfig") or {}
    if not isinstance(jc, dict):
        jc = {}
    mode = str(jc.get("judges") or "inherit").lower()
    wanted = (mode == "on") if mode in ("on", "off") else bool(include_default)
    rate = jc.get("sampleRate")
    try:
        rate = float(rate) if rate is not None and str(rate) != "" else None
    except (TypeError, ValueError):
        rate = None
    if rate is not None:
        rate = min(1.0, max(0.0, rate))
    return {"wanted": wanted,
            "rate": rate if rate is not None else float(default_rate),
            "inbox": jc.get("inboxOnCondemnation") is not False}


# ── The judge condemnation incident (PROP-001, surfaced per PROP-002) ────────
# Campaign case G7: three judges unanimously found a delivered answer ungrounded
# and NOTHING was created — no incident, no review item; the verdict became a
# number on a card. This closes the surfacing half: a failed groundedness-family
# JUDGE verdict on a turn the gate did NOT withhold records a danger incident in
# the Assurance Inbox (+ live notice). Whether such a turn should also PARK for
# approval is PROP-001's own open decision — deliberately not taken here.
#
# Runs only from the online hook (once per turn); a manual re-score does not
# re-emit. Fail-open like every inbox write.
_CONDEMNING_DIMS = frozenset({"groundedness", "answer_groundedness"})


def _record_judge_condemnation(user_uuid, session_id, turn_id, prof_tag,
                               verdicts, turn, *, judged: bool) -> None:
    if not judged:
        return
    failed = [v for v in (verdicts or [])
              if getattr(v, "kind", "") == "judge"
              and getattr(v, "dimension", "") in _CONDEMNING_DIMS
              and getattr(v, "passed", None) is False]
    if not failed:
        return
    # A WITHHELD answer was never delivered — the gate already acted, and a second
    # danger item for the same turn would be the double-count the station
    # partition exists to prevent.
    g = turn.get("grounding") if isinstance(turn, dict) else None
    if isinstance(g, dict) and g.get("decision") == "block":
        return
    from trusted_data_agent.core import trust_events
    dims = sorted({v.dimension for v in failed})
    names = ", ".join(d.replace("_", " ") for d in dims)
    summary = (f"Judge condemnation: a delivered answer failed the {names} "
               f"judge{'s' if len(dims) > 1 else ''} — the in-path gate did not withhold it")
    trust_events.record_event(
        user_uuid, "judge_condemnation", summary, severity="danger",
        profile_tag=(str(prof_tag).lstrip("@") or None) if prof_tag else None,
        session_id=session_id, turn_id=turn_id,
        payload={"dimensions": dims,
                 "scores": {v.dimension: getattr(v, "score", None) for v in failed}})
    trust_events.broadcast_notice(
        user_uuid, "judge_condemnation", summary,
        session_id=session_id,
        profile_tag=(str(prof_tag).lstrip("@") or None) if prof_tag else None,
        severity="danger")


# ── Trust-budget circuit breaker ─────────────────────────────────────────────
# Closed-loop, opt-in response to sustained bad conduct: when an agent's
# trailing behavioral record enters `at_risk` (its trust budget is exhausted),
# notify the owner — and, in `freeze_autonomy` mode, suspend the agent's
# UNATTENDED use (Agent Authority autonomous=False) until a human reviews.
# Interactive use is never touched; the action is recorded on the profile so it
# is visible and reversible in the Agent Authority card. Acts only on the
# TRANSITION into at_risk (per process lifetime) so one bad streak produces one
# alert, not one per turn. Fully defensive: any failure degrades to a log line.
_BREAKER_LAST_STATE: dict = {}


async def _run_trust_breaker(user_uuid: str, prof_tag, prof_id) -> None:
    from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
    from trusted_data_agent.eval import trust_behavior as tb

    cfg = tb.resolve_config(getattr(APP_CONFIG, "TRUST_BEHAVIOR", None) or {})
    mode = str(cfg.get("breaker_mode", "off") or "off").lower()
    if not cfg.get("enabled") or mode == "off" or not prof_tag:
        return

    # Trailing record for THIS user's agent from the span sink (bounded tail).
    import json as _json
    import os as _os
    path = getattr(APP_CONFIG, "ONLINE_EVAL_SINK", "tda_eval/online_eval.jsonl")
    if not _os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()][-2000:]
    agent_spans = []
    for line in lines:
        try:
            sp = _json.loads(line)
        except Exception:
            continue
        attrs = sp.get("attributes") or {}
        if attrs.get("agent.profile_tag") == prof_tag and attrs.get("user.uuid") == user_uuid:
            agent_spans.append(sp)
    if not agent_spans:
        return

    behavior = tb.behavior_for_spans(agent_spans, config=cfg)
    decision = tb.breaker_decision(behavior, cfg)
    key = (user_uuid, str(prof_tag))
    prev = _BREAKER_LAST_STATE.get(key)
    _BREAKER_LAST_STATE[key] = behavior.get("state")
    if not decision["trip"] or prev == "at_risk":
        return

    action = "notified"
    if mode == "freeze_autonomy" and prof_id:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager, get_profile
            prof = get_profile(prof_id, user_uuid) or {}
            ac = dict(prof.get("authorityConfig") or prof.get("identityConfig") or {})
            if ac.get("autonomous") is not False:
                ac.update({"autonomous": False, "frozen_by_breaker": True,
                           "frozen_reason": decision["reason"]})
                if get_config_manager().update_profile(prof_id, {"authorityConfig": ac}, user_uuid):
                    action = "autonomy_frozen"
        except Exception as e:
            logger.warning(f"trust-breaker: freeze failed for @{prof_tag}: {e}")

    logger.warning(
        f"TRUST BREAKER tripped for @{prof_tag} (user {user_uuid}): {decision['reason']} — {action}")
    try:
        from trusted_data_agent.core import trust_events
        trust_events.record_event(
            user_uuid, "trust_breaker",
            f"Trust breaker tripped for @{prof_tag}: {decision['reason']} — {action}",
            severity="danger", profile_tag=prof_tag,
            payload={"action": action, "score": behavior.get("score"),
                     "turns": behavior.get("turns"), "breaker_mode": mode})
    except Exception as e:
        logger.debug(f"trust-breaker: inbox record failed: {e}")
    try:
        # ONE live-emission path (PROP-002): the breaker was the single event type
        # whose stored row and live frame were produced by two different code paths.
        # `notice_type` keeps the client's dedicated `trust_breaker` rendering;
        # `extra` carries the frame's payload; severity rides so the banner tone is
        # honest.
        from trusted_data_agent.core import trust_events
        trust_events.broadcast_notice(
            user_uuid, "trust_breaker",
            f"Trust breaker tripped for @{prof_tag}: {decision['reason']} — {action}",
            profile_tag=prof_tag, severity="danger", notice_type="trust_breaker",
            extra={"profile_id": prof_id, "state": behavior.get("state"),
                   "score": behavior.get("score"), "turns": behavior.get("turns"),
                   "action": action, "reason": decision["reason"], "breaker_mode": mode})
    except Exception as e:
        logger.debug(f"trust-breaker: notification emit failed: {e}")
