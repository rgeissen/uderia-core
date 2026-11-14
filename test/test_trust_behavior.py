"""CI-pure tests for eval/trust_behavior.py — the Behavioral Trust Score.

No server, no DB, no clock: spans are inline dicts (the same OTLP-shaped quality
spans eval/analysis.py consumes), time is injected via now_ns.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.trust_behavior import (  # noqa: E402
    DEFAULTS, resolve_config, behavior_for_spans, breaker_decision, compose,
    assess_agents)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


_NS_PER_DAY = 86_400 * 1_000_000_000
NOW = 1_000 * _NS_PER_DAY  # deterministic "now"


def _sp(tag="A", passed=True, age_days=0.0, dims=None, kind=None):
    """Build a minimal quality span. dims = {dim_name: passed_bool}."""
    attrs = {"agent.profile_tag": tag,
             "eval.gating_failures": 0 if passed else 1}
    if kind:
        attrs["agent.kind"] = kind
    for d, p in (dims or {}).items():
        attrs[f"eval.dim.{d}.passed"] = bool(p)
    ts = NOW - int(age_days * _NS_PER_DAY)
    return {"attributes": attrs, "start_time_unix_nano": ts - 1_000,
            "end_time_unix_nano": ts}


# ── config ────────────────────────────────────────────────────────────────────
def t_config():
    cfg = resolve_config(None)
    check("defaults returned", cfg == DEFAULTS and cfg is not DEFAULTS)
    cfg = resolve_config({"healthy_floor": 0.7, "bogus_key": 1, "enabled": None})
    check("override merges known keys", cfg["healthy_floor"] == 0.7)
    check("unknown keys dropped", "bogus_key" not in cfg)
    check("None override ignored", cfg["enabled"] is DEFAULTS["enabled"])


# ── behavior ladder ───────────────────────────────────────────────────────────
def t_provisional():
    b = behavior_for_spans([], now_ns=NOW)
    check("no evidence → provisional", b["state"] == "provisional")
    check("no evidence → score None", b["score"] is None and b["turns"] == 0)
    b = behavior_for_spans([_sp(passed=True) for _ in range(5)], now_ns=NOW)
    check("below min_turns_proven → provisional", b["state"] == "provisional")
    check("turns counted", b["turns"] == 5)


def t_proven_and_trusted():
    b = behavior_for_spans([_sp(passed=True, age_days=i) for i in range(12)], now_ns=NOW)
    check("12 clean turns → proven (volume-gated below trusted)", b["state"] == "proven")
    b = behavior_for_spans([_sp(passed=True, age_days=i * 0.5) for i in range(30)], now_ns=NOW)
    check("30 clean recent turns → trusted", b["state"] == "trusted")
    check("trusted score ~1.0", b["score"] is not None and b["score"] > 0.99)


def t_bands():
    # ~40% pass → at_risk
    spans = [_sp(passed=(i % 5 < 2), age_days=1) for i in range(20)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("40% pass → at_risk", b["state"] == "at_risk")
    # ~70% pass → watch
    spans = [_sp(passed=(i % 10 < 7), age_days=1) for i in range(20)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("70% pass → watch", b["state"] == "watch")


def t_recency_weighting():
    # Old failures + recent passes should score higher than the reverse.
    old_fail = [_sp(passed=False, age_days=60) for _ in range(10)] + \
               [_sp(passed=True, age_days=1) for _ in range(10)]
    recent_fail = [_sp(passed=True, age_days=60) for _ in range(10)] + \
                  [_sp(passed=False, age_days=1) for _ in range(10)]
    b_old = behavior_for_spans(old_fail, now_ns=NOW)
    b_recent = behavior_for_spans(recent_fail, now_ns=NOW)
    check("recent conduct dominates", b_old["score"] > b_recent["score"])
    check("recent failures pull below healthy", b_recent["state"] in ("at_risk", "watch"))


def t_trust_dims():
    # Same gating pass rate, but trust-critical dims failing → lower score.
    clean = [_sp(passed=True, age_days=1, dims={"scope_adherence": True}) for _ in range(15)]
    dirty = [_sp(passed=True, age_days=1, dims={"scope_adherence": False}) for _ in range(15)]
    b_clean = behavior_for_spans(clean, now_ns=NOW)
    b_dirty = behavior_for_spans(dirty, now_ns=NOW)
    check("trust-dim failures lower the score", b_dirty["score"] < b_clean["score"])
    check("trust_dim_rate surfaces", b_dirty["trust_dim_rate"] == 0.0 and b_clean["trust_dim_rate"] == 1.0)
    check("non-trust dims ignored",
          behavior_for_spans([_sp(passed=True, dims={"latency": False})] * 12,
                             now_ns=NOW)["trust_dim_rate"] is None)


def t_partition_blend():
    """The blend PARTITIONS the evidence — no dimension is counted twice.

    Before the partition the first term was the WHOLE-TURN pass rate, so a
    trust-critical failure was counted twice: once failing the turn, once lowering
    the critical rate. Now: score = (1-w)·residual + w·critical, and the whole-turn
    rate is only the fallback for spans with no per-dimension evidence at all.
    """
    w = DEFAULTS["trust_dim_weight"]
    # A critical failure on an otherwise-clean turn: exact one-count arithmetic.
    # residual (latency, completed) = 1.0 · critical (scope_adherence) = 0.0
    spans = [_sp(passed=False, age_days=1,
                 dims={"scope_adherence": False, "latency": True, "completed": True})
             for _ in range(15)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("residual_rate surfaces", b["residual_rate"] == 1.0)
    check("critical failure counted ONCE: score = (1-w)*1.0 + w*0.0 exactly",
          abs(b["score"] - (1.0 - w)) < 1e-6)
    # Under the old double count the whole-turn rate (0.0) dragged the first term:
    # score would have been 0.0. The partition keeps the residual term clean.
    check("…not the double-counted 0.0 the union blend produced", b["score"] > 0.5)
    # The mirrored case: a residual failure must not touch the critical term.
    spans = [_sp(passed=False, age_days=1,
                 dims={"scope_adherence": True, "latency": False})
             for _ in range(15)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("residual failure counted once: score = (1-w)*0.0 + w*1.0 exactly",
          abs(b["score"] - w) < 1e-6)
    # No per-dim evidence at all → honest fallback to the whole-turn rate.
    spans = [_sp(passed=(i % 2 == 0), age_days=1) for i in range(16)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("no per-dim evidence → whole-turn fallback",
          b["residual_rate"] is None and b["trust_dim_rate"] is None
          and abs(b["score"] - b["weighted_pass_rate"]) < 1e-9)
    # Only critical dims recorded → the critical term IS the score (no fabricated
    # residual), and vice versa.
    b = behavior_for_spans([_sp(passed=False, age_days=1,
                                dims={"scope_adherence": False})] * 15, now_ns=NOW)
    check("critical-only evidence → score is the critical rate",
          b["residual_rate"] is None and b["score"] == 0.0)
    b = behavior_for_spans([_sp(passed=True, age_days=1,
                                dims={"latency": True})] * 15, now_ns=NOW)
    check("residual-only evidence → score is the residual rate",
          b["trust_dim_rate"] is None and b["score"] == 1.0)


def t_staleness():
    # Excellent but old evidence: trusted must decay to proven + stale flag.
    spans = [_sp(passed=True, age_days=40 + i * 0.1) for i in range(30)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("stale flagged", b["stale"] is True)
    check("stale caps trusted → proven", b["state"] == "proven")
    # Fresh evidence: not stale.
    spans = [_sp(passed=True, age_days=i * 0.1) for i in range(30)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("fresh evidence not stale", b["stale"] is False and b["state"] == "trusted")


def t_window_and_trend():
    # 60 old failures + 50 recent passes with window 50 → only passes counted.
    spans = [_sp(passed=False, age_days=30 + i * 0.01) for i in range(60)] + \
            [_sp(passed=True, age_days=i * 0.01) for i in range(50)]
    b = behavior_for_spans(spans, now_ns=NOW)
    check("window keeps newest 50", b["turns"] == 50)
    check("windowed score clean", b["score"] > 0.99)
    check("trend capped at 30", len(b["trend"]) == 30)
    check("trend is pass/fail ints", set(b["trend"]) <= {0, 1})


def t_untimestamped_and_flow():
    sp = _sp(passed=True)
    del sp["end_time_unix_nano"]
    del sp["start_time_unix_nano"]
    b = behavior_for_spans([sp] * 12, now_ns=NOW)
    check("untimestamped spans still count (neutral weight)",
          b["turns"] == 12 and b["state"] == "proven")
    flow = [_sp(passed=False, kind="flow") for _ in range(20)]
    b = behavior_for_spans(flow + [_sp(passed=True, age_days=1)] * 12, now_ns=NOW)
    check("flow spans excluded", b["turns"] == 12 and b["state"] == "proven")


def t_deterministic_default_now():
    spans = [_sp(passed=True, age_days=i) for i in range(30)]
    b1 = behavior_for_spans(spans, now_ns=None)
    b2 = behavior_for_spans(list(spans), now_ns=None)
    check("default now anchored to newest span (reproducible)", b1 == b2)
    check("anchored assessment not stale", b1["stale"] is False)


# ── composite ────────────────────────────────────────────────────────────────
def _beh(state, stale=False):
    return {"state": state, "stale": stale}


def t_compose():
    c = compose("trusted", _beh("trusted"))
    check("trusted+trusted → trusted", c["state"] == "trusted")
    c = compose("unsigned", _beh("trusted"))
    check("unsigned caps trusted → proven", c["state"] == "proven" and "not signed" in c["reason"])
    c = compose("changed", _beh("trusted"))
    check("changed caps → watch", c["state"] == "watch" and "changed" in c["reason"])
    # BE-H3: the fourth integrity state must cap no higher than `unsigned` — a deployment
    # never attested here cannot out-score a portable-signed agent (was: no cap → trusted).
    c = compose("deployment_unattested", _beh("trusted"))
    check("deployment_unattested caps trusted → proven (ladder not inverted)",
          c["state"] == "proven" and "not attested" in c["reason"])
    c = compose("deployment_unattested", _beh("watch"))
    check("deployment_unattested never PROMOTES conduct below the cap", c["state"] == "watch")
    c = compose("trusted", _beh("watch"))
    check("integrity never promotes conduct", c["state"] == "watch")
    c = compose(None, _beh("trusted"))
    check("unknown integrity: no cap, honest reason",
          c["state"] == "trusted" and c["integrity"] is None and c["reason"] == "behavioral")
    c = compose("changed", _beh("provisional"))
    check("provisional passes through with integrity noted",
          c["state"] == "provisional" and c["integrity"] == "changed")
    c = compose("trusted", _beh("proven", stale=True))
    check("stale noted in reason", "stale" in c["reason"])
    c = compose("unsigned", _beh("at_risk"))
    check("cap never raises a bad state", c["state"] == "at_risk")


# ── fleet assessment ─────────────────────────────────────────────────────────
def t_assess_agents():
    spans = ([_sp(tag="GOOD", passed=True, age_days=i * 0.1) for i in range(30)] +
             [_sp(tag="BAD", passed=False, age_days=1) for _ in range(15)] +
             [_sp(tag="FLOWY", passed=False, kind="flow") for _ in range(5)] +
             [{"attributes": {}}])  # untagged → skipped
    out = assess_agents(spans,
                        integrity_by_tag={"GOOD": "trusted", "BAD": "trusted",
                                          "SIGNED_ONLY": "trusted"},
                        now_ns=NOW)
    check("agents grouped by tag", set(out) == {"GOOD", "BAD", "SIGNED_ONLY"})
    check("GOOD composite trusted", out["GOOD"]["composite"]["state"] == "trusted")
    check("BAD composite at_risk despite signature",
          out["BAD"]["composite"]["state"] == "at_risk")
    check("signed-but-never-evaluated appears provisional",
          out["SIGNED_ONLY"]["composite"]["state"] == "provisional"
          and out["SIGNED_ONLY"]["behavior"]["turns"] == 0)
    check("integrity carried through", out["GOOD"]["integrity"]["state"] == "trusted")
    out2 = assess_agents(spans, integrity_by_tag=None, now_ns=NOW)
    check("no integrity map → behavior-only composite",
          out2["GOOD"]["composite"]["state"] == "trusted"
          and out2["GOOD"]["integrity"]["state"] is None)


def t_unsigned_cap_end_to_end():
    spans = [_sp(tag="X", passed=True, age_days=i * 0.1) for i in range(30)]
    out = assess_agents(spans, integrity_by_tag={"X": "unsigned"}, now_ns=NOW)
    check("behaviorally trusted but unsigned → composite proven",
          out["X"]["behavior"]["state"] == "trusted"
          and out["X"]["composite"]["state"] == "proven")


# ── circuit breaker decision ─────────────────────────────────────────────────
def t_assess_agents_per_user():
    """TC-35: system scope must key cells `tag::uuid` — two users' same-tag agents never
    merge into one behavioral cell, and the REQUESTER's integrity must not cap OTHER
    users' conduct. My scope (per_user=False) keeps the historical plain-tag shape."""
    def _usp(tag, uuid, passed=True):
        sp = _sp(tag, passed=passed)
        sp["attributes"]["user.uuid"] = uuid
        return sp

    spans = ([_usp("X", "u-self", True) for _ in range(6)]
             + [_usp("X", "u-other", False) for _ in range(6)])

    # per_user=False (my scope): historical merge behaviour, plain-tag keys, no stamps.
    my = assess_agents(spans, now_ns=NOW)
    check("my scope keys by plain tag", set(my) == {"X"})
    check("my scope cells carry no per-user stamps", "user_uuid" not in my["X"])

    # per_user=True (system scope): two cells, stamped, never merged.
    sys_out = assess_agents(spans, integrity_by_tag={"X": "unsigned"},
                            now_ns=NOW, per_user=True, self_uuid="u-self")
    check("system scope keys tag::uuid", set(sys_out) == {"X::u-self", "X::u-other"})
    check("cells stamped with tag", sys_out["X::u-self"]["tag"] == "X")
    check("cells stamped with user_uuid", sys_out["X::u-other"]["user_uuid"] == "u-other")
    check("same-tag agents never merge (distinct conduct)",
          sys_out["X::u-self"]["behavior"]["weighted_pass_rate"]
          != sys_out["X::u-other"]["behavior"]["weighted_pass_rate"])
    check("requester integrity caps only the requester's own cell",
          sys_out["X::u-self"]["integrity"]["state"] == "unsigned"
          and sys_out["X::u-other"]["integrity"]["state"] is None)

    # Integrity-only (no-span) agents key under the requester in per-user mode.
    only = assess_agents([], integrity_by_tag={"Y": "trusted"},
                         now_ns=NOW, per_user=True, self_uuid="u-self")
    check("integrity-only agent keys under the requester", set(only) == {"Y::u-self"})


