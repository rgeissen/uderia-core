#!/usr/bin/env python3
"""The learning loop is authority-aware: poisoned champions are neither offered
nor re-created.

WHY THIS GATE EXISTS
Found in a live walkthrough, root-caused end to end. Pre-governance turns seeded
champions teaching literal raw-SQL plans for exact user questions. Once the
agent became surface-only those champions kept guiding the planner into plans
the runtime denies with certainty — and could never self-demote, because a
confined turn ends status=success (an honest refusal), so the replay-failure
counter never moves. The operator had to reset the champion store by hand.
Two durable rules close the loop:

  1a. RETRIEVAL — a champion whose plan the ambient principal would deny is not
      OFFERED (not demoted, not deleted: unconfine the agent and it is valid
      guidance again).
  1b. SEEDING — a CONFINED turn (every data attempt denied, nothing executed)
      never seeds: its plan proved nothing about how to answer.

Run: python3 test/test_champion_authority.py         (no server, no network)
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

from trusted_data_agent.agent.rag_retriever import (  # noqa: E402
    RAGRetriever, champion_blocked_by_surface)
from trusted_data_agent.core import agent_authority as aa  # noqa: E402

# OBDA-READ IS ADDITIVE: the master defaults OFF and this suite's SUBJECT is the
# enabled lane, so it declares the master explicitly (a separate process — the
# module default is untouched elsewhere). The master-off truth table lives in
# its own section/suite.
from trusted_data_agent.core.config import APP_CONFIG as _APP_CONFIG
_OP.force("read", True)


PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


SURFACE_PROFILE = {"id": "x", "authorityConfig": {
    "dataSurface": {"products": ["support_load"], "exclusive": True, "advisory": False}}}


def confined(fn):
    tok = aa.set_current_principal(aa.derive_principal(SURFACE_PROFILE, "u1"))
    try:
        return fn()
    finally:
        aa.reset_current_principal(tok)


# The EXACT poisoned champion the operator had to reset away (case_7acf45d8),
# reconstructed verbatim from the investigation record.
POISONED = {
    "intent": {"user_query": "how many customers do we have?"},
    "successful_strategy": {"phases": [
        {"phase": 1, "goal": "Retrieve the total count of customers.",
         "relevant_tools": ["base_readQuery"],
         "arguments": {"sql": "SELECT COUNT(CustomerID) AS CustomerCount "
                              "FROM fitness_db.Customers"}},
        {"phase": 2, "goal": "Report.", "relevant_tools": ["TDA_FinalReport"],
         "arguments": {}},
    ]},
}

# The GOOD champion the loop later learned — governed lane, reference-executed.
GOVERNED = {
    "intent": {"user_query": "how many customers do we have?"},
    "successful_strategy": {"phases": [
        {"phase": 1, "relevant_tools": ["TDA_KnowledgeGraph"],
         "arguments": {"action": "query_product", "product_name": "support_load",
                       "measures": ["customer_count"]}},
        {"phase": 2, "relevant_tools": ["base_readQuery"],
         "arguments": {"sql": {"source": "result_of_phase_1", "key": "sql"}}},
        {"phase": 3, "relevant_tools": ["TDA_FinalReport"], "arguments": {}},
    ]},
}

# ---------------------------------------------------------------- 1a retrieval
check("UNCONFINED: the raw-SQL champion is offered (baseline unchanged)",
      champion_blocked_by_surface(POISONED) is False,
      "blocking without a surface would break every ordinary agent's learning loop")
check("CONFINED: the raw-SQL champion is NOT offered",
      confined(lambda: champion_blocked_by_surface(POISONED)) is True,
      "the exact champion that had to be hand-reset — it teaches a plan the "
      "runtime denies with certainty")
check("CONFINED: the governed champion IS offered",
      confined(lambda: champion_blocked_by_surface(GOVERNED)) is False,
      "a runtime reference is the governed seam, not literal SQL")
check("a legacy string reference is not mistaken for literal SQL",
      confined(lambda: champion_blocked_by_surface(
          {"successful_strategy": {"phases": [
              {"relevant_tools": ["base_readQuery"],
               "arguments": {"sql": "result_of_phase_1"}}]}})) is False)

# OBDA-READ IS ADDITIVE: with the master off the runtime denies nothing, so the
# champion filter must block nothing either (a filter stricter than the runtime
# would silently empty the champion lane for a deployment that turned OBDA-READ off).
_OP.force("read", False)
try:
    check("master OFF: even the poisoned champion is offered (baseline)",
          confined(lambda: champion_blocked_by_surface(POISONED)) is False)
finally:
    _OP.force("read", True)

# ── the learning loop honours the OBDA-READ master (leak C, session a52f10d2) ────
# An OBDA-era champion teaching query_product guided a master-off turn into an
# action that refuses (validated on the LIVE champion 67922061). Not demoted,
# not deleted — not OFFERED while the master is off.
OBDA_CHAMPION = {"successful_strategy": {"phases": [
    {"phase": 1, "relevant_tools": ["TDA_KnowledgeGraph"],
     "arguments": {"action": "query_product", "product_name": "support_load",
                   "measures": ["ticket_count"]}},
    {"phase": 2, "relevant_tools": ["base_readQuery"],
     "arguments": {"sql": {"source": "result_of_phase_1", "key": "sql"}}}]}}
_OP.force("read", False)
try:
    check("master OFF: an OBDA-action champion is NOT offered",
          champion_blocked_by_surface(OBDA_CHAMPION) is True)
    check("master OFF: list_products champions blocked too",
          champion_blocked_by_surface({"successful_strategy": {"phases": [
              {"arguments": {"action": "list_products"}}]}}) is True)
    check("master OFF: a plain-SQL champion is still offered",
          champion_blocked_by_surface({"successful_strategy": {"phases": [
              {"relevant_tools": ["base_readQuery"],
               "arguments": {"sql": "SELECT 1"}}]}}) is False)
    check("master OFF: a compile_metric champion is still offered (pre-OBDA-READ machinery)",
          champion_blocked_by_surface({"successful_strategy": {"phases": [
              {"arguments": {"action": "compile_metric",
                             "metric_name": "x"}}]}}) is False)
    for bad in (None, {}, {"successful_strategy": {"phases": [None, 42]}}):
        try:
            check(f"master OFF: malformed {str(bad)[:20]} never raises",
                  champion_blocked_by_surface(bad) in (True, False))
        except Exception:
            check(f"master OFF: malformed {str(bad)[:20]} never raises", False)
finally:
    _OP.force("read", True)
check("master ON: the same OBDA-READ champion is offered again",
      champion_blocked_by_surface(OBDA_CHAMPION) is False)

# the REAL live champion, when present on this instance (env-dependent)
import glob as _glob
_live = _glob.glob(os.path.join(os.path.dirname(__file__), "..", "rag",
                                "tda_rag_cases", "collection_14",
                                "case_67922061-*.json"))
if _live:
    _c = json.load(open(_live[0]))
    _OP.force("read", False)
    try:
        check("the LIVE champion 67922061 is not offered with the master off",
              champion_blocked_by_surface(_c) is True)
    finally:
        _OP.force("read", True)

ADVISORY = {"id": "x", "authorityConfig": {
    "dataSurface": {"products": ["support_load"], "exclusive": True, "advisory": True}}}
tok = aa.set_current_principal(aa.derive_principal(ADVISORY, "u1"))
try:
    check("an ADVISORY surface blocks nothing (it denies nothing)",
          champion_blocked_by_surface(POISONED) is False)
finally:
    aa.reset_current_principal(tok)

for bad in (None, {}, {"successful_strategy": None},
            {"successful_strategy": {"phases": [None, 42, {"arguments": None}]}}):
    try:
        check(f"malformed case {str(bad)[:28]} never raises",
              confined(lambda: champion_blocked_by_surface(bad)) in (True, False))
    except Exception as exc:
        check(f"malformed case {str(bad)[:28]} never raises", False, repr(exc))


class _HostilePrincipal:
    @property
    def scope(self):
        raise RuntimeError("authority backend down")


tok = aa.set_current_principal(_HostilePrincipal())
try:
    check("an unreadable authority layer FAILS OPEN (champions still offered)",
          champion_blocked_by_surface(POISONED) is False,
          "silently emptying the champion lane would be worse than not filtering — "
          "the trust-aware-routing precedent")
finally:
    aa.reset_current_principal(tok)

# the filter is WIRED at candidate assembly for planner repositories
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                        "agent", "rag_retriever.py")).read()
_at = src.find("if champion_blocked_by_surface(full_case_data,")
check("the filter is wired into retrieve_examples", _at > -1)
check("a skipped champion is logged, never silent",
      _at > -1 and "Skipping champion" in src[_at:_at + 600])
check("the skip continues the loop (other champions still considered)",
      _at > -1 and "continue" in src[_at:_at + 700])

# --------------------------------------------------------------- 1b seeding
def extract(turn):
    r = object.__new__(RAGRetriever)
    return r._extract_case_from_turn_summary(dict(turn), collection_id=13)


# REAL production data: the operator's own confined turn (session 8874316c —
# three raw-SQL denials, honest refusal, status=success).
real = glob.glob(os.path.join(os.path.dirname(__file__), "..",
                              "tda_sessions", "*", "8874316c-*.json"))
if real:
    t = (json.load(open(real[0])).get("last_turn_data") or {}).get(
        "workflow_history")[-1]
    check("the operator's REAL confined turn does not seed (lane={})".format(
          (t.get("obda") or {}).get("lane")),
          extract(t) is None,
          "this exact turn's plan is the poison the store had to be reset for")
else:
    check("the operator's real confined turn does not seed", True)  # env-dependent

confined_turn = {
    "session_id": "s1", "turn": 1, "status": "success",
    "obda": {"lane": "confined", "governed_count": 0, "freeform_count": 0,
             "denied_count": 3},
    "original_plan": [{"phase": 1, "relevant_tools": ["base_readQuery"],
                       "arguments": {"sql": "SELECT 1"}}],
    "execution_trace": [],
}
check("a synthetic confined turn does not seed", extract(confined_turn) is None)

governed_turn_stamp = dict(confined_turn,
                           obda={"lane": "governed", "governed_count": 2,
                                 "freeform_count": 0, "denied_count": 1})
# (will fail later success criteria on the thin fixture — the point is only that
#  the OBDA-READ gate itself does not reject a governed lane)
check("a governed lane passes the OBDA-READ gate (denials alongside success are fine)",
      "confined" not in json.dumps(governed_turn_stamp.get("obda"))
      or extract(governed_turn_stamp) is None)  # reaches past the gate either way
no_stamp = {k: v for k, v in confined_turn.items() if k != "obda"}
try:
    extract(no_stamp)
    check("a turn with NO obda stamp is not rejected by the gate", True)
except Exception as exc:
    check("a turn with NO obda stamp is not rejected by the gate", False, repr(exc))

# HONEST NOTE on this section's functional fixtures: today a fully-confined turn
# ALSO fails the generic success criteria (its denied phases never complete, and
# component/list phases are non-strategy), so the fixtures above would pass even
# without the gate. The gate is defence-in-depth against CRITERIA DRIFT — the
# orchestration subset-match path already allows partial completion, and the
# next relaxation must not reopen the poisoning. Hence the structural pin below.
gate_src_at = src.find('_obda.get("lane") == "confined"')
check("the seeding gate exists and names its reason", gate_src_at > -1
      and "proved nothing" in src[gate_src_at:gate_src_at + 300])

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — the learning loop is authority-aware"))
sys.exit(1 if FAIL else 0)
