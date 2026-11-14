#!/usr/bin/env python3
"""A signed seed asked word for word is replayed verbatim, never re-planned.

WHY THIS GATE EXISTS
On production (14 Oct 2026) the policing demo's cross-over question — seeded verbatim in a
trust-locked planner of 30 signed champion queries — was adopted by the strategic planner one
time in three; the other two turns re-planned it into a per-record join the preflight guard
then refused, or into a count of the case's objects. A champion that matches the question
exactly, held by a locked planner (a locked planner never learns — every case in it was put
there on purpose), leaves nothing for a model to decide: the proven strategy IS the plan.
Rule: `planner.exact_signed_champion(query, cases, is_locked)`.

Run: python3 test/test_signed_seed_replay.py         (no server, no network)
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.agent.planner import exact_signed_champion, _norm_question, planner_replayable  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  ✓ {label}")
    else:
        failed += 1; print(f"  ✗ {label}" + (f" — {detail}" if detail else ""))


def case(q, *, cid=540, template=True, phases=True, case_id="c1"):
    return {"case_id": case_id, "full_case_data": {
        "intent": {"user_query": q},
        "metadata": {"collection_id": cid, "template_generated": template},
        "successful_strategy": {"phases": [{"phase": 1, "relevant_tools": ["base_readQuery"],
                                             "arguments": {"sql": "SELECT 1"}}] if phases else []}}}


LOCKED = {540}
is_locked = lambda cid: cid in LOCKED  # noqa: E731
Q = "Show me the types of evidence collected for robberies."

print("[§1] the match is on the WORDS, not the punctuation or the case")
check("the same wording normalises to one key",
      _norm_question("  Show me the  types of evidence collected for robberies?") == _norm_question(Q))
r = exact_signed_champion("show me the types of evidence collected for robberies", [case(Q)], is_locked)
check("an exact question against a signed seed is replayed", r is not None and r["case_id"] == "c1" and r["collection_id"] == 540)
check("the strategy comes back as a copy, never the store's object",
      r is not None and r["strategy"]["phases"][0]["arguments"]["sql"] == "SELECT 1")
src = case(Q); r2 = exact_signed_champion(Q, [src], is_locked)
r2["strategy"]["phases"][0]["arguments"]["sql"] = "DROP"
check("…mutating the copy leaves the champion untouched",
      src["full_case_data"]["successful_strategy"]["phases"][0]["arguments"]["sql"] == "SELECT 1")

print("[§2] only a SIGNED SEED replays — everything else still guides the model")
check("a near match (one word different) does not replay",
      exact_signed_champion("Show me the types of evidence collected for burglaries.", [case(Q)], is_locked) is None)
check("a LEARNED champion (not template-generated) does not replay, even word for word",
      exact_signed_champion(Q, [case(Q, template=False)], is_locked) is None)
check("a seed in an UNLOCKED planner does not replay",
      exact_signed_champion(Q, [case(Q, cid=525)], is_locked) is None)
check("a champion without a strategy does not replay",
      exact_signed_champion(Q, [case(Q, phases=False)], is_locked) is None)
check("an unknown lock state is not a signed seed",
      exact_signed_champion(Q, [case(Q)], lambda cid: (_ for _ in ()).throw(RuntimeError("db"))) is None)
check("an empty question never matches", exact_signed_champion("   ", [case("")], is_locked) is None)
check("no champions ⇒ None", exact_signed_champion(Q, [], is_locked) is None and exact_signed_champion(Q, None, is_locked) is None)

print("[§3] the first exact signed seed wins among several champions")
r = exact_signed_champion(Q, [case("How many robberies are there?", case_id="other"), case(Q, case_id="hit"),
                              case(Q, case_id="dup")], is_locked)
check("the exact one, not the first retrieved", r is not None and r["case_id"] == "hit")

print("[§4] the planner applies the rule where the model would be called")
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "planner.py")).read()
check("decided after retrieval, through the one replay rule (locked, or signed and intact)",
      "_verbatim = exact_signed_champion(" in src and "lambda _cid: planner_replayable(_cid, _gcd_v().get_collection_by_id(_cid))" in src)
check("applied in place of the strategic LLM call, 0 tokens, the trace says so",
      'if _verbatim is not None:' in src and '"step": "Signed seed replayed verbatim"' in src
      and 'json.dumps(_verbatim["strategy"]), 0, 0' in src)

print("[§5] OC-F3 — a SIGNED planner replays too, while its signature still covers its content")
# Found by the Optimize campaign (2026-10-29): signing a planner never locks it, and no operator
# repository was locked, so a signed seed asked word for word was always re-planned by a model.
check("a locked repository replays (as before)", planner_replayable(540, {"trust_locked": 1}))
check("a signed repository whose attested hash is its live hash replays",
      planner_replayable(541, {"trust_locked": 0}, attested="h1", live="h1"))
check("…a signed repository that LEARNED since signing does not (the signature no longer covers it)",
      not planner_replayable(541, {"trust_locked": 0}, attested="h1", live="h2"))
check("an unsigned repository does not", not planner_replayable(542, {"trust_locked": 0}, attested="", live="h1"))
check("an unreadable repository does not (no live hash)", not planner_replayable(543, {}, attested="h1", live=""))
r = exact_signed_champion(Q, [case(Q, cid=541)], lambda cid: planner_replayable(cid, {}, attested="h1", live="h1"))
check("the replay rule takes a signed, intact repository's exact seed", r is not None and r["collection_id"] == 541)
check("…and still refuses a learned (non-template) case in it",
      exact_signed_champion(Q, [case(Q, cid=541, template=False)],
                            lambda cid: planner_replayable(cid, {}, attested="h1", live="h1")) is None)

print(f"\n{'ALL PASS' if not failed else 'FAIL'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
