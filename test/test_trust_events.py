"""CI-pure tests for core/trust_events.py — the Assurance Inbox substrate.

Store tests run against a temp SQLite file (injected db_path); derivation +
sentinel are pure functions over turn dicts.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.trust_events import (  # noqa: E402
    RECEIPT_EVENT_TYPES, SENTINEL_MIN_FINDINGS, _FALLBACK_SEVERITY, ack_derived,
    ack_event, apply_derived_acks, derive_session_sentinel, derive_turn_events,
    derived_ack_index, derived_severity, disposition_index, list_events,
    record_event, unacked_count)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def _tmpdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ── stored events ─────────────────────────────────────────────────────────────
def t_store_roundtrip():
    db = _tmpdb()
    eid = record_event("u1", "trust_breaker", "breaker tripped for @X",
                       severity="danger", profile_tag="X",
                       payload={"score": 0.4}, db_path=db)
    check("record returns id", bool(eid))
    ev = list_events("u1", db_path=db)
    check("event listed", len(ev) == 1 and ev[0]["event_type"] == "trust_breaker")
    check("payload round-trips", ev[0]["payload"] == {"score": 0.4})
    check("severity kept", ev[0]["severity"] == "danger")
    check("source marked stored", ev[0]["source"] == "stored")
    check("unacked count", unacked_count("u1", db_path=db) == 1)
    check("other user sees nothing", list_events("u2", db_path=db) == []
          and unacked_count("u2", db_path=db) == 0)
    # ack
    check("ack succeeds", ack_event("u1", eid, db_path=db) is True)
    check("acked hidden by default", list_events("u1", db_path=db) == [])
    check("acked visible with include_acked",
          len(list_events("u1", include_acked=True, db_path=db)) == 1)
    check("unacked count drops", unacked_count("u1", db_path=db) == 0)
    check("cross-user ack refused", ack_event("u2", eid, db_path=db) is False)
    check("bogus severity normalized",
          list_events("u3", db_path=db) == [] and
          record_event("u3", "x", "s", severity="apocalyptic", db_path=db) is not None and
          list_events("u3", db_path=db)[0]["severity"] == "warning")
    os.unlink(db)


def t_store_fail_open():
    # An unwritable path must not raise — the triggering action is sacrosanct.
    eid = record_event("u1", "x", "s", db_path="/nonexistent/dir/nope.db")
    check("record fails open (None, no raise)", eid is None)
    check("list fails open ([])", list_events("u1", db_path="/nonexistent/dir/nope.db") == [])
    check("count fails open (0)", unacked_count("u1", db_path="/nonexistent/dir/nope.db") == 0)


# ── derived events ────────────────────────────────────────────────────────────
def _turn(n, **stamps):
    return {"turn": n, "profile_tag": "@OPTIM", **stamps}


def t_derive_turn_events():
    turns = [
        _turn(1, grounding={"decision": "block", "out_of_scope_dbs": ["hr_db"]}),
        _turn(2, grounding={"decision": "annotate", "out_of_scope_dbs": ["x"]}),
        _turn(3, grounding={"decision": "pass"}),                       # quiet
        _turn(4, guardrail={"action": "redact", "categories": ["pii"]}),
        _turn(5, guardrail_inbound={"action": "block", "categories": ["prompt_injection"]}),
        _turn(6, honesty={"decision": "block", "failed_query_count": 3}),
        # PROP-002: a flagged honesty finding ACTED (the dial marks it) and
        # therefore derives too — it was invisible to the inbox before.
        _turn(7, honesty={"decision": "flag", "reason": "unsupported"}),
        _turn(8),                                                        # clean
        # PROP-002: `annotate` changed the answer's presentation — it derives.
        _turn(9, guardrail={"action": "annotate", "categories": ["toxicity"]}),
        "junk",                                                          # malformed
    ]
    ev = derive_turn_events(turns, session_id="s1")
    types = sorted(e["event_type"] for e in ev)
    check("seven incidents derived (flag + annotate now derive)", len(ev) == 7)
    check("types correct", types == ["grounding_annotate", "grounding_block",
                                     "guardrail_inbound_block", "guardrail_outbound_annotate",
                                     "guardrail_outbound_redact",
                                     "honesty_flag", "honesty_rewrite"])
    blk = next(e for e in ev if e["event_type"] == "grounding_block")
    check("block is danger + carries scope", blk["severity"] == "danger"
          and blk["payload"]["out_of_scope_dbs"] == ["hr_db"])
    # PROP-002 — ONE severity map: the dial reads an honesty block as danger, so
    # the inbox must carry danger for the identical stamp (it was `warning`).
    hr = next(e for e in ev if e["event_type"] == "honesty_rewrite")
    check("honesty rewrite severity agrees with the dial (danger)",
          hr["severity"] == "danger")
    check("honesty flag is warning",
          next(e for e in ev if e["event_type"] == "honesty_flag")["severity"] == "warning")
    check("annotate is warning",
          next(e for e in ev if e["event_type"] == "guardrail_outbound_annotate")["severity"] == "warning")
    check("session id threaded", all(e["session_id"] == "s1" for e in ev))
    check("profile tag stripped of @", all(e["profile_tag"] == "OPTIM" for e in ev))
    check("source marked derived", all(e["source"] == "derived" for e in ev))
    check("pass/clean stay quiet", not any(e["turn_id"] in (3, 8) for e in ev))


def t_severity_single_source():
    # PROP-002 — the canonical map lives beside the assurance-points registry;
    # the fallback literal here must be IDENTICAL (the "five hand-rolled
    # ladders" defect class, on severities).
    from trusted_data_agent.agent.assurance_points import DERIVED_EVENT_SEVERITY
    check("fallback severity map equals the registry's canonical map",
          _FALLBACK_SEVERITY == DERIVED_EVENT_SEVERITY)
    check("derived_severity reads the map", derived_severity("honesty_rewrite") == "danger"
          and derived_severity("grounding_annotate") == "warning")
    check("unknown event type defaults to warning",
          derived_severity("never_heard_of_it") == "warning")


def t_reporting_badge_filter():
    """PROP-002b — the badge honours per-station reporting levels: an item below
    its station's assigned level does not COUNT (it stays recorded); danger
    always counts (the construct's floor)."""
    db = _tmpdb()
    # warning on GUARDED (memory quarantine) + danger on GUARDED (redteam)
    record_event("u1", "memory_quarantine", "planted instruction quarantined",
                 severity="warning", db_path=db)
    record_event("u1", "live_redteam_failure", "a guard did not hold",
                 severity="danger", db_path=db)
    check("unfiltered count sees both", unacked_count("u1", db_path=db) == 2)
    rep_default = {"guarded": "warning"}
    check("default level counts both", unacked_count("u1", db_path=db, reporting=rep_default) == 2)
    rep_quiet = {"guarded": "danger"}
    check("danger-only station: the warning stops counting, the danger never does",
          unacked_count("u1", db_path=db, reporting=rep_quiet) == 1)
    check("…and the warning is still RECORDED (presentation, never creation)",
          len(list_events("u1", db_path=db)) == 2)
    os.unlink(db)


def t_receipts():
    # PROP-002 (the receipt invariant): quarantining a document is an owner ACT —
    # it must never raise the badge it just worked.
    check("corpus_quarantine is a receipt", "corpus_quarantine" in RECEIPT_EVENT_TYPES)
    db = _tmpdb()
    record_event("u1", "corpus_quarantine", "doc quarantined", severity="info", db_path=db)
    check("a receipt never counts as open work", unacked_count("u1", db_path=db) == 0)
    os.unlink(db)


def t_disposition_source_keyed():
    # PROP-002 — a decision on ONE gate's parked item must not resolve another
    # gate's observation on the same turn.
    idx = disposition_index([
        {"session_id": "s1", "turn_id": 4, "source": "grounding",
         "status": "approved", "released_to_context": False, "decided_at": "t"},
    ])
    turns = [
        _turn(4, grounding={"decision": "block", "out_of_scope_dbs": []},
              guardrail={"action": "redact", "categories": ["pii"]}),
    ]
    ev = derive_turn_events(turns, session_id="s1", dispositions=idx)
    g = next(e for e in ev if e["event_type"] == "grounding_block")
    r = next(e for e in ev if e["event_type"] == "guardrail_outbound_redact")
    check("decided gate reads resolved (info + label)",
          g["severity"] == "info" and "released by the owner" in g["disposition_label"])
    check("the OTHER gate on the same turn stays open",
          r["severity"] == "warning" and "disposition_label" not in r)


def t_derived_acks():
    db = _tmpdb()
    ok = ack_derived("u1", "s1", 4, "grounding_block", db_path=db)
    check("derived ack stores", ok is True)
    check("sentinel (no turn) acks too",
          ack_derived("u1", "s1", None, "session_sentinel", db_path=db) is True)
    idx = derived_ack_index("u1", db_path=db)
    check("index round-trips", ("s1", "4", "grounding_block") in idx
          and ("s1", "", "session_sentinel") in idx)
    check("other user sees nothing", derived_ack_index("u2", db_path=db) == set())
    items = [
        {"session_id": "s1", "turn_id": 4, "event_type": "grounding_block"},
        {"session_id": "s1", "turn_id": 5, "event_type": "grounding_block"},
        {"session_id": "s1", "turn_id": None, "event_type": "session_sentinel"},
    ]
    out = apply_derived_acks(items, idx)
    check("acked items stamped, others untouched",
          out[0].get("acknowledged") is True and "acknowledged" not in out[1]
          and out[2].get("acknowledged") is True)
    check("empty event_type refused", ack_derived("u1", "s1", 1, "", db_path=db) is False)
    check("ack fails open on a bad path",
          ack_derived("u1", "s", 1, "x", db_path="/nonexistent/dir/nope.db") is False
          and derived_ack_index("u1", db_path="/nonexistent/dir/nope.db") == set())
    os.unlink(db)


def t_sentinel():
    probing = {"action": "pass", "findings": [{"category": "prompt_injection",
                                               "label": "ignore_previous"}]}
    # Below threshold → no sentinel.
    turns = [_turn(i, guardrail_inbound=probing) for i in range(1, SENTINEL_MIN_FINDINGS)]
    check("below threshold → None", derive_session_sentinel(turns, "s1") is None)
    # At threshold → one danger item; observe-mode findings count (mode-independent).
    turns = [_turn(i, guardrail_inbound=probing) for i in range(1, SENTINEL_MIN_FINDINGS + 1)] \
        + [_turn(99)]  # clean turn ignored
    s = derive_session_sentinel(turns, "s1")
    check("at threshold → sentinel fires", s is not None and s["severity"] == "danger")
    check("sentinel counts + turns listed",
          s["payload"]["count"] == SENTINEL_MIN_FINDINGS
          and s["payload"]["flagged_turns"] == list(range(1, SENTINEL_MIN_FINDINGS + 1)))
    check("sentinel is session-level (no turn_id)", s["turn_id"] is None)
    # Blocked attempts count too.
    turns = [_turn(i, guardrail_inbound={"action": "block", "findings": []})
             for i in range(1, SENTINEL_MIN_FINDINGS + 1)]
    check("blocked attempts count", derive_session_sentinel(turns, "s1") is not None)
    # Clean session → None.
    check("clean session → None",
          derive_session_sentinel([_turn(1), _turn(2)], "s1") is None)


def main():
    for t in (t_store_roundtrip, t_store_fail_open, t_derive_turn_events,
              t_severity_single_source, t_reporting_badge_filter, t_receipts,
              t_disposition_source_keyed, t_derived_acks, t_sentinel):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
