"""Behavioral Trust Score — fuses integrity attestation with the live track record.

The Trust Membrane answers *"is this agent unchanged?"* (static integrity); the
online-eval stream answers *"is this agent behaving well?"* (dynamic conduct).
This module is the fusion layer: a pure, deterministic, CI-testable assessment
that turns both signals into one **behavioral trust state** per agent, with
honest volume gates (an agent cannot be "trusted" on three turns) and honest
decay (trust fades when the evidence is stale, and an intact signature cannot
outrank bad live conduct).

Design constraints (platform charter):
- Pure: no I/O, no clock reads unless injected (``now_ns``), no imports from the
  removable Ontology Suite — integrity states are *passed in* by the caller,
  which resolves them defensively (suite absent → integrity unknown → uncapped
  but reported as such, never punished).
- Observe-only: nothing here gates or alters agent behaviour. The score is a
  transparency surface; enforcement (trust budgets / circuit breaker) is a
  separate opt-in layer that CONSUMES this assessment.
- Span shape: the same OTLP-shaped quality spans ``eval/analysis.py`` consumes
  (``attributes["agent.profile_tag"]``, ``eval.gating_failures``,
  ``eval.dim.<d>.passed``, ``end_time_unix_nano``). Flow spans
  (``agent.kind == "flow"``) are excluded, mirroring ``online_quality_by_agent``.

Behavior states (rank order, worst→best):
    at_risk < watch < proven < trusted    (+ provisional = insufficient evidence)

Composite = behavior capped by integrity:
    integrity trusted  → no cap (behavior speaks)
    integrity unsigned → capped at "proven"  (never composite-trusted unsigned)
    integrity changed  → capped at "watch"   (signed-but-tampered = review)
    integrity unknown  → no cap, reported as unknown (absence-tolerant, honest)
"""
from __future__ import annotations

from typing import Optional

# Defaults for the admin-tunable `trust_behavior` block (APP_CONFIG.TRUST_BEHAVIOR).
# `enabled` gates only the UI ring/state surfaces — computing the assessment is
# read-only and always safe; default off preserves the byte-identical baseline.
DEFAULTS: dict = {
    "enabled": False,
    "window_turns": 50,        # trailing evidence window per agent
    "min_turns_proven": 10,    # evidence needed to leave "provisional"
    "min_turns_trusted": 25,   # evidence needed to reach "trusted"
    "healthy_floor": 0.80,     # weighted score >= floor → proven-eligible
    "trusted_floor": 0.90,     # weighted score >= floor → trusted-eligible
    "at_risk_floor": 0.50,     # weighted score <  floor → at_risk
    "half_life_days": 14.0,    # recency half-life for pass/fail weighting
    "stale_days": 30.0,        # newest evidence older than this → decay flag
    # Trust-critical dimensions: failures here are trust failures, not mere
    # quality dips, so they carry extra weight when present on a turn.
    "trust_dims": ["scope_adherence", "answer_groundedness", "governed_data_ratio",
                   "no_tool_loops", "output_sanity", "content_safety"],
    # Blend: (1-w)*RESIDUAL rate (every non-critical dim) + w*trust-dim rate.
    # A PARTITION, deliberately: the first term used to be the whole-turn pass
    # rate, so a trust-critical failure was counted twice — once failing the
    # turn, once lowering the critical rate. TRUSTED is the platform's single
    # aggregate over every station's evidence, and an aggregate is only
    # transparent if each dimension is counted exactly once.
    "trust_dim_weight": 0.4,
    # Circuit breaker — the closed-loop, opt-in response to sustained bad conduct.
    # "off" (default) = pure observability; "notify" = alert the owner when an
    # agent's conduct exhausts its trust budget (enters at_risk); "freeze_autonomy"
    # = additionally suspend the agent's UNATTENDED use (Agent Authority
    # autonomous=False) until a human reviews — interactive use is never touched.
    "breaker_mode": "off",
}

_RANK = {"at_risk": 0, "watch": 1, "proven": 2, "trusted": 3}
_STATES = set(_RANK) | {"provisional"}

_NS_PER_DAY = 86_400 * 1_000_000_000