def t_breaker_decision():
    # Sustained bad conduct with enough evidence → at_risk → trip.
    bad = behavior_for_spans([_sp(passed=(i % 5 < 2), age_days=1) for i in range(20)], now_ns=NOW)
    d = breaker_decision(bad)
    check("at_risk trips the breaker", d["trip"] is True and "at-risk floor" in d["reason"])
    # A new agent's few bad turns are provisional — volume gate → no trip.
    fresh = behavior_for_spans([_sp(passed=False, age_days=1) for _ in range(5)], now_ns=NOW)
    check("low-evidence bad streak never trips (provisional)",
          fresh["state"] == "provisional" and breaker_decision(fresh)["trip"] is False)
    # Healthy conduct → within budget.
    good = behavior_for_spans([_sp(passed=True, age_days=1) for _ in range(20)], now_ns=NOW)
    d = breaker_decision(good)
    check("healthy conduct within budget", d["trip"] is False and d["reason"] == "within trust budget")
    # Watch (review band) does NOT trip — only Danger exhausts the budget.
    watch = behavior_for_spans([_sp(passed=(i % 10 < 7), age_days=1) for i in range(20)], now_ns=NOW)
    check("watch band does not trip", watch["state"] == "watch"
          and breaker_decision(watch)["trip"] is False)


def main():
    tests = (t_config, t_provisional, t_proven_and_trusted, t_bands,
             t_recency_weighting, t_trust_dims, t_partition_blend,
             t_staleness, t_window_and_trend,
             t_untimestamped_and_flow, t_deterministic_default_now, t_compose,
             t_assess_agents, t_assess_agents_per_user, t_unsigned_cap_end_to_end,
             t_breaker_decision)
    for t in tests:
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
