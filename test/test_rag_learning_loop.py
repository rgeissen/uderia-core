"""
RAG learning-loop quality — regression gate for the three deferred §3 items of
docs/assessments/FUSION_OPTIMIZER_DEEP_ASSESSMENT_2026-07.md (plan: RAG_LEARNING_LOOP_PLAN.md):

  1. non-owned-collection seeding fallback  (process_and_store_case)
  2. bounded few-shot injection             (_bounded_plan_json / _format_few_shot_example)
  3. per-user champion re-election scope     (_reevaluate_champion_for_query)

CI-pure: no server, no DB, no network. Functional for the reachable pure logic (item 2 formatter,
item 3 re-election driven with a fake ChromaDB collection); scoped comment-stripped source assertions
for item 1's fallback, which lives inside a large I/O-bound async method.

Run:  PYTHONPATH=src python3 test/test_rag_learning_loop.py
"""
import asyncio
import json
import sys
import types
from pathlib import Path

from trusted_data_agent.agent import rag_retriever as rr
from trusted_data_agent.agent.rag_retriever import RAGRetriever
from trusted_data_agent.agent.rag_template_generator import RAGTemplateGenerator

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


_SRC = Path(__file__).resolve().parent.parent / "src" / "trusted_data_agent"


def _code_lines(rel):
    text = (_SRC / rel).read_text()
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# ============================================================ 2 — bounded few-shot injection
print("\n[2] few-shot injection is bounded (truncate long args + hard cap), structure preserved")

_big_sql = "SELECT " + ", ".join(f"col_{i}" for i in range(2000)) + " FROM huge_table"
big_phases = [
    {"phase": 1, "goal": "run the big query", "relevant_tools": ["base_readQuery"],
     "arguments": {"sql": _big_sql}},
    {"phase": 2, "goal": "final report", "relevant_tools": ["TDA_FinalReport"]},
]
bounded = RAGRetriever._bounded_plan_json(big_phases)
check("long SQL arg truncated to the preview (not verbatim)", _big_sql not in bounded)
check("preview marker present", "chars)" in bounded or "truncated" in bounded)
check("whole block capped at RAG_FEWSHOT_MAX_CHARS (+ marker)",
      len(bounded) <= rr.RAG_FEWSHOT_MAX_CHARS + 120)
check("structure preserved (phase/goal/tool still visible)",
      '"phase": 1' in bounded and "TDA_FinalReport" in bounded)
# input not mutated (replay path reads the original full_case_data)
check("input phases NOT mutated", big_phases[0]["arguments"]["sql"] == _big_sql)

# 2026-09-03: a champion's SQL is its value. A 330-char statement previewed at 240 chars
# came back from the planner as an invented column (`V20101`) on a question whose exact
# answer sat in the repository. SQL keeps a statement-sized bound; prose keeps the preview.
_mid_sql = ("SELECT COUNT(*) AS digital_with_property FROM NIBRS_Source_Domain.Incident i WHERE "
            "(i.V20081='2' OR i.V20082='2' OR i.V20083='2' OR i.V20111='58' OR i.V20112='58' OR "
            "i.V20113='58' OR i.V20061 IN ('262','265','266','267','370')) AND TRIM(COALESCE(i.V30071,''))<>''")
check("fixture: the statement is longer than the prose preview and inside the SQL bound",
      rr.RAG_FEWSHOT_ARG_PREVIEW_CHARS < len(_mid_sql) < rr.RAG_FEWSHOT_SQL_CHARS)
_prose = "explain " * 60
mid_phases = [{"phase": 1, "goal": "count", "relevant_tools": ["base_readQuery"],
               "arguments": {"sql": _mid_sql, "notes": _prose}}]
mid = RAGRetriever._bounded_plan_json(mid_phases)
check("a statement-sized SQL arg survives the few-shot INTACT", _mid_sql in mid)
check("its exact predicate list is not cut", "V30071" in mid and "'370'" in mid)
check("a prose arg of the same size is still previewed", _prose not in mid and "chars)" in mid)
check("SQL over the statement bound is still cut (the 2000-column case above)",
      len(rr.RAG_FEWSHOT_SQL_CHARS and _big_sql) > rr.RAG_FEWSHOT_SQL_CHARS and _big_sql not in bounded)