def resolve_config(overrides: Optional[dict] = None) -> dict:
    """DEFAULTS merged with a (possibly partial) admin override block."""
    cfg = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k in DEFAULTS and v is not None:
            cfg[k] = v
    return cfg


def _span_ts(span: dict) -> Optional[int]:
    ts = span.get("end_time_unix_nano") or span.get("start_time_unix_nano")
    return int(ts) if isinstance(ts, (int, float)) and ts > 0 else None


def _span_passed(span: dict) -> bool:
    return not bool((span.get("attributes") or {}).get("eval.gating_failures", 0))


def _trust_dim_signal(span: dict, trust_dims: list) -> Optional[float]:
    """Mean pass over the trust-critical dims decided on this span, or None."""
    attrs = span.get("attributes") or {}
    vals = []
    for d in trust_dims:
        v = attrs.get(f"eval.dim.{d}.passed")
        if v is not None:
            vals.append(1.0 if v else 0.0)
    return (sum(vals) / len(vals)) if vals else None


def _residual_signal(span: dict, trust_dims: list) -> Optional[float]:
    """Mean pass over every NON-critical dim decided on this span, or None.

    The complement of `_trust_dim_signal` over the same per-dim attributes, so the
    two terms of the blend PARTITION the span's evidence: no dimension is in both.
    None when the span carries no per-dim attributes at all (older spans) — the
    caller then falls back to the whole-turn pass rate, which is honest there
    because nothing finer was recorded.
    """
    attrs = span.get("attributes") or {}
    crit = set(trust_dims)
    vals = []
    for k, v in attrs.items():
        if not (k.startswith("eval.dim.") and k.endswith(".passed")):
            continue
        d = k[len("eval.dim."):-len(".passed")]
        if d in crit or v is None:
            continue
        vals.append(1.0 if v else 0.0)
    return (sum(vals) / len(vals)) if vals else None


def behavior_for_spans(agent_spans: list, *, now_ns: Optional[int] = None,
                       config: Optional[dict] = None) -> dict:
    """Assess one agent's behavioral trust from its quality spans.

    ``agent_spans`` must already be this agent's spans (any order); the newest
    ``window_turns`` (by timestamp; untimestamped spans keep list order and a
    neutral weight) form the evidence window. Returns the behavior dict —
    never raises on malformed spans (they contribute a neutral/failed reading
    only where the attribute is genuinely present).
    """
    cfg = resolve_config(config)
    spans = [s for s in agent_spans
             if (s.get("attributes") or {}).get("agent.kind") != "flow"]
    # Chronological sort where timestamps exist; stable for those without.
    spans.sort(key=lambda s: _span_ts(s) or 0)
    windowed = spans[-int(cfg["window_turns"]):]

    if now_ns is None:
        # Deterministic default: anchor "now" to the newest evidence so the
        # assessment of a fixed span set is reproducible (tests, backfills).
        now_ns = max((t for t in (_span_ts(s) for s in spans) if t), default=0)

    half_life_ns = float(cfg["half_life_days"]) * _NS_PER_DAY
    w_sum = w_pass = 0.0
    td_w_sum = td_w_pass = 0.0
    rd_w_sum = rd_w_pass = 0.0
    trend = []
    for s in windowed:
        ts = _span_ts(s)
        if ts is not None and half_life_ns > 0 and now_ns:
            age = max(0.0, float(now_ns - ts))
            w = 0.5 ** (age / half_life_ns)
        else:
            w = 1.0  # untimestamped evidence: neutral weight, honest inclusion
        passed = _span_passed(s)
        w_sum += w
        w_pass += w if passed else 0.0
        trend.append(1 if passed else 0)
        td = _trust_dim_signal(s, cfg["trust_dims"])
        if td is not None:
            td_w_sum += w
            td_w_pass += w * td
        rd = _residual_signal(s, cfg["trust_dims"])
        if rd is not None:
            rd_w_sum += w
            rd_w_pass += w * rd

    turns = len(windowed)
    weighted_pass_rate = (w_pass / w_sum) if w_sum > 0 else None
    trust_dim_rate = (td_w_pass / td_w_sum) if td_w_sum > 0 else None
    residual_rate = (rd_w_pass / rd_w_sum) if rd_w_sum > 0 else None

    # The blend is a PARTITION: trust-critical dims carry `w`, every other dim
    # carries `1-w`, and no dimension is in both terms — the whole-turn pass rate
    # (which re-counts a critical failure) is only the fallback for spans that
    # recorded no per-dimension evidence at all.
    if weighted_pass_rate is None:
        score = None
    elif trust_dim_rate is None:
        score = residual_rate if residual_rate is not None else weighted_pass_rate
    elif residual_rate is None:
        score = trust_dim_rate
    else:
        tw = float(cfg["trust_dim_weight"])
        score = (1.0 - tw) * residual_rate + tw * trust_dim_rate

    newest_ts = max((t for t in (_span_ts(s) for s in spans) if t), default=None)
    stale = bool(newest_ts and now_ns
                 and (now_ns - newest_ts) > float(cfg["stale_days"]) * _NS_PER_DAY)

    # State ladder — volume-gated, floor-banded, stale-capped.
    if turns < int(cfg["min_turns_proven"]) or score is None:
        state = "provisional"
    elif score < float(cfg["at_risk_floor"]):
        state = "at_risk"
    elif score < float(cfg["healthy_floor"]):
        state = "watch"
    elif score >= float(cfg["trusted_floor"]) and turns >= int(cfg["min_turns_trusted"]):
        state = "trusted"
    else:
        state = "proven"
    if stale and _RANK.get(state, -1) > _RANK["proven"]:
        state = "proven"  # trust cannot be *held* on stale evidence

    return {
        "state": state,
        "score": round(score, 4) if score is not None else None,
        "weighted_pass_rate": round(weighted_pass_rate, 4) if weighted_pass_rate is not None else None,
        "trust_dim_rate": round(trust_dim_rate, 4) if trust_dim_rate is not None else None,
        "residual_rate": round(residual_rate, 4) if residual_rate is not None else None,
        "turns": turns,
        "stale": stale,
        "last_activity_ns": newest_ts,
        "trend": trend[-30:],
    }


