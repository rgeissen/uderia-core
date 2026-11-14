"""Eval analysis — failure-mode clustering and experiment diffing.

Pure functions over score rows / run summaries / online spans, so the UX layer
(W3.5 dashboard) and the CLI both consume one implementation. CI-pure.
"""
from __future__ import annotations

from typing import Dict, Optional


#: Dimension → scorer kind, resolved once from the registry. `judge` is the only value
#: the conviction rule cares about: a judge OPINED, a deterministic check MEASURED.
_KIND_CACHE: dict = {}


def _dim_kind(dim: str) -> str:
    if not _KIND_CACHE:
        try:
            from trusted_data_agent.eval import scorer as _scorer
            import trusted_data_agent.eval.scorers  # noqa: F401 — registers the suite
            for x in _scorer.all_scorers():
                d = getattr(x, "dimension", None)
                if d:
                    _KIND_CACHE[d] = str(getattr(x, "kind", "")).split(".")[-1].lower()
        except Exception:  # noqa: BLE001 — an absent eval package is not a broken read
            return "deterministic"
        _KIND_CACHE.setdefault("", "deterministic")
    return _KIND_CACHE.get(dim, "deterministic")


def _bands(attrs: dict) -> dict:
    """This turn's per-dimension outcome as bands, from the span's own attributes."""
    out = {}
    for k, v in (attrs or {}).items():
        if k.startswith("eval.dim.") and k.endswith(".passed") and v is not None:
            out[k[len("eval.dim."):-len(".passed")]] = "healthy" if v else "danger"
    return out


def judge_cleared(attrs: dict) -> tuple:
    """Coarse checks a refining judge CORRECTED — Rule 3.0c's `disputed` reading.

    These are the exception in the conviction rule, and they must stay VISIBLE: a
    deterministic check said the answer was unsupported and the judge that refines it
    disagreed. That is a real disagreement about a real turn, not an absence.
    """
    if not isinstance(attrs, dict):
        return ()
    try:
        from trusted_data_agent.agent.assurance_points import refinement_reading
    except Exception:  # noqa: BLE001
        return ()
    b = _bands(attrs)
    out = []
    for coarse, judge in (_REFINEMENT_PAIRS or {}).items():
        if refinement_reading(b.get(coarse), b.get(judge)) == "disputed":
            out.append(coarse)
    return tuple(sorted(out))


def judge_flagged(attrs: dict) -> tuple:
    """Coarse checks a refining judge CONTRADICTED — Rule 3.0c's `judge_only` reading.

    The mirror of :func:`judge_cleared`: the rule measured the answer clean and the judge
    that refines it found a problem. It does NOT convict (a judge alone never does), but
    it is a disagreement about a real turn and it was the only one of the four readings
    with no name on the surface — a green measurement beside a red judge, with nothing
    saying the two are about the same property.
    """
    if not isinstance(attrs, dict):
        return ()
    try:
        from trusted_data_agent.agent.assurance_points import refinement_reading
    except Exception:  # noqa: BLE001
        return ()
    b = _bands(attrs)
    return tuple(sorted(
        coarse for coarse, judge in (_REFINEMENT_PAIRS or {}).items()
        if refinement_reading(b.get(coarse), b.get(judge)) == "judge_only"))


def convicting_dimensions(attrs: dict) -> tuple:
    """The checks that FAIL this turn — the one conviction rule, both surfaces.

    A turn fails when a check a RULE computed landed in the danger band. A judge alone
    in danger does not convict: a model opined, and the platform's judges are advisory
    until their calibration clears — but it stays visible, and where a judge CORROBORATES
    a deterministic check the deterministic one convicts on its own anyway.

    The exception is Rule 3.0c's `disputed`: a coarse check flagged and the judge that
    refines it cleared it. That is not a failure, and `judge_cleared()` names it so the
    correction is shown rather than silently swallowed.

    A check with no refining judge stands alone at full weight — the rule the refinement
    reading already states for an absent judge.

    This replaced gate-eligibility (`Scorer.gating`) as the decider. Under that rule a
    turn where `grounded_overlap` AND `groundedness` BOTH found the answer unsupported
    scored clean, because each is individually advisory — the strongest grounding
    evidence available, counted as nothing. `Scorer.gating` still governs the Scorecard's
    "gating failures vs flagged for review" split; it no longer decides a turn's fate.
    """
    if not isinstance(attrs, dict):
        return ()
    cleared = set(judge_cleared(attrs))
    return tuple(sorted(
        d for d, band in _bands(attrs).items()
        if band == "danger" and _dim_kind(d) != "judge" and d not in cleared))


def gate_withheld(attrs: dict) -> bool:
    """Did a mechanism withhold the answer? A withheld turn must never read clean, and
    no measurement can express that — the scorers deliberately abstain on a blocked
    turn's delivered text (it is the platform's own notice, not the model's)."""
    return isinstance(attrs, dict) and attrs.get("eval.gate.grounding") == "block"


def conviction_count(attrs: dict) -> int:
    """How many convictions a turn carries — the measured failures, plus the gate
    withholding if one occurred. Resolved against the CURRENT registry, never the
    `eval.gating_failures` scalar frozen onto the span when it was written: a sink
    outlives a policy change, and a stored derived value keeps convicting under a
    retired rule."""
    if not isinstance(attrs, dict):
        return 0
    if not _bands(attrs):
        # No per-dimension evidence at all (older spans, the flow path): fall back to
        # the stored scalar rather than reading clean. Absence-tolerant, never
        # authoritative.
        return int(attrs.get("eval.gating_failures") or 0)
    return len(convicting_dimensions(attrs)) + (1 if gate_withheld(attrs) else 0)


