"""I2 — a disposition must be visible wherever its incident is.

Two findings, one root cause: the inbox is computed from records that never consult
the review queue.

  #48  Derived observations are recomputed from the sealed trace, which records what
       HAPPENED and is immutable. So an incident the owner had ALREADY decided kept
       reading "unresolved" forever — REVIEWED said the trace "carries an unresolved
       observation" minutes after the owner upheld it.
  #57  Some events RECORD an act the owner just performed (releasing a withheld
       answer, acknowledging a corpus finding) and arrived unacknowledged like any
       other event. So acting on the inbox INCREASED its badge: you then had to
       acknowledge the records of your own actions, and the count could never reach
       zero by working it. A badge that cannot reach zero gets ignored.

Pure — no server, no network. The disposition index is passed IN so the derivation
stays testable and cannot silently acquire a database dependency.
"""
import os
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import trust_events as te  # noqa: E402

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")


BLOCK_TURN = {"turn": 2, "session_id": "s1", "profile_tag": "@UDERIA",
              "grounding": {"decision": "block", "out_of_scope_dbs": []}}
REDACT_TURN = {"turn": 1, "session_id": "s2", "profile_tag": "@UDERIA",
               "guardrail": {"action": "redact", "categories": ["pii"]}}
REFUSE_TURN = {"turn": 3, "session_id": "s1", "profile_tag": "@UDERIA",
               "guardrail_inbound": {"action": "block", "categories": ["prompt_injection"]}}

# ── 1 · undecided incidents are unchanged (the baseline must not move) ──────
ev = te.derive_turn_events([BLOCK_TURN, REDACT_TURN, REFUSE_TURN], session_id="s1")
check("three incidents derived", len(ev), 3)
check("an undecided block is danger", ev[0].get("severity"), "danger")
check("…and carries no disposition", "disposition" in ev[0], False)
check("an undecided redaction is warning", ev[1].get("severity"), "warning")
check("passing no index at all is safe", len(te.derive_turn_events([BLOCK_TURN])), 1)

# ── 2 · #48 — a decided incident reads as resolved ──────────────────────────
# Re-pinned 2026-08-25 to the PROP-002 taxonomy (this suite, 2026-07-31, predates it
# and sat red — it was never added to the trust battery, which test_trust_events is):
#   * the disposition key is (session, str(turn), SOURCE) — a (session, turn) key let
#     one gate's decided item stamp EVERY gate's observation on that turn as resolved;
#     review items therefore carry `source`, and the tuples below carry the gate.
#   * `corpus_quarantine` is a RECEIPT — quarantining IS the owner's act, so it must
#     not raise the open-work count (Rule 11.2: acting on the inbox only lowers it).
idx = te.disposition_index([
    {"session_id": "s1", "turn_id": 2, "status": "rejected", "decided_at": "T1",
     "source": "grounding"},
    {"session_id": "s2", "turn_id": 1, "status": "approved",
     "released_to_context": True, "decided_at": "T2", "source": "guardrail_outbound"},
])
ev = te.derive_turn_events([BLOCK_TURN, REDACT_TURN, REFUSE_TURN],
                           session_id="s1", dispositions=idx)
by = {e["event_type"]: e for e in ev}

g = by["grounding_block"]
check("#48 an upheld block is no longer danger", g.get("severity"), "info")
check("#48 …it names the decision", g.get("disposition_label"), "withholding upheld")
check("#48 …in the summary the operator reads",
      "withholding upheld" in g.get("summary"), True)
check("#48 …and records when", g.get("decided_at"), "T1")
check("#48 …never as the raw wire status", "rejected" in g.get("summary"), False)

r = by["guardrail_outbound_redact"]
check("#48 a released redaction says so", r.get("disposition_label"),
      "released into the conversation by the owner")
check("#48 …and drops to info", r.get("severity"), "info")

# An UNdecided incident in the same batch must be untouched — the join is per item.
check("#48 an undecided sibling keeps its severity",
      by["guardrail_inbound_block"].get("severity"), "danger")
check("#48 …and gains no disposition",
      "disposition" in by["guardrail_inbound_block"], False)

# ── 3 · the index only admits DECIDED items ─────────────────────────────────
check("pending items are not dispositions",
      te.disposition_index([{"session_id": "s", "turn_id": 1, "status": "pending"}]), {})
check("items without a turn ref are skipped",
      te.disposition_index([{"status": "approved", "session_id": None, "turn_id": None}]), {})
check("garbage is skipped, not raised",
      te.disposition_index(["nope", None, 42]), {})
check("an approved-but-unreleased item is not 'released'",
      te.disposition_index([{"session_id": "s", "turn_id": 1, "status": "approved",
                             "source": "grounding"}])
      .get(("s", "1", "grounding"), {}).get("released"), False)
# turn ids arrive as int from the trace and TEXT from the store — the join must not
# care, or a decided incident silently reads unresolved.
check("turn ids join across int/str (store TEXT vs trace int)",
      sorted(te.disposition_index([
          {"session_id": "s", "turn_id": "1", "status": "approved", "source": "g"},
          {"session_id": "t", "turn_id": 2, "status": "rejected", "source": "g"},
      ]).keys()), [("s", "1", "g"), ("t", "2", "g")])

# ── 4 · #57 — receipts are not open work ────────────────────────────────────
check("a release is a receipt", te.is_receipt("review_release"), True)
check("a corpus ack is a receipt", te.is_receipt("corpus_finding_ack"), True)
check("a breaker trip is NOT a receipt", te.is_receipt("trust_breaker"), False)
check("a quarantine IS a receipt (PROP-002: quarantining is the owner's act)",
      te.is_receipt("corpus_quarantine"), True)
check("an unknown type is not a receipt", te.is_receipt(None), False)

with tempfile.TemporaryDirectory() as d:
    db = os.path.join(d, "t.db")
    U = "u1"
    te.record_event(U, "trust_breaker", "breaker tripped", db_path=db)
    te.record_event(U, "memory_quarantine", "memory write quarantined", db_path=db)
    check("#57 real work counts", te.unacked_count(U, db_path=db), 2)

    # Acting on the inbox must not INCREASE the count.
    te.record_event(U, "review_release", "owner released an answer", db_path=db)
    te.record_event(U, "corpus_finding_ack", "owner acknowledged a finding", db_path=db)
    check("#57 receipts do not raise the open-work count",
          te.unacked_count(U, db_path=db), 2)

    # …but they remain VISIBLE as history (Rule 3.1 — nothing is hidden).
    listed = te.list_events(U, db_path=db)
    check("#57 receipts are still listed", len(listed), 4)
    check("#57 …and identifiable as receipts",
          sorted(e["event_type"] for e in listed if te.is_receipt(e["event_type"])),
          ["corpus_finding_ack", "review_release"])   # memory_quarantine is open work

    # Working the real items reaches zero — the property the badge lost.
    for e in listed:
        if not te.is_receipt(e["event_type"]):
            te.ack_event(U, e["id"], db_path=db)
    check("#57 the badge can reach zero by working it",
          te.unacked_count(U, db_path=db), 0)

# ── report ──────────────────────────────────────────────────────────────────
print()
if failures:
    print(f"FAILED — {len(failures)} assertion(s)\n")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print("ALL PASS — I2: dispositions are visible, and receipts are not open work.")
