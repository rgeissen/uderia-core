"""A turn the provider failed: an honest answer, a failed record, a red JUDGED. CI-pure.

FOUND (trust campaign C18, 2026-10-29): the campaign's OpenRouter account ran out of credits
(HTTP 402). The ReAct executor's catch-all DELIVERED the provider's raw JSON as the answer
("I encountered an error while processing your request: Error code: 402 - {'error': …}"), the
coordinator's did the same, Optimize ended the turn with no answer — and every assurance station
read quiet (invariant I6). Decision (operator, 2026-10-29): the failure is read at JUDGED — the
execution-quality station — by its two deterministic measures, never on SIGNED (a provider that
did not answer says nothing about the signature: the refusal rule). The pieces:
  1. `agent/turn_failure.answer` — one honest sentence naming the failure class, never str(exc);
  2. every engine's failure path delivers it and records the turn as failed;
  3. `clean_termination` fails a turn the record calls failed (the ReAct engine adds its
     `turn_complete` step on the failure path too, so it used to pass);
  4. `output_sanity` recognises a provider's raw error;
  5. a failed Optimize turn (no `final_answer`) is still scored; judges are skipped for it;
  6. an empty account counts against the provider breaker (the health strip; the fallback);
  7. I6 accepts the failure measured at JUDGED.
"""
import os, pathlib, sys, types, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from trusted_data_agent.agent import turn_failure as TF
from trusted_data_agent.llm import resilience as R

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
ROOT = pathlib.Path(__file__).resolve().parents[1]
src = lambda rel: (ROOT / rel).read_text()

RAW_402 = ("Error code: 402 - {'error': {'message': 'This request requires more credits, or fewer "
           "max_tokens. You requested up to 16384 tokens, but can only afford 1043.', 'code': 402, "
           "'metadata': {'provider_name': None}}, 'user_id': 'user_31abc'}")
class APIStatusError(Exception):
    def __init__(self, msg, status):
        super().__init__(msg); self.status_code = status

print("\n── §1 the honest sentence")
e402 = APIStatusError(RAW_402, 402)
a = TF.answer(e402)
check("an empty account is named as such, with what to do and the HTTP code",
      a.startswith(TF.PROVIDER_PREFIX) and "no credits left" in a and "(HTTP 402)" in a and a.endswith(TF.NOTHING_DONE.strip()))
check("the raw provider error never reaches the answer", "Error code" not in a and "{" not in a and "user_31abc" not in a and "max_tokens" not in a)
check("a rate limit, an auth failure, a timeout, an oversized request each get their own words",
      "rate-limiting" in TF.answer(APIStatusError("Error code: 429 - too many requests", 429))
      and "credentials" in TF.answer(APIStatusError("Error code: 401 - invalid api key", 401))
      and "did not respond in time" in TF.answer(TimeoutError("timed out"))
      and "larger than the model accepts" in TF.answer(APIStatusError("maximum context length is 128000 tokens", 400)))
g = TF.answer(KeyError("secret_internal_key"))
check("anything else is an internal error, named by its type only", "internal error (KeyError)" in g and "secret_internal_key" not in g)
class Weird(Exception):
    def __str__(self): raise RuntimeError("no str")
check("the sentence is produced even when the exception cannot be printed", "internal error" in TF.answer(Weird()))

print("\n── §2 the credits reading leaves the envelope's retry behaviour alone")
check("402, a credits message, OpenAI's insufficient_quota (429) read as out of credits",
      R.out_of_credits(e402) and R.out_of_credits(Exception("insufficient_quota: You exceeded your current quota"))
      and R.out_of_credits(APIStatusError("x", 402)) and not R.out_of_credits(APIStatusError("Error code: 429 - slow down", 429)))
q = R.classify(APIStatusError("You exceeded your current quota", 429))
check("…while a quota error stays RETRYABLE in classify (a signed fallback still takes over)", q.retryable and q.reason == "rate_limit")
check("describe() names the class the sentence is built from", TF.describe(e402)["reason"] == "credits" and TF.describe(e402)["provider"] is True)

print("\n── §3 every engine's failure path delivers the sentence")
ca = src("src/trusted_data_agent/agent/conversation_agent.py")
check("ReAct executor catch-all: the sentence, not str(e)", '"response": _tf.answer(_unwrap(e)),' in ca
      and "I encountered an error while processing your request: {str(e)}" not in ca)
ce = src("src/trusted_data_agent/agent/engines/conversation_engine.py")
check("ReAct engine: its own exception branch speaks the sentence", "error_msg = _tf.answer(_unwrap(e))" in ce and 'f"Agent execution failed: {str(e)}"' not in ce)
check("ReAct engine: a failed result is recorded failed, the sentence as error_message, the raw error as error_details",
      '"status": "success" if success else "failed",' in ce and '"error_message": response_text or "The turn failed."' in ce and '"error_details": str(result.get("error")' in ce)