def turn_failed(attrs: dict) -> bool:
    """Did this turn fail? The ONE predicate — the pass rate, the sparkline series, the
    KPI dashboard, the timeline and the runs ledger all read it, so no surface can
    convict on a rule another surface does not use."""
    return conviction_count(attrs) > 0


def dedupe_spans(spans: list[dict]) -> list[dict]:
    """Collapse multiple quality spans for the *same* unit to the latest one.

    A turn keyed by (session.id, turn.id), a flow run by flow.run_id. The online
    sampler emits one span per unit naturally; manual re-scores (the "Score this
    turn" button) emit several for one turn — keeping the last (newest, since the
    sink is chronological) prevents inflating an agent's turn count / pass rate.
    """
    by_key: dict = {}
    for sp in spans:
        attrs = sp.get("attributes") or {}
        if attrs.get("agent.kind") == "flow":
            key = ("flow", attrs.get("flow.run_id") or sp.get("span_id"))
        else:
            key = ("turn", attrs.get("session.id"), attrs.get("turn.id"))
        # None turn.id (or missing key parts) → treat each as unique
        if key[-1] is None:
            by_key[id(sp)] = sp
        else:
            by_key[key] = sp  # later occurrence wins
    return list(by_key.values())


# ── failure-mode clustering ───────────────────────────────────────────────────
def cluster_failures(scores: list[dict]) -> list[dict]:
    """Group failing reps by their *set of failing dimensions*.

    ``scores`` are eval_scores rows (dicts with case_id, rep_index, dimension,
    passed). Returns clusters [{signature, dimensions, count, cases}] sorted by
    count desc — the recurring failure shapes an SME should triage first.
    """
    by_rep: dict = {}
    for s in scores:
        if s.get("passed") is False:
            key = (s.get("case_id"), s.get("rep_index", 0))
            by_rep.setdefault(key, set()).add(s.get("dimension"))
    clusters: dict = {}
    for (case_id, _rep), dims in by_rep.items():
        sig = ",".join(sorted(d for d in dims if d))
        c = clusters.setdefault(sig, {"signature": sig, "dimensions": sorted(dims),
                                       "count": 0, "cases": []})
        c["count"] += 1
        if case_id not in c["cases"]:
            c["cases"].append(case_id)
    return sorted(clusters.values(), key=lambda x: x["count"], reverse=True)


def cluster_online_failures(spans: list[dict]) -> list[dict]:
    """Failure clustering over OTLP quality spans (eval.dim.<d>.passed == False)."""
    pseudo: list[dict] = []
    for sp in spans:
        attrs = sp.get("attributes") or {}
        case_id = attrs.get("turn.id") or sp.get("span_id")
        for k, v in attrs.items():
            if k.startswith("eval.dim.") and k.endswith(".passed") and v is False:
                dim = k[len("eval.dim."):-len(".passed")]
                pseudo.append({"case_id": case_id, "rep_index": 0,
                               "dimension": dim, "passed": False})
    return cluster_failures(pseudo)


# ── dimension pass rates (for dashboard bars) ─────────────────────────────────
def pass_rate_by_dimension(scores: list[dict]) -> dict:
    """{dimension: pass_rate} over decided (passed not None) scores."""
    agg: dict = {}
    for s in scores:
        p = s.get("passed")
        if p is None:
            continue
        d = s.get("dimension")
        cell = agg.setdefault(d, [0, 0])
        cell[1] += 1
        if p:
            cell[0] += 1
    return {d: (c[0] / c[1] if c[1] else None) for d, (c) in agg.items()}


#: The refinement pairs, from the ONE declaration (agent/assurance_points). Imported
#: rather than restated: a second copy here could silently disagree with the registry
#: the dial reads, and the two surfaces would then band the same turn differently.
try:
    from trusted_data_agent.agent.assurance_points import REFINEMENT_PAIRS as _REFINEMENT_PAIRS
except Exception:  # noqa: BLE001 — the removable suite may be absent; no pairs, no disputes
    _REFINEMENT_PAIRS = {}


def vote_alignment(spans: list[dict], votes: dict) -> dict:
    """How the users' own votes line up with what the scorers measured.

    ``votes`` maps ``(session_id, str(turn_id)) -> "up" | "down"`` (the latest vote
    per turn; a cleared vote is simply absent). Returns::

        {"upvotes": n, "downvotes": n, "disagreements": [ … ]}

    A *disagreement* is a turn the scorers passed but the user downvoted, or one
    they failed but the user upvoted — the highest-value calibration signal there
    is, because it is the only place a human says the bands are wrong.

    A vote is a PREFERENCE, never a measurement: nothing here feeds a pass rate, a
    band or a trust composite. It is reported BESIDE them (Rule 3bb).

    Pure. Caller dedupes (``dedupe_spans``) exactly as the other readers require.
    """
    out = {"upvotes": 0, "downvotes": 0, "disagreements": []}
    if not votes:
        return out
    seen: set = set()
    for sp in spans:
        attrs = sp.get("attributes") or {}
        if attrs.get("agent.kind") == "flow":
            continue
        key = (attrs.get("session.id"), str(attrs.get("turn.id")))
        vote = votes.get(key)
        if not vote or key in seen:
            continue
        seen.add(key)
        if vote == "up":
            out["upvotes"] += 1
        elif vote == "down":
            out["downvotes"] += 1
        else:
            continue
        # A refused turn was deliberately never measured, so it has no verdict to
        # agree or disagree with — counted as a vote, never as a disagreement.
        if attrs.get("eval.gate_only"):
            continue
        passed = not turn_failed(attrs)
        if (passed and vote == "down") or (not passed and vote == "up"):
            out["disagreements"].append({
                "session_id": key[0], "turn": attrs.get("turn.id"),
                "vote": vote, "passed": passed,
                "profile_tag": attrs.get("agent.profile_tag"),
            })
    return out