# Integrity caps: the highest composite state each integrity reading permits.
#
# `deployment_unattested` is the fourth Trust-Membrane state (a portable signature is
# intact but THIS instance never attested its own resolved deployment surface — model,
# tool catalog, connector opt-ins). It must cap no higher than `unsigned`: we know LESS
# about an agent whose deployment was never attested than about a portable-signed one, so
# letting it reach `trusted` (which is what "no cap" did) inverted the ladder. Capped at
# `proven`, matching `unsigned` — it cannot reach the top state until the deployment is
# attested here.
_INTEGRITY_CAP = {"trusted": "trusted", "unsigned": "proven",
                  "deployment_unattested": "proven", "changed": "watch"}


def compose(integrity_state: Optional[str], behavior: dict,
            config: Optional[dict] = None) -> dict:
    """Fuse an integrity reading (Trust Membrane state, or None = unknown) with a
    behavior assessment into the composite trust state.

    The composite can only ever be *lowered* by integrity — a signature never
    promotes bad conduct, and unsigned/tampered artefacts cap how high conduct
    can lift the agent. Unknown integrity (suite absent) applies no cap but is
    reported honestly.
    """
    b_state = behavior.get("state", "provisional")
    if b_state == "provisional":
        return {"state": "provisional", "integrity": integrity_state,
                "reason": "insufficient evidence"}
    cap = _INTEGRITY_CAP.get(integrity_state)  # None → unknown → no cap
    if cap is not None and _RANK[cap] < _RANK[b_state]:
        reason = {"unsigned": "capped: agent is not signed",
                  "deployment_unattested": "capped: this deployment is not attested",
                  "changed": "capped: signed content has changed"}.get(integrity_state,
                                                                       "capped by integrity")
        return {"state": cap, "integrity": integrity_state, "reason": reason}
    reason = "behavioral" if cap is None and integrity_state is None else "integrity + behavior"
    if behavior.get("stale"):
        reason += " (stale evidence)"
    return {"state": b_state, "integrity": integrity_state, "reason": reason}