check("the SQL bound sits under the block cap so one statement can never blow the block",
      rr.RAG_FEWSHOT_SQL_CHARS < rr.RAG_FEWSHOT_MAX_CHARS)

# a small plan passes through structurally intact + valid JSON up to the cap
small_phases = [{"phase": 1, "goal": "list dbs", "relevant_tools": ["base_databaseList"]}]
small = RAGRetriever._bounded_plan_json(small_phases)
check("small plan is intact valid JSON", json.loads(small) == small_phases)

# _format_few_shot_example routes a huge champion through the bound
fake_self = types.SimpleNamespace(_bounded_plan_json=RAGRetriever._bounded_plan_json)
case = {
    "user_query": "show the big thing",
    "case_id": "case_x",
    "strategy_type": "successful",
    "full_case_data": {"successful_strategy": {"phases": big_phases}},
}
formatted = RAGRetriever._format_few_shot_example(fake_self, case)
check("_format_few_shot_example emits the bounded plan (verbatim SQL absent)", _big_sql not in formatted)
check("_format_few_shot_example stays well under the raw-dump size",
      len(formatted) <= rr.RAG_FEWSHOT_MAX_CHARS + 400)


# ============================================================ 3 — per-user champion re-election scope
print("\n[3] champion re-election is scoped to the downvoted case's user (matches seeding scope)")

TEMPLATE = RAGTemplateGenerator.TEMPLATE_SESSION_ID


def _match(where, meta):
    """Minimal ChromaDB where-clause evaluator for the shapes _reevaluate produces."""
    if "$and" in where:
        return all(_match(c, meta) for c in where["$and"])
    if "$or" in where:
        return any(_match(c, meta) for c in where["$or"])
    for key, cond in where.items():
        val = meta.get(key)
        if "$eq" in cond and val != cond["$eq"]:
            return False
        if "$gte" in cond and not (val is not None and val >= cond["$gte"]):
            return False
    return True


class _FakeCollection:
    def __init__(self, cases):
        # cases: {id: metadata dict}
        self.cases = {cid: dict(m) for cid, m in cases.items()}

    def get(self, where=None, ids=None, include=None):
        if ids is not None:
            got = {cid: self.cases[cid] for cid in ids if cid in self.cases}
        elif where is not None:
            got = {cid: m for cid, m in self.cases.items() if _match(where, m)}
        else:
            got = dict(self.cases)
        # COPIES, as a real ChromaDB read returns freshly-deserialised dicts. Aliasing
        # here makes an in-place metadata mutation instantly visible to the next query,
        # which hides read-then-write ordering defects (it hid one: re-election running
        # before the score was persisted). See test_feedback_champion.py.
        return {"ids": list(got.keys()), "metadatas": [dict(got[c]) for c in got]}

    def update(self, ids=None, metadatas=None):
        for cid, m in zip(ids, metadatas):
            self.cases[cid] = dict(m)


def _make_retriever(fake_coll):
    r = types.SimpleNamespace()
    r.collections = {1: fake_coll}
    # bind the real method
    r._reevaluate_champion_for_query = types.MethodType(RAGRetriever._reevaluate_champion_for_query, r)
    return r


# Query Q with two users' cases. A's champion just got downvoted (excluded via feedback -1).
Q = "how many databases"
cases = {
    "case_A1": {"user_query": Q, "user_uuid": "userA", "user_feedback_score": -1, "output_tokens": 100, "is_most_efficient": False},
    "case_A2": {"user_query": Q, "user_uuid": "userA", "user_feedback_score": 0,  "output_tokens": 200, "is_most_efficient": False},
    "case_B1": {"user_query": Q, "user_uuid": "userB", "user_feedback_score": 0,  "output_tokens": 50,  "is_most_efficient": True},
}
coll = _FakeCollection(cases)
r = _make_retriever(coll)
run(r._reevaluate_champion_for_query(1, Q, case_user_uuid="userA"))
# scoped to userA: A2 becomes A's champion (only eligible A case); B1 is UNTOUCHED
check("scoped re-election elects userA's own eligible case (A2)", coll.cases["case_A2"]["is_most_efficient"] is True)
check("userB's champion (B1) is NOT demoted by userA's downvote", coll.cases["case_B1"]["is_most_efficient"] is True)
check("downvoted A1 stays demoted", coll.cases["case_A1"]["is_most_efficient"] is False)