def online_quality_by_agent(spans: list[dict], per_user: bool = False,
                            votes: dict | None = None) -> dict:
    """Per-agent (profile tag) online quality signals: turns scored, gating
    failures, pass rate, and a recency-ordered pass/fail sparkline series.

    Returns {profile_tag: {turns, failures, pass_rate, series, profile_class}}.
    ``series`` is oldest→newest 1/0 (turn passed = no gating failure).

    ``per_user=True`` (system-scope admin views): cells are keyed
    ``"<tag>::<user_uuid>"`` and carry ``user_uuid`` — profile tags are only
    unique PER USER, so a cross-user aggregation keyed by tag alone would
    silently merge two different users' same-tag agents into one cell.

    ``votes`` (optional) maps ``(session_id, str(turn_id)) -> "up"|"down"``. When
    supplied, each cell also carries ``upvotes``/``downvotes`` and ``vote_disagreements``
    — a PREFERENCE reading published beside the measurements and never folded into
    ``pass_rate``, ``failures`` or ``series`` (Rule 3bb: intervention, quality and
    preference are separate axes). Omitted entirely when no votes are supplied, so
    every existing caller is byte-identical.
    """
    by: dict = {}
    for sp in spans:
        attrs = sp.get("attributes") or {}
        if attrs.get("agent.kind") == "flow":
            continue  # flow runs are their own agents — see online_quality_by_flow
        tag = attrs.get("agent.profile_tag")
        if not tag:
            continue
        uuid = attrs.get("user.uuid") or ""
        key = f"{tag}::{uuid}" if per_user else tag
        cell = by.setdefault(key, {"turns": 0, "failures": 0, "series": [],
                                   "profile_class": attrs.get("eval.profile_class"),
                                   **({"tag": tag, "user_uuid": uuid or None} if per_user else {}),
                                   "_dims": {}, "_dim_series": {}, "_turn_fails": [], "_turn_disputed": [], "_tasks": {}, "_task_turns": 0,
                                   "direct_turns": 0, "expert_turns": 0,
                                   "_cost": [], "_lat": [], "_tok": [],
                                   **({"upvotes": 0, "downvotes": 0,
                                       "vote_disagreements": 0} if votes else {})})
        # The user's own rating of this turn. Counted BEFORE the gate-only skip below:
        # a vote is a preference about the answer the user received, so it is real
        # feedback even on a refused turn — unlike a measurement, which that turn
        # deliberately never got. Kept strictly out of turns/failures/series.
        if votes:
            _v = votes.get((attrs.get("session.id"), str(attrs.get("turn.id"))))
            if _v == "up":
                cell["upvotes"] += 1
            elif _v == "down":
                cell["downvotes"] += 1
        # I1 — a GATE-ONLY span records a refused turn: the gate acted, but nothing was
        # measured (a blocked turn is deliberately kept away from the scorers). It must
        # reach the gate record WITHOUT diluting a quality denominator it was never
        # measured against, so it skips turns/series/dimensions entirely.
        if attrs.get("eval.gate_only"):
            _gg = cell.setdefault("_gate", {"acted": 0, "parked": 0, "escalated": 0,
                                            "cleared": 0, "blocked": 0, "redacted": 0,
                                            "refused": 0, "errored": 0})
            if attrs.get("eval.gate.acted"):
                _gg["acted"] += 1
            if attrs.get("eval.gate.parked"):
                _gg["parked"] += 1
            if attrs.get("eval.gate.inbound") == "block":
                _gg["refused"] += 1
            if attrs.get("eval.gate.outbound") in ("redact", "block"):
                _gg["redacted"] += 1
            if attrs.get("eval.gate.grounding") == "block":
                _gg["blocked"] += 1
            continue
        cell["turns"] += 1
        # Resource averages (for the roster + the agent-comparison metric picker —
        # "compare tokens / cost / latency across agents").
        for src, key in (("eval.dim.cost.score", "_cost"), ("eval.dim.latency.score", "_lat"),
                         ("eval.dim.tokens.score", "_tok")):
            val = attrs.get(src)
            if isinstance(val, (int, float)) and val > 0:
                cell[key].append(float(val))
        # How the agent was invoked: directly by the user vs as a genie coordinator's
        # expert. Self-cost accounting means each counts once with no double-count
        # against the coordinator (Agent Ops & Eval §12a).
        if attrs.get("agent.invocation") == "expert":
            cell["expert_turns"] += 1
        else:
            cell["direct_turns"] += 1
        failed = turn_failed(attrs)
        if failed:
            cell["failures"] += 1
        cell["series"].append(0 if failed else 1)
        # A measured turn where the human and the scorers point opposite ways — the
        # one place a human says the bands are miscalibrated. Reported, never folded
        # into the rate above it.
        if votes:
            _v = votes.get((attrs.get("session.id"), str(attrs.get("turn.id"))))
            if (_v == "down" and not failed) or (_v == "up" and failed):
                cell["vote_disagreements"] += 1
        # I1 - what the GATES did, alongside what the scorers measured. Without this
        # a guard that refuses (#50) or redacts (#53) is invisible in its own station:
        # the scorers either never see the turn (inbound block is deliberately unscored)
        # or see it AFTER redaction, which is clean by construction.
        _g = cell.setdefault("_gate", {"acted": 0, "parked": 0, "escalated": 0,
                                       "cleared": 0, "blocked": 0, "redacted": 0,
                                       "refused": 0, "errored": 0})
        if attrs.get("eval.gate.acted"):
            _g["acted"] += 1
        if attrs.get("eval.gate.parked"):
            _g["parked"] += 1
        if attrs.get("eval.gate.escalated"):
            _g["escalated"] += 1
        if attrs.get("eval.gate.cleared"):
            _g["cleared"] += 1
        if attrs.get("eval.gate.grounding") == "block":
            _g["blocked"] += 1
        if attrs.get("eval.gate.outbound") in ("redact", "block"):
            _g["redacted"] += 1
        if attrs.get("eval.gate.inbound") == "block":
            _g["refused"] += 1
        # A guard that COULD NOT RUN is not a guard that passed. The per-turn instrument
        # has always read this honestly (`assurance_points` → `unknown`, "the guard
        # failed — not recorded as a pass"), but the aggregate had no counter for it, so
        # the fact simply vanished at roster altitude. Counted once per turn however many
        # gates errored — the turn is the unit, and on the real occurrence all four
        # errored together from one malformed config block.
        if any(attrs.get(k) == "error" for k in
               ("eval.gate.inbound", "eval.gate.outbound", "eval.gate.grounding")):
            _g["errored"] += 1
        _had_tasks = False
        _span_failed: list = []
        _span_decided: set = set()          # every dim that returned a verdict on THIS turn
        for k, v in attrs.items():
            if k.startswith("eval.dim.") and k.endswith(".passed") and v is not None:
                dim = k[len("eval.dim."):-len(".passed")]
                _span_decided.add(dim)
                acc = cell["_dims"].setdefault(dim, [0, 0])
                acc[1] += 1
                if v:
                    acc[0] += 1
                else:
                    _span_failed.append(dim)
                # Trailing per-dimension pass/fail series (oldest→newest) for the
                # roster micro-sparklines: this verdict vs the agent's own history.
                # MUST record every decided verdict, pass AND fail — this append
                # once sat inside `if v:`, so only passes were ever kept and every
                # sparkline was all-green by construction (a dimension at 25%
                # rendered a single green tick). The series is the trend signal
                # behind the rate; a trend that cannot fall says nothing.
                cell["_dim_series"].setdefault(dim, []).append(1 if v else 0)
            elif k.startswith("eval.task.") and isinstance(v, (int, float)):
                cls = k[len("eval.task."):]
                cell["_tasks"][cls] = cell["_tasks"].get(cls, 0) + v
                _had_tasks = True
        # #62b/#63 — the PER-TURN record the per-dim aggregates cannot reconstruct:
        # which checks failed on this turn (empty list = the turn was clean on every
        # check that ran). `dimension_series` appends only when a dim was DECIDED, so
        # series across dims do NOT align by index — a sampled judge's index 0 is a
        # different turn than a full-window dim's index 0. Any per-turn union ("X of
        # N turns clean") or corroboration reading ("failed on more than one check")
        # must come from THIS structure, never from zipping the per-dim series.
        # Gate-only spans never reach here (they `continue` above): a refused turn is
        # the gate's work, not a scored turn.
        cell["_turn_fails"].append(sorted(_span_failed))
        # Rule 3.0c — a coarse check that failed while the judge REFINING it decided and
        # PASSED is DISPUTED, not a failure: heavy paraphrase or a lenient judge, either
        # way a human should glance. Computed here because this is the only place the
        # per-turn decided set exists — "the judge passed" and "the judge never ran" are
        # different facts and the aggregate cannot tell them apart afterwards.
        _disputed = sorted(
            c for c, j in _REFINEMENT_PAIRS.items()
            if c in _span_failed and j in _span_decided and j not in _span_failed)
        cell["_turn_disputed"].append(_disputed)
        if _had_tasks:
            cell["_task_turns"] += 1
    for cell in by.values():
        t = cell["turns"]
        cell["pass_rate"] = round((t - cell["failures"]) / t, 4) if t else None
        cell["series"] = cell["series"][-30:]  # cap sparkline length
        # I1 — the gates' own record, published beside the scorers'. `gate_turns` is
        # the denominator that MATTERS for these counts: turns whose gates recorded an
        # outcome at all (a deployment with the masters off has none, and the key is
        # then absent rather than a misleading zero).
        _gate = cell.pop("_gate", None)
        if _gate and any(_gate.values()):
            cell["gate"] = _gate
        _dims = cell.pop("_dims")
        cell["dimensions"] = {d: round(c[0] / c[1], 4) if c[1] else None
                              for d, c in _dims.items()}
        # Per-dimension decided-verdict count (the denominator behind each bar).
        # Deterministic dims are decided every turn; judge dims only on sampled
        # turns — so a judge dim can read 0%/100% off a single turn. The roster
        # uses this to flag low-sample dimensions rather than showing a stark
        # rate with a hidden n=1 denominator.
        cell["dimension_counts"] = {d: c[1] for d, c in _dims.items()}
        # Trailing per-dimension pass/fail series (last 20) for the roster
        # micro-sparklines — situates each dimension's rate against its own trend.
        cell["dimension_series"] = {d: s[-20:] for d, s in cell.pop("_dim_series").items()}
        # #62b — same trailing window as the sparklines; oldest→newest.
        cell["turn_failed_dims"] = cell.pop("_turn_fails")[-20:]
        cell["turn_disputed_dims"] = cell.pop("_turn_disputed")[-20:]
        # Average task composition (per-class mean over turns that produced tasks) —
        # the work-mix transparency for the Agent-performance lens.
        tt = cell.pop("_task_turns")
        comp = {c: round(s / tt, 1) for c, s in cell.pop("_tasks").items()} if tt else {}
        cell["task_composition"] = comp
        # Resource + work-volume averages for the comparison metric picker.
        _cost = cell.pop("_cost"); _lat = cell.pop("_lat"); _tok = cell.pop("_tok")
        cell["avg_cost"] = round(sum(_cost) / len(_cost), 6) if _cost else 0.0
        cell["avg_latency_ms"] = round(sum(_lat) / len(_lat)) if _lat else 0
        cell["avg_tokens"] = round(sum(_tok) / len(_tok)) if _tok else 0
        cell["avg_tasks"] = round(sum(comp.values()), 1) if comp else 0
    return by