gc = src("src/trusted_data_agent/agent/genie_coordinator.py")
check("coordinator catch-all: the sentence", '"coordinator_response": _tf.answer(_unwrap(e)),' in gc and 'f"Error during coordination: {str(e)}"' not in gc)
oe = src("src/trusted_data_agent/agent/engines/optimize_engine.py")
check("Optimize: the error event and the saved turn carry the sentence; the raw error stays as details",
      '"error": _honest,' in oe and "error_message=_honest," in oe and '"details": str(root_exception),' in oe)

pl = src("src/trusted_data_agent/agent/planner.py")
_i = pl.index('reason=f"Generating a strategic meta-plan for the goal:')
_strat = pl[_i:pl.index("\n            )", _i)]   # the strategic call's own argument list
check("Optimize: the strategic call raises a provider failure (it read as an empty plan → 'internal error (RuntimeError)')",
      "raise_on_error=True," in _strat)
fe = src("src/trusted_data_agent/agent/engines/focus_engine.py")
check("Focus: a failed synthesis (either branch) is recorded failed with the sentence, never as a success",
      'if agent_result.get("success") is False:' in fe and "raise_on_error=True," in fe
      and '"status": "failed" if _synthesis_error is not None else "success",' in fe)
ex = src("src/trusted_data_agent/agent/executor.py")
check("a failed turn persists the assurance stamps (integrity at answer time) — I2/I8 on Optimize",
      "_tally.snapshot()" in ex and "turn_summary.setdefault(_k, _v)" in ex)

print("\n── §4 clean_termination fails a turn the record calls failed")
from trusted_data_agent.eval.scorers.reliability import CleanTerminationScorer, OutputSanityScorer
from trusted_data_agent.eval.types import RunRecord, EvalCase
case = EvalCase(id="c", query="q", profile_class="conversation_with_tools", assertions=[])
failed = RunRecord({"status": "failed", "final_summary_text": TF.answer(e402), "error_message": TF.answer(e402),
                    "provenance_chain": [{"step_type": "turn_complete"}]})
v = CleanTerminationScorer().score(case, failed)
check("a failed ReAct turn with a turn_complete step FAILS (it used to pass)", v.passed is False and "the turn failed" in v.explanation)
check("…and its explanation is the honest sentence, never the raw error", "no credits left" in v.explanation and "Error code" not in v.explanation)
err = RunRecord({"status": "error", "error_message": TF.answer(e402)})
check("a failed Optimize turn with no answer FAILS", CleanTerminationScorer().score(case, err).passed is False)
ok = RunRecord({"status": "success", "final_summary_text": "There are 1,200 customers.", "provenance_chain": [{"step_type": "turn_complete"}]})
check("a successful turn still passes", CleanTerminationScorer().score(case, ok).passed is True)

print("\n── §5 output_sanity recognises a provider's raw error")
leak = RunRecord({"status": "success", "final_summary_text": "I encountered an error while processing your request: " + RAW_402})
check("the C18 answer is flagged as a leak", OutputSanityScorer().score(case, leak).passed is False)
check("the honest sentence is clean", OutputSanityScorer().score(case, RunRecord({"final_summary_text": TF.answer(e402)})).passed is True)
check("ordinary prose about errors is not a leak",
      OutputSanityScorer().score(case, RunRecord({"final_summary_text": "Read the error message the API returns."})).passed is True)

print("\n── §6 an empty account counts against the provider breaker")
from trusted_data_agent.llm import handler as H
calls = []
_orig = (H._provider_health.record_failure, H._provider_health.record_probe_answered)
H._provider_health.record_failure = lambda p, why, now=None: calls.append(("failure", p, why))
H._provider_health.record_probe_answered = lambda p, now=None: calls.append(("answered", p))
try:
    H._record_terminal("OpenRouter", e402)
    H._record_terminal("OpenAI", APIStatusError("Error code: 400 - bad request", 400))
finally:
    H._provider_health.record_failure, H._provider_health.record_probe_answered = _orig
check("credits → a breaker failure (the health strip shows it; an open breaker routes to the fallback)", calls[0] == ("failure", "OpenRouter", "credits"))
check("any other terminal answer still reads 'reachable'", calls[1] == ("answered", "OpenAI"))
hd = src("src/trusted_data_agent/llm/handler.py")
check("both terminal branches use the one rule", hd.count("_record_terminal(effective_provider, e)") == 2 and hd.count("_provider_health.record_probe_answered(effective_provider)") == 0)

print("\n── §7 a failed turn is scored, without judges")
on = src("src/trusted_data_agent/eval/online.py")
check("judges are skipped for a failed turn (and for an interrupted one — the one rule, core/turn_states)",
      'if _ts.is_failure(run.status) or _ts.is_interrupted(run.status):' in on and 'include_judges = False' in on)
es = src("src/trusted_data_agent/agent/execution_service.py")
check("an engine that ended the turn with an error event is still dispatched for scoring",
      'elif final_result_payload is None and _errored_turn_id is not None:' in es and '_errored_turn_id = event_data.get("turn_id")' in es)

