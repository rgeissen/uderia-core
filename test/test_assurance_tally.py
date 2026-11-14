"""The per-turn assurance tally.

Three gates fire at seams that know nothing about the turn they serve. The tally lets
them record anyway. What must hold: concurrent turns cannot see each other's counts, a
gate outside a turn records nothing rather than leaking into the next one, and nothing
here can break a turn.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent import assurance_tally as T  # noqa: E402
from trusted_data_agent.agent import assurance_points as AP  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}")


# ── 1 · accumulation ──────────────────────────────────────────────────────────
print("\n[1] accumulation semantics")
T.begin_turn()
check("a fresh turn has nothing", T.snapshot() == {})
check("a gate with no record stamps None", T.stamp("preflight") is None)

T.mark_ran("preflight")
check("mark_ran records that it RAN without claiming a finding",
      T.stamp("preflight") == {"ran": True})

T.record("preflight", checked_count=1, unknown_dbs=["a_db"])
T.record("preflight", checked_count=1, blocked_count=1, unknown_dbs=["a_db", "b_db"])
st = T.stamp("preflight")
check("integers add across calls", st["checked_count"] == 2 and st["blocked_count"] == 1)
check("lists extend, de-duplicated", st["unknown_dbs"] == ["a_db", "b_db"])

T.record("preflight", mode="observe")
T.record("preflight", mode="enforce")
check("scalars are last-write-wins (a mode is a constant, not a count)",
      T.stamp("preflight")["mode"] == "enforce")

T.record("g", flagged=False)
T.record("g", flagged=True)
T.record("g", flagged=False)
check("booleans latch true — a finding cannot be un-found by a later clean call",
      T.stamp("g")["flagged"] is True)

T.begin_turn()
for i in range(40):
    T.record("x", labels=[f"L{i}"])
check("lists are bounded — a stamp never becomes an unbounded dump",
      len(T.stamp("x")["labels"]) <= 12)


# ── 2 · the turn boundary ─────────────────────────────────────────────────────
print("\n[2] the turn boundary — nothing leaks between turns")
T.begin_turn()
T.record("preflight", blocked_count=3)
T.begin_turn()
check("a new turn starts clean", T.stamp("preflight") is None)

T.end_turn()
T.record("preflight", blocked_count=9)
check("a gate firing OUTSIDE a turn records nothing", T.stamp("preflight") is None)
check("outside a turn, snapshot is empty", T.snapshot() == {})
check("active() reports the boundary honestly", T.active() is False)
T.begin_turn()
check("active() is true inside a turn", T.active() is True)


# ── 3 · concurrent turns ──────────────────────────────────────────────────────
print("\n[3] concurrent turns are isolated — the reason this is a contextvar")


async def _turn(name, n, delay):
    T.begin_turn()
    for _ in range(n):
        await asyncio.sleep(delay)          # yield: another turn runs here
        T.record("preflight", blocked_count=1, unknown_dbs=[f"{name}_db"])
    await asyncio.sleep(delay)
    return T.stamp("preflight")


async def _race():
    return await asyncio.gather(
        _turn("alpha", 5, 0.001),
        _turn("beta", 3, 0.001),
        _turn("gamma", 7, 0.001),
    )


res = asyncio.run(_race())
check("each concurrent turn counts only its own gates",
      [r["blocked_count"] for r in res] == [5, 3, 7])
check("no turn sees another's labels",
      all(r["unknown_dbs"] == [f"{n}_db"]
          for r, n in zip(res, ("alpha", "beta", "gamma"))))


async def _child_writes():
    """A gate firing inside a task the turn spawned must land on THAT turn."""
    T.begin_turn()

    async def _inner():
        await asyncio.sleep(0.001)
        T.record("preflight", blocked_count=1)

    await asyncio.gather(_inner(), _inner())
    return T.stamp("preflight")


check("a child task's record reaches its parent turn",
      (asyncio.run(_child_writes()) or {}).get("blocked_count") == 2)


# ── 4 · never breaks a turn ───────────────────────────────────────────────────
print("\n[4] a tally failure is strictly worse than no tally — so it cannot fail")
T.begin_turn()
ok = True
for bad in (object(), None, {"a": 1}, 3.5):
    try:
        T.record("weird", value=bad)
    except Exception:
        ok = False
check("recording an unserialisable value never raises", ok)

try:
    T.record(None, x=1)          # noqa: type
    T.mark_ran(None)             # noqa: type
    T.stamp(None)                # noqa: type
    ok2 = True
except Exception:
    ok2 = False
check("a malformed gate name never raises", ok2)


# ── 5 · the registry reads it ─────────────────────────────────────────────────
print("\n[5] end to end: the registry now reads a real state, not `unknown`")
T.begin_turn()
T.mark_ran("preflight")
T.record("preflight", mode="enforce", checked_count=2, blocked_count=1,
         unknown_dbs=["nope_db"])
T.record("retrieval_hygiene", screened_chunks=6, defused_chunks=1,
         defuse_labels=["obey_instruction"], max_sensitivity="internal",
         sensitivity_excluded=2, quarantined_excluded=1)

turn = dict(T.snapshot())
by_key = {r.key: r for r in AP.read_turn(turn, "tool_enabled")}
check("preflight now reads danger, not unknown",
      by_key["preflight_sql"].state == "danger")
check("preflight names the database it blocked",
      "nope_db" in by_key["preflight_sql"].detail)
check("the retrieval firewall now reads warn, not unknown",
      by_key["retrieval_firewall"].state == "warn")
check("the sensitivity ceiling now reads warn and names the ceiling",
      by_key["sensitivity_ceiling"].state == "warn"
      and "internal" in by_key["sensitivity_ceiling"].detail)
check("corpus quarantine now reads warn",
      by_key["corpus_quarantine"].state == "warn")

# The privacy invariant that governs the guardrail events governs this too.
blob = str(turn)
check("the stamp carries labels and counts, never matched text",
      "obey_instruction" in blob and "chunk" not in blob.lower().replace("chunks", ""))

# A clean run must read `pass`, which is the entire point of P1.
T.begin_turn()
T.mark_ran("preflight")
T.record("preflight", mode="observe", checked_count=3)
T.record("retrieval_hygiene", screened_chunks=4)
clean = {r.key: r for r in AP.read_turn(dict(T.snapshot()), "tool_enabled")}
check("a clean preflight reads pass, not unknown", clean["preflight_sql"].state == "pass")
check("a clean firewall reads pass, not unknown",
      clean["retrieval_firewall"].state == "pass")

# The trap this nearly fell into: recording every chunk as "screened" regardless of
# the master switch would report a firewall that never ran as one that ran clean —
# the exact defect the tally exists to remove. Off must read `na`, never `pass`.
T.begin_turn()
T.record("retrieval_hygiene", firewall_off=True)
_off_fw = {r.key: r for r in AP.read_turn(dict(T.snapshot()), "rag_focused")}
check("a SWITCHED-OFF firewall reads na, never pass",
      _off_fw["retrieval_firewall"].state == "na"
      and "not switched on" in _off_fw["retrieval_firewall"].detail)
check("no ceiling set reads `na`, never pass",
      clean["sensitivity_ceiling"].state == "na")

# On a turn where tracing IS live, a gate that never ran reads `na` — NOT `unknown`,
# and NEVER `pass`. (Retired 2026-08: this used to assert `unknown`; production
# partial-stamp shapes — ['grounding'] alone, ['honesty'] alone — proved absence is
# benign path variance, and preflight/retrieval_hygiene stamp only when their subject
# occurs, so `unknown` buried whole stations in "not recorded" on clean turns. The
# precise gap signal is the gate's OWN action="error" stamp. See test_assurance_points
# §[4b] for the full record.)
T.begin_turn()
T.record("guardrail_seen_by_the_turn", ran=True)
traced = dict(T.snapshot(), guardrail={"ran": True, "action": "pass"})
never = {r.key: r for r in AP.read_turn(traced, "tool_enabled")}
check("on a traced turn, a gate that never ran reads na (absent), NOT pass",
      never["preflight_sql"].state == "na"
      and never["retrieval_firewall"].state == "na")

# …and on a turn that predates tracing entirely, the same absence also reads `na` with
# a reason — the honesty is in the words, not in a fifth state.
pre = {r.key: r for r in AP.read_turn({}, "tool_enabled")}
check("on a pre-tracing turn the same absence reads na, with the reason",
      pre["preflight_sql"].state == "na"
      and ("did not run" in pre["preflight_sql"].detail
           or "not recorded" in pre["preflight_sql"].detail))

T.end_turn()
print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