_IFOC_LABEL = {"llm_only": "Ideate", "conversation_with_tools": "Ideate+Tools",
               "rag_focused": "Focus", "tool_enabled": "Optimize", "genie": "Coordinate"}


def agent_kpi_dashboard(spans: list[dict], *, recent: int = 60, top_checks: int = 8,
                        healthy_floor: float = 0.8) -> dict:
    """Turn the quality spans into actionable, chart-ready KPIs across all agents.

    Returns:
      overall        — turns, pass_rate, fail_turns, avg_cost, avg_latency_ms
      recent[]       — chronological per-turn series (passed/cost/latency/tag) for trend lines
      failing_checks — per-dimension failure rate (sorted worst-first) → "what to fix"
      agents[]       — per-agent scoreboard: pass_rate, turns, avg_cost, avg_latency_ms, status
    Pure / CI-testable. Excludes flow spans (flows are their own lens).
    """
    # I1 (same rule as ``online_quality_by_agent``): a GATE-ONLY span records a refused
    # turn — the gate acted, nothing was measured. With no dimension bands it carried
    # zero convictions and so counted as a PASS here while the roster kept it out of
    # its denominator: the same agent read two pass rates on one page.
    turn_spans = [s for s in spans
                  if (s.get("attributes") or {}).get("agent.kind") != "flow"
                  and not (s.get("attributes") or {}).get("eval.gate_only")]

    def _attr(s):
        return s.get("attributes") or {}

    # ── overall + recent series ───────────────────────────────────────────────
    n = len(turn_spans)
    fail_turns = sum(1 for s in turn_spans if turn_failed(_attr(s)))
    costs = [float(_attr(s).get("eval.dim.cost.score") or 0) for s in turn_spans]
    lats = [float(_attr(s).get("eval.dim.latency.score") or 0) for s in turn_spans]
    cost_vals = [c for c in costs if c > 0]
    lat_vals = [l for l in lats if l > 0]
    overall = {
        "turns": n,
        "pass_rate": round((n - fail_turns) / n, 4) if n else None,
        "fail_turns": fail_turns,
        "avg_cost": round(sum(cost_vals) / len(cost_vals), 6) if cost_vals else 0.0,
        "avg_latency_ms": round(sum(lat_vals) / len(lat_vals)) if lat_vals else 0,
    }
    recent_series = []
    for i, s in enumerate(turn_spans[-recent:]):
        a = _attr(s)
        recent_series.append({
            "i": i,
            "passed": not turn_failed(a),
            "cost": round(float(a.get("eval.dim.cost.score") or 0), 6),
            "latency_ms": round(float(a.get("eval.dim.latency.score") or 0)),
            "tag": a.get("agent.profile_tag"),
        })

    # ── failing checks (per-dimension failure rate) ───────────────────────────
    dim_agg: dict = {}
    for s in turn_spans:
        for k, v in _attr(s).items():
            if k.startswith("eval.dim.") and k.endswith(".passed") and v is not None:
                dim = k[len("eval.dim."):-len(".passed")]
                cell = dim_agg.setdefault(dim, [0, 0])  # [fails, decided]
                cell[1] += 1
                if v is False:
                    cell[0] += 1
    failing = [{"dimension": d, "fail_rate": round(c[0] / c[1], 4), "fails": c[0], "n": c[1]}
               for d, c in dim_agg.items() if c[1]]
    failing.sort(key=lambda x: (-x["fail_rate"], -x["fails"]))
    failing = [f for f in failing if f["fails"] > 0][:top_checks]

    # ── per-agent scoreboard ──────────────────────────────────────────────────
    by_tag: dict = {}
    for s in turn_spans:
        a = _attr(s)
        tag = a.get("agent.profile_tag")
        if not tag:
            continue
        cell = by_tag.setdefault(tag, {"tag": tag, "profile_class": a.get("eval.profile_class"),
                                       "turns": 0, "fails": 0, "_cost": [], "_lat": []})
        cell["turns"] += 1
        if turn_failed(a):
            cell["fails"] += 1
        c = float(a.get("eval.dim.cost.score") or 0)
        l = float(a.get("eval.dim.latency.score") or 0)
        if c > 0:
            cell["_cost"].append(c)
        if l > 0:
            cell["_lat"].append(l)
    agents = []
    for cell in by_tag.values():
        t = cell["turns"]
        pr = round((t - cell["fails"]) / t, 4) if t else None
        cost = cell.pop("_cost"); lat = cell.pop("_lat")
        agents.append({
            "tag": cell["tag"],
            "label": _IFOC_LABEL.get(cell["profile_class"], cell["profile_class"] or "Agent"),
            "profile_class": cell["profile_class"],
            "turns": t, "pass_rate": pr,
            "avg_cost": round(sum(cost) / len(cost), 6) if cost else 0.0,
            "avg_latency_ms": round(sum(lat) / len(lat)) if lat else 0,
            "status": "healthy" if (pr is not None and pr >= healthy_floor)
                      else ("attention" if pr is not None else "unevaluated"),
        })
    agents.sort(key=lambda x: (x["pass_rate"] if x["pass_rate"] is not None else 1, -x["turns"]))

    return {"overall": overall, "recent": recent_series,
            "failing_checks": failing, "agents": agents}