# Template cases are in-scope for the user (mirror the owned-collection seeding filter)
cases2 = {
    "case_T": {"user_query": Q, "user_uuid": TEMPLATE, "user_feedback_score": 0, "output_tokens": 10, "is_most_efficient": False},
    "case_A2": {"user_query": Q, "user_uuid": "userA", "user_feedback_score": 0, "output_tokens": 200, "is_most_efficient": True},
    "case_B1": {"user_query": Q, "user_uuid": "userB", "user_feedback_score": 0, "output_tokens": 5, "is_most_efficient": True},
}
coll2 = _FakeCollection(cases2)
r2 = _make_retriever(coll2)
run(r2._reevaluate_champion_for_query(1, Q, case_user_uuid="userA"))
check("template case competes within the user's scope (T beats A2 on tokens)",
      coll2.cases["case_T"]["is_most_efficient"] is True and coll2.cases["case_A2"]["is_most_efficient"] is False)
check("userB's lower-token case is still NOT pulled into userA's re-election",
      coll2.cases["case_B1"]["is_most_efficient"] is True)

# Legacy fallback: no case_user_uuid → global behaviour (all cases considered)
cases3 = {
    "case_X": {"user_query": Q, "user_feedback_score": 0, "output_tokens": 300, "is_most_efficient": True},
    "case_Y": {"user_query": Q, "user_feedback_score": 0, "output_tokens": 90,  "is_most_efficient": False},
}
coll3 = _FakeCollection(cases3)
r3 = _make_retriever(coll3)
run(r3._reevaluate_champion_for_query(1, Q, case_user_uuid=None))
check("legacy (no user_uuid) → global re-election picks the fewest-token case (Y)",
      coll3.cases["case_Y"]["is_most_efficient"] is True and coll3.cases["case_X"]["is_most_efficient"] is False)


# ============================================================ 1 — non-owned-collection seeding fallback
print("\n[1] seeding falls back to the user's own default collection when the guiding one is read-only")
_body = _code_lines("agent/rag_retriever.py")
check("write-denial no longer immediately returns (fallback attempted first)",
      "falling back to own default collection" in _body)
# the fallback moved to the ensure- form (a dropped lesson is impossible when no default
# exists yet — test_planner_subscription_keying K5 pins it); this assertion pinned the get-
# form and read red at HEAD for two increments
check("fallback resolves the user's own default collection for the MCP server",
      "ensure_default_collection_for_mcp_server(mcp_server_id, user_uuid)" in _body)
check("fallback only retargets when the fallback is writable AND distinct",
      "rag_context.validate_collection_access(fallback_id, write=True)" in _body
      and "fallback_id != collection_id" in _body)
check("still skips (no silent seed) when no writable fallback exists",
      "no writable" in _body and "Skipping RAG processing" in _body)
# the import hoist that makes mcp_server_id/config_manager available to BOTH branches (also fixes a
# latent APP_CONFIG scope bug at the metadata-stamp line for the collection_id-provided path)
check("mcp_server_id + config_manager resolved before the access check (both branches)",
      _body.index("mcp_server_id = turn_summary.get('mcp_server_id')") < _body.index("validate_collection_access(collection_id, write=True)"))
check("caller threads the downvoted case's user_uuid into re-election (item 3 wiring)",
      "case_user_uuid=metadata.get(\"user_uuid\")" in _body)


print(f"\n{'='*60}\nRAG learning-loop: {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