def breaker_decision(behavior: dict, config: Optional[dict] = None) -> dict:
    """Trust-budget circuit breaker decision — pure and deterministic.

    Trips exactly when the agent's behavior state is ``at_risk`` (the state
    ladder already volume-gates: at_risk requires >= min_turns_proven evidence,
    so a couple of bad turns on a new agent can never trip the breaker).
    The caller decides what a trip DOES (notify / freeze autonomy) — this
    function only decides whether the budget is exhausted, and why.
    """
    cfg = resolve_config(config)
    trip = behavior.get("state") == "at_risk"
    if trip:
        score = behavior.get("score")
        reason = (f"conduct score {round(score, 3) if score is not None else '?'} fell below "
                  f"the at-risk floor ({cfg['at_risk_floor']}) over the last "
                  f"{behavior.get('turns', 0)} evaluated turn(s)")
    else:
        reason = "within trust budget"
    return {"trip": trip, "reason": reason}


def assess_agents(spans: list, *, integrity_by_tag: Optional[dict] = None,
                  now_ns: Optional[int] = None,
                  config: Optional[dict] = None,
                  per_user: bool = False,
                  self_uuid: Optional[str] = None) -> dict:
    """Full fleet assessment: {key: {behavior, integrity, composite}}.

    ``integrity_by_tag`` maps profile_tag → Trust-Membrane state string
    ("trusted"|"unsigned"|"changed") or is omitted entirely when the trust
    suite is unavailable (integrity then reads None = unknown).

    TC-35: with ``per_user=False`` (my scope) the key is the plain profile tag — the
    historical shape, byte-identical. With ``per_user=True`` (system scope) the key is
    ``"tag::uuid"`` and each cell is stamped with ``tag``/``user_uuid`` — mirroring
    ``analysis.online_quality_by_agent(per_user=True)``. Without this, two users' same-tag
    agents (both have @OPTIM) silently merged into one behavioral cell, and the REQUESTER's
    ``integrity_by_tag`` capped OTHER users' conduct. In per-user mode integrity joins only
    onto the requester's own cells (``self_uuid``) — other users' agents read integrity
    unknown, which is honest: their audit records are not the requester's.
    """
    integrity_by_tag = integrity_by_tag or {}
    self_uuid = str(self_uuid) if self_uuid else ""

    def _key(tag, uuid):
        return f"{tag}::{uuid or ''}" if per_user else tag

    def _integrity_for(tag, uuid):
        if per_user and str(uuid or "") != self_uuid:
            return None
        return integrity_by_tag.get(tag)

    by_key: dict = {}
    for sp in spans:
        attrs = sp.get("attributes") or {}
        if attrs.get("agent.kind") == "flow":
            continue
        tag = attrs.get("agent.profile_tag")
        if not tag:
            continue
        uuid = attrs.get("user.uuid") or ""
        by_key.setdefault(_key(tag, uuid), {"tag": tag, "uuid": uuid, "spans": []})["spans"].append(sp)
    out: dict = {}
    for key, cellin in by_key.items():
        tag, uuid = cellin["tag"], cellin["uuid"]
        behavior = behavior_for_spans(cellin["spans"], now_ns=now_ns, config=config)
        integrity = _integrity_for(tag, uuid)
        cell = {
            "behavior": behavior,
            "integrity": {"state": integrity},
            "composite": compose(integrity, behavior, config),
        }
        if per_user:
            cell["tag"], cell["user_uuid"] = tag, uuid
        out[key] = cell
    # Agents with an integrity reading but no spans still appear (an admin must
    # see a signed-but-never-evaluated agent as provisional, not invisible). These are
    # the REQUESTER's profiles, so in per-user mode they key under self_uuid.
    for tag, integrity in integrity_by_tag.items():
        key = _key(tag, self_uuid)
        if key not in out:
            behavior = {"state": "provisional", "score": None,
                        "weighted_pass_rate": None, "trust_dim_rate": None,
                        "residual_rate": None,
                        "turns": 0, "stale": False, "last_activity_ns": None,
                        "trend": []}
            cell = {
                "behavior": behavior,
                "integrity": {"state": integrity},
                "composite": compose(integrity, behavior, config),
            }
            if per_user:
                cell["tag"], cell["user_uuid"] = tag, self_uuid
            out[key] = cell
    return out