def agent_timeline(spans: list[dict], tag: str, *, bucket_seconds: int = 86400,
                   recent: int = 80) -> dict:
    """Track ONE agent's performance over time. Buckets that agent's spans by day
    (default) into pass_rate / avg_cost / avg_latency / volume, and also returns the
    raw per-turn series (for a fine-grained trend when the window is short).
    Pure / CI-testable. ``ts`` is unix seconds (formatting is the caller's job)."""
    from datetime import datetime, timezone

    rows = [s for s in spans
            if (s.get("attributes") or {}).get("agent.profile_tag") == tag
            and (s.get("attributes") or {}).get("agent.kind") != "flow"]

    series = []
    buckets: dict = {}
    for s in rows:
        a = s.get("attributes") or {}
        ns = s.get("start_time_unix_nano") or 0
        sec = (ns / 1e9) if ns else 0
        passed = not turn_failed(a)
        cost = float(a.get("eval.dim.cost.score") or 0)
        lat = float(a.get("eval.dim.latency.score") or 0)
        series.append({"passed": passed, "cost": round(cost, 6),
                       "latency_ms": round(lat), "ts": int(sec)})
        if sec:
            b = int(sec // bucket_seconds * bucket_seconds)
            cell = buckets.setdefault(b, {"ts": b, "turns": 0, "fails": 0, "_cost": [], "_lat": []})
            cell["turns"] += 1
            if not passed:
                cell["fails"] += 1
            if cost > 0:
                cell["_cost"].append(cost)
            if lat > 0:
                cell["_lat"].append(lat)

    timeline = []
    for b in sorted(buckets):
        c = buckets[b]
        t = c["turns"]
        cost = c.pop("_cost"); lat = c.pop("_lat")
        timeline.append({
            "ts": b,
            "label": datetime.fromtimestamp(b, tz=timezone.utc).strftime("%b %d"),
            "turns": t,
            "pass_rate": round((t - c["fails"]) / t, 4) if t else None,
            "avg_cost": round(sum(cost) / len(cost), 6) if cost else 0.0,
            "avg_latency_ms": round(sum(lat) / len(lat)) if lat else 0,
        })

    return {"tag": tag, "turns": len(rows), "timeline": timeline, "recent": series[-recent:]}


def online_quality_by_flow(spans: list[dict]) -> dict:
    """Per-flow-agent online quality, keyed on flow_id. Mirrors by_agent but for
    `agent.kind == 'flow'` spans (a flow is its own agent type)."""
    by: dict = {}
    for sp in spans:
        attrs = sp.get("attributes") or {}
        if attrs.get("agent.kind") != "flow":
            continue
        fid = attrs.get("agent.flow_id")
        if not fid:
            continue
        cell = by.setdefault(fid, {"flow_id": fid, "name": attrs.get("agent.flow_name"),
                                   "turns": 0, "failures": 0, "series": [], "_dims": {},
                                   "_tasks": {}, "_task_turns": 0})
        # Owner attribution (flow ids are globally unique — no key change needed;
        # the admin system view uses this for the card's user pill).
        if attrs.get("user.uuid") and not cell.get("user_uuid"):
            cell["user_uuid"] = attrs.get("user.uuid")
        cell["turns"] += 1
        failed = turn_failed(attrs)
        if failed:
            cell["failures"] += 1
        cell["series"].append(0 if failed else 1)
        _had_tasks = False
        for k, v in attrs.items():
            if k.startswith("eval.dim.") and k.endswith(".passed") and v is not None:
                dim = k[len("eval.dim."):-len(".passed")]
                acc = cell["_dims"].setdefault(dim, [0, 0])
                acc[1] += 1
                if v:
                    acc[0] += 1
            elif k.startswith("eval.task.") and isinstance(v, (int, float)):
                cls = k[len("eval.task."):]
                cell["_tasks"][cls] = cell["_tasks"].get(cls, 0) + v
                _had_tasks = True
        if _had_tasks:
            cell["_task_turns"] += 1
    for cell in by.values():
        t = cell["turns"]
        cell["pass_rate"] = round((t - cell["failures"]) / t, 4) if t else None
        cell["series"] = cell["series"][-30:]
        _dims = cell.pop("_dims")
        cell["dimensions"] = {d: round(c[0] / c[1], 4) if c[1] else None
                              for d, c in _dims.items()}
        cell["dimension_counts"] = {d: c[1] for d, c in _dims.items()}
        tt = cell.pop("_task_turns")
        cell["task_composition"] = ({c: round(s / tt, 1) for c, s in cell.pop("_tasks").items()}
                                    if tt else {})
    return by


def online_pass_rate_by_dimension(spans: list[dict]) -> dict:
    pseudo: list[dict] = []
    for sp in spans:
        attrs = sp.get("attributes") or {}
        for k, v in attrs.items():
            if k.startswith("eval.dim.") and k.endswith(".passed") and v is not None:
                dim = k[len("eval.dim."):-len(".passed")]
                pseudo.append({"dimension": dim, "passed": bool(v)})
    return pass_rate_by_dimension(pseudo)


# ── station shortfalls (PROP-002b — the red⇒item direction) ──────────────────
def station_shortfalls(by_agent: dict, *, review_floor: float,
                       station_dims: dict) -> list:
    """One ack-able observation per (agent, station) whose execution sits in the
    DANGER band — the red⇒item half of the completeness invariant (contract §11):
    a segment that reads *needs you now* from a failing rate must resolve to an
    open inbox item, not just a colour.

    Pure. Banding is WORST-OWNED-DIMENSION against the shared review floor
    (danger = rate < review floor) — the conservative subset of the ring's
    per-turn-union banding, so an item never files where the ring would not read
    at least as bad. Severity is always ``danger`` (the construct's floor: never
    filterable by a reporting level). The ack key is content-derived
    (``station_shortfall_<station>::<tag>``) so acknowledging one agent's
    shortfall can never mark another's as seen — an ack means *seen*, never
    *fixed*: the rate stays red until the turns improve.
    """
    from trusted_data_agent.agent.assurance_points import station_label as _station_label
    out = []
    for key, cell in (by_agent or {}).items():
        if not isinstance(cell, dict):
            continue
        dims = cell.get("dimensions") or {}
        counts = cell.get("dimension_counts") or {}
        tag = cell.get("tag") or str(key).split("::")[0]
        if not tag:
            continue
        for station, keys in (station_dims or {}).items():
            worst = None
            for k in (keys or ()):
                r = dims.get(k)
                if isinstance(r, (int, float)) and (worst is None or r < worst[1]):
                    worst = (k, float(r), counts.get(k))
            if not worst or worst[1] >= review_floor:
                continue
            n = worst[2] if isinstance(worst[2], int) and worst[2] > 0 else None
            out.append({
                "session_id": None, "turn_id": None, "profile_tag": tag,
                "event_type": f"station_shortfall_{station}::{tag}",
                "severity": "danger",
                "summary": (f"{_station_label(station)} execution "
                            f"below the danger floor for @{tag} — worst check at "
                            f"{round(worst[1] * 100)}%"
                            + (f" over {n} turn{'s' if n != 1 else ''}" if n else "")),
                "payload": {"station": station, "dimension": worst[0],
                            "rate": worst[1], "n": n},
                "source": "derived",
            })
    return out


# ── experiment diffing ────────────────────────────────────────────────────────
def diff_runs(summary_a: Optional[dict], summary_b: Optional[dict],
              epsilon: float = 1e-9) -> dict:
    """Diff two run summaries (A = baseline, B = candidate).

    Returns pass^k delta plus per-dimension / per-cohort / per-profile-class
    deltas, split into regressions and improvements (|delta| > epsilon).
    """
    a = summary_a or {}
    b = summary_b or {}

    def _delta_map(key: str) -> dict:
        ma, mb = a.get(key) or {}, b.get(key) or {}
        out = {}
        for name in set(ma) | set(mb):
            va, vb = ma.get(name), mb.get(name)
            if va is None or vb is None:
                out[name] = {"baseline": va, "candidate": vb, "delta": None}
            else:
                out[name] = {"baseline": va, "candidate": vb, "delta": round(vb - va, 4)}
        return out

    pk_a, pk_b = a.get("pass_k"), b.get("pass_k")
    pk_delta = (round(pk_b - pk_a, 4) if pk_a is not None and pk_b is not None else None)

    dims = _delta_map("per_dimension")
    regressions = sorted(
        [{"dimension": d, **v} for d, v in dims.items()
         if v["delta"] is not None and v["delta"] < -epsilon],
        key=lambda x: x["delta"])
    improvements = sorted(
        [{"dimension": d, **v} for d, v in dims.items()
         if v["delta"] is not None and v["delta"] > epsilon],
        key=lambda x: x["delta"], reverse=True)
    return {
        "pass_k": {"baseline": pk_a, "candidate": pk_b, "delta": pk_delta},
        "per_dimension": dims,
        "per_cohort_pass_k": _delta_map("per_cohort_pass_k"),
        "per_profile_class": _delta_map("per_profile_class"),
        "regressions": regressions,
        "improvements": improvements,
    }


_OK_RUN = frozenset({"success", "ok", "complete", "completed"})


_LIBRARY_CELLS = ("read_ran", "read_failed", "unread_ran", "unread_failed", "read_unused")


def library_outcomes(spans: list[dict], tag: Optional[str] = None) -> dict:
    """Read → ran per skill-library SECTION across turns (SKILL_ARCHITECTURE §4a).

    Sums the ``eval.library.<anchor>.<cell>`` span attributes (`skills.library_use`) per
    anchor, optionally for one agent. Per section: how many statements using its operators
    came after a read (and ran / failed), how many did not (and ran / failed), and how often
    it was read without any statement using it. The two rates that decide whether forcing a
    read would pay — success after a read vs without one — are computed only where both
    sides have statements; a rate over nothing is not reported as one."""
    sections: Dict[str, Dict[str, int]] = {}
    turns = 0
    for sp in spans or []:
        attrs = sp.get("attributes") or {}
        if tag and attrs.get("agent.profile_tag") != tag:
            continue
        hit = False
        for k, v in attrs.items():
            if not k.startswith("eval.library.") or not isinstance(v, (int, float)):
                continue
            anchor, _, cell = k[len("eval.library."):].rpartition(".")
            if cell not in _LIBRARY_CELLS or not anchor:
                continue
            sections.setdefault(anchor, {c: 0 for c in _LIBRARY_CELLS})[cell] += int(v)
            hit = True
        turns += 1 if hit else 0
    out = {}
    for anchor, c in sorted(sections.items()):
        read_n = c["read_ran"] + c["read_failed"]
        unread_n = c["unread_ran"] + c["unread_failed"]
        out[anchor] = dict(c, statements=read_n + unread_n,
                           read_first_rate=round(read_n / (read_n + unread_n), 3) if read_n + unread_n else None,
                           ran_after_read=round(c["read_ran"] / read_n, 3) if read_n else None,
                           ran_without_read=round(c["unread_ran"] / unread_n, 3) if unread_n else None)
    return {"turns": turns, "sections": out, "tag": tag}


def agent_runs(spans: list[dict], tag: str, *, limit: int = 80,
               votes: dict | None = None) -> dict:
    """All scored runs for ONE agent (newest first), each with its full per-dimension
    verdicts — the data behind the "Runs Ledger" (every score per agent per run).

    A coordinator run additionally carries its expert (slave) children as a nested
    tree (the master/slave trace): each expert is matched to its own scored span by
    session id (so its real verdicts/band show), with timing for the waterfall. When
    an expert wasn't scored as its own span, a best-effort node is synthesised from
    the coordinator's persisted expert summary. SELF-cost throughout: each node's
    cost is its own; ``rollup_*`` are display-only inclusive totals.

    Pure / CI-testable. ``ts`` is unix seconds (formatting is the caller's job).
    """
    by_session: dict = {}
    for s in spans:
        a = s.get("attributes") or {}
        sid = a.get("session.id")
        if sid and sid not in by_session:
            by_session[sid] = s

    def _verdicts(sp) -> list:
        """A span's verdicts, each with `gating` RESOLVED against the current registry.

        Persisted verdict events carry no `gating` key — the flag became a derived
        property after these spans were written. Without back-filling it here, a
        consumer asking "was this failure gate-eligible?" gets `undefined` and has to
        guess, which is how the runs ledger came to convict a whole run on an advisory
        check while the headline above it counted 7. Same back-fill the turn-score
        endpoint already performs when replaying an older span.
        """
        from trusted_data_agent.eval.scorer import is_gating_dimension
        out = []
        for e in (sp.get("events") or []):
            if not (isinstance(e, dict) and e.get("name") == "verdict"
                    and isinstance(e.get("attributes"), dict)):
                continue
            v = dict(e["attributes"])
            if "gating" not in v:
                v["gating"] = is_gating_dimension(v.get("dimension"))
            out.append(v)
        return out

    def _shape(sp, depth=0):
        a = sp.get("attributes") or {}
        ns = sp.get("start_time_unix_nano") or 0
        run = {
            "session_id": a.get("session.id"),
            "turn_id": a.get("turn.id"),
            "ts": int(ns / 1e9) if ns else 0,
            "profile_class": a.get("eval.profile_class"),
            "profile_tag": a.get("agent.profile_tag"),
            "invocation": a.get("agent.invocation", "direct"),
            "convictions": conviction_count(a),
            "disputed": list(judge_cleared(a)),
            "judge_flagged": list(judge_flagged(a)),
            "passed": not turn_failed(a),
            "cost": float(a.get("eval.dim.cost.score") or 0),
            "latency_ms": float(a.get("eval.dim.latency.score") or 0),
            "tokens": float(a.get("eval.dim.tokens.score") or 0),
            "judged": bool(a.get("eval.judged")),
            "verdicts": _verdicts(sp),
            # #44 — the ledger described a turn by its SCORES alone, so a turn two judges
            # adjudicated and the gate WITHHELD was labelled "deterministic". The gate's
            # outcome now travels on the span (I1); the row carries it so the surface
            # built to explain a turn can name the thing that defined it.
            "gate": {k[len("eval.gate."):]: v for k, v in a.items()
                     if k.startswith("eval.gate.") and v is not None} or None,
            "gate_only": bool(a.get("eval.gate_only")),
            # …and WHICH dimensions decided the verdict, so a reader is not left
            # scanning twenty chips for the red ones.
            "failed_dimensions": sorted(
                k[len("eval.dim."):-len(".passed")] for k, v in a.items()
                if k.startswith("eval.dim.") and k.endswith(".passed") and v is False),
        }
        # The user's own rating of this run, when the caller supplied their vote map.
        # A PREFERENCE shown beside the verdict — it must never move `passed`, the
        # convictions, or the row's band (Rule 3bb). Absent when unvoted.
        if votes:
            _v = votes.get((run["session_id"], str(run["turn_id"])))
            if _v:
                run["vote"] = _v
        experts = a.get("eval.experts") if depth == 0 else None
        if experts:
            kids = []
            for ex in experts:
                if not isinstance(ex, dict):
                    continue
                esid = ex.get("slave_session_id")
                child = by_session.get(esid) if esid else None
                if child:
                    kid = _shape(child, depth + 1)
                else:  # expert not scored as its own span — synthesise from the summary
                    kid = {
                        "session_id": esid, "turn_id": None,
                        "profile_tag": ex.get("profile_tag"),
                        "profile_class": ex.get("profile_type"),
                        "invocation": "expert",
                        "passed": str(ex.get("status", "success")).lower() in _OK_RUN,
                        "cost": float(ex.get("cost_usd") or 0),
                        "latency_ms": float(ex.get("duration_ms") or 0),
                        "tokens": int(ex.get("input_tokens") or 0) + int(ex.get("output_tokens") or 0),
                        "verdicts": [], "synthetic": True,
                    }
                # authoritative timing from the coordinator's expert summary (offsets)
                kid["started_at"] = ex.get("started_at")
                kid["completed_at"] = ex.get("completed_at")
                kid["duration_ms"] = ex.get("duration_ms")
                kid["expert_status"] = ex.get("status")
                kids.append(kid)
            run["experts"] = kids
            run["expert_count"] = len(kids)
            run["rollup_cost"] = round(run["cost"] + sum(k.get("cost", 0) for k in kids), 6)
            run["rollup_tokens"] = run["tokens"] + sum(k.get("tokens", 0) for k in kids)
        return run

    rows = [_shape(s) for s in spans
            if (s.get("attributes") or {}).get("agent.profile_tag") == tag
            and (s.get("attributes") or {}).get("agent.kind") != "flow"]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return {"tag": tag, "count": len(rows), "runs": rows[:limit]}