print("\n── §8 I6 accepts the failure measured at JUDGED")
sys.path.insert(0, str(ROOT / "test" / "trust_campaign"))
import suite as SU  # noqa: E402
def i6(turn, points):
    t = dict(turn, assurance={"points": points, "measures": {"scored": [{"k": "clean_termination"}]}, "by_station": {}})
    return next(r for r in SU.check_invariants(t) if r["id"] == "I6")
quiet = [{"key": "llm_recovery", "state": "na"}]
failed_turn = {"status": "failed", "final_summary_text": TF.answer(e402),
               "eval": {"dimensions": [{"k": "clean_termination", "b": "danger"}]}}
r_ok = i6(failed_turn, quiet)
check("a failed turn whose clean_termination is red satisfies I6", r_ok["ok"] is True, str(r_ok))
silent = dict(failed_turn, eval={"dimensions": [{"k": "tokens", "b": "danger"}]})
r_bad = i6(silent, quiet)
check("an unrelated red measure does not", r_bad["ok"] is False, str(r_bad))

print("\n── §8 a data source that cannot be reached is not the model provider")
# FOUND 2026-11-01: an agent's MCP server was unreachable; the turn read "The language model
# provider did not answer: the provider could not be reached".
ds = TF.DataSourceUnavailable("Teradata 8889", "ConnectError")
a = TF.answer(ds)
check("a named data-source failure says the data source, names it, and says what to check",
      a.startswith("The agent's data source (Teradata 8889) could not be reached") and "MCP server" in a
      and TF.PROVIDER_PREFIX not in a and a.endswith(TF.NOTHING_DONE.strip()), a)
try:
    try:
        raise ConnectionError("All connection attempts failed")
    except ConnectionError as inner:
        raise TF.DataSourceUnavailable("Sales DW", "ConnectError") from inner
except TF.DataSourceUnavailable as wrapped:
    d = TF.describe(wrapped)
check("…described as the data source's, never counted as the provider's",
      d["reason"] == "data_source" and d["provider"] is False and d["source"] == "Sales DW", d)
_ns = {}
exec(compile("def boom():\n    raise ConnectionError('All connection attempts failed')\n",
             "/opt/env/lib/python3.12/site-packages/mcp/client/streamable_http.py", "exec"), _ns)
try:
    try:
        _ns["boom"]()
    except ConnectionError as leaf:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [leaf])
except ExceptionGroup as grp:
    from trusted_data_agent.core.utils import unwrap_exception
    leaf = unwrap_exception(grp)
check("a failure raised inside the MCP client library reads as the data source's, through the engines' unwrap",
      TF.describe(leaf)["reason"] == "data_source" and "data source" in TF.answer(leaf), TF.answer(leaf))
check("a connection failure anywhere else still reads as the provider's",
      TF.answer(ConnectionError("All connection attempts failed")).startswith(TF.PROVIDER_PREFIX))
_ns2 = {}
exec(compile("def ours():\n    raise ValueError('bad schema')\n",
             "/repo/src/trusted_data_agent/mcp_adapter/adapter.py", "exec"), _ns2)
try:
    _ns2["ours"]()
except ValueError as own:
    check("a defect in Uderia's own MCP adapter is not read as the data source being down",
          TF.describe(own)["reason"] != "data_source")
lsrc = src("src/trusted_data_agent/llm/langchain_adapter.py")
check("the MCP tool loader raises the data-source failure for an MCP-layer failure, and re-raises anything else",
      "if source_failure(e)[0]:" in lsrc and "raise DataSourceUnavailable(" in lsrc)

# behavioural: the real loader against a port nobody listens on
import asyncio  # noqa: E402
import trusted_data_agent.core.config_manager as _CM  # noqa: E402
class _FakeCM:
    def get_profile_enabled_tools(self, p, u): return ["*"]
    def get_mcp_servers(self, u):
        return [{"id": "srv-x", "name": "Closed port", "transport": {"type": "http"},
                 "host": "127.0.0.1", "port": 9, "path": "/mcp"}]
_real_cm = _CM.get_config_manager
_CM.get_config_manager = lambda: _FakeCM()
import trusted_data_agent.llm.langchain_adapter as LA  # noqa: E402
_la_cm = getattr(LA, "get_config_manager", None)
LA.get_config_manager = lambda: _FakeCM()
try:
    got = None
    try:
        asyncio.run(asyncio.wait_for(LA.load_mcp_tools_for_langchain(mcp_server_id="srv-x", profile_id="p", user_uuid="u"), 60))
    except BaseException as ex:  # noqa: BLE001
        got = ex
    check("the real loader, against a server nobody answers, raises the named data-source failure",
          isinstance(got, TF.DataSourceUnavailable) and got.server == "Closed port", repr(got))
    check("…and the turn's sentence names it", got is not None and "data source (Closed port)" in TF.answer(got))
finally:
    _CM.get_config_manager = _real_cm
    if _la_cm is not None:
        LA.get_config_manager = _la_cm

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
