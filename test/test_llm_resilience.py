"""S4 — the Resilience Envelope, gated. Spec: BEYOND_CLAUDE_CONVERSATION_AGENT.md §S4.

  [1] the policy truth table (classify · backoff/retry-after · finish kinds · should_retry)
  [2] continuation: bounded, provider-neutral, joined without restating
  [3] refusal → honest answer (never retried, never a fallback)
  [4] the breaker: closed → open → half_open → closed / re-open doubling, capped; only
      retryable failures count; a healthy call clears
  [5] the handler, DRIVEN with the switch on: retry+record, terminal fails fast, retry-after
      honoured, continuation, refusal, fallback (exhausted AND breaker-open), a fallback never
      falls back, cost/identity at the model that SERVED (the 5-tuple names it)
  [6] the signed fallback: only `llmFallbackConfigurationId`, only a config the user owns,
      never the primary, never improvised
  [7] the turn accumulator + stamp + the live event shape; sealed as `llm_recovery`, verified
  [8] the LangChain envelope: wrap re-classes in place, retry, refusal → honest AIMessage,
      fallback re-binds the raw tools, served_by stamped, a yielded stream is never re-run
  [9] the assurance mechanism at SIGNED; the deployment signature carries the fallback
  [10] structural: switch off ⇒ the pre-S4 loop; the seams route through ONE policy

CI-pure: no server, no network, no provider SDK call."""
import ast, asyncio, json, logging, os, pathlib, re, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ["TDA_RESILIENCE_ENVELOPE"] = "1"
from trusted_data_agent.core.config import APP_CONFIG
APP_CONFIG.RESILIENCE_ENVELOPE = True

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
_LOOP = asyncio.new_event_loop()
def run(c): return _LOOP.run_until_complete(c)

from trusted_data_agent.llm import resilience as R
from trusted_data_agent.core import provider_health as PH
from trusted_data_agent.agent import resilience_envelope as RE
from openai import APIError as OAErr, RateLimitError as OARate
import anthropic

print("[1] the policy truth table")
def E(msg="", **attrs):
    e = Exception(msg)
    for k, v in attrs.items(): setattr(e, k, v)
    return e
check("429 → retryable rate_limit", R.classify(E("x", status_code=429)).reason == "rate_limit" and R.classify(E("x", status_code=429)).retryable)
check("RateLimitError by NAME (any SDK) → rate_limit", R.classify(OARate("rate", response=types.SimpleNamespace(status_code=429, headers={}, request=None), body=None)).reason == "rate_limit")
check("529/503 → overloaded", R.classify(E("overloaded", status_code=529)).reason == "overloaded" and R.classify(E("x", status_code=503)).reason == "overloaded")
check("500 → server_error, retryable", R.classify(E("x", status_code=500)).reason == "server_error" and R.classify(E("x", status_code=500)).retryable)
check("InternalServerError by name → retryable", R.classify(anthropic.InternalServerError("boom", response=types.SimpleNamespace(status_code=500, headers={}, request=None), body=None)).retryable)
check("timeout by name → timeout", R.classify(type("APITimeoutError", (Exception,), {})("t")).reason == "timeout")
check("connection reset → connection", R.classify(ConnectionResetError("reset")).reason == "connection")
check("401/403 → terminal auth", R.classify(E("x", status_code=401)).kind == R.TERMINAL and R.classify(E("x", status_code=403)).reason == "auth")
check("404 → terminal not_found", R.classify(E("x", status_code=404)).reason == "not_found")
check("400 → terminal bad_request", R.classify(E("x", status_code=400)).reason == "bad_request")
check("'maximum context length' → terminal context_too_long (never retried)", R.classify(E("This model's maximum context length is 8192 tokens")).reason == "context_too_long")
check("an UNKNOWN error is terminal (retrying a bug is noise)", R.classify(ValueError("kaboom")).kind == R.TERMINAL and R.classify(ValueError("kaboom")).reason == "unknown")
check("a plain 'rate limit' message classifies without a status", R.classify(Exception("Rate limit exceeded, slow down")).reason == "rate_limit")
check("retry-after seconds honoured from the response header", R.classify(E("x", status_code=429, response=types.SimpleNamespace(status_code=429, headers={"retry-after": "7"}))).retry_after == 7.0)
check("retry-after HTTP-date parsed to seconds", 0 <= (R.parse_retry_after("Thu, 01 Jan 2099 00:00:00 GMT") or -1) and R.parse_retry_after("garbage") is None)
check("backoff: base·2^(n-1), capped", R.backoff(1, base=2) == 2 and R.backoff(3, base=2) == 8 and R.backoff(10, base=2) == R.MAX_BACKOFF_SECONDS)
check("backoff: a provider's retry-after beats the schedule", R.backoff(3, base=2, retry_after=1.5) == 1.5)
check("backoff: a retry-after beyond the honoured max is NOT waited for", R.backoff(1, base=2, retry_after=999) == 2)
check("should_retry: retryable + attempts left", R.should_retry(R.classify(E("x", status_code=503)), 1, 5) and not R.should_retry(R.classify(E("x", status_code=503)), 5, 5))
check("should_retry: never a terminal", not R.should_retry(R.classify(E("x", status_code=400)), 1, 5))
check("should_retry: a retry-after > 60s means 'come back later', not a wait", not R.should_retry(R.classify(E("x", status_code=429, retry_after=120)), 1, 5))
for fin, kind in [("stop", R.STOPPED), ("end_turn", R.STOPPED), ("length", R.TRUNCATED), ("max_tokens", R.TRUNCATED), ("MAX_TOKENS", R.TRUNCATED),
                  ("FinishReason.MAX_TOKENS", R.TRUNCATED), ("refusal", R.REFUSED), ("content_filter", R.REFUSED), ("SAFETY", R.REFUSED), ("FinishReason.RECITATION", R.REFUSED), (None, R.STOPPED)]:
    check(f"finish_kind({fin!r}) = {kind}", R.finish_kind(fin) == kind)
check("refusal_category maps provider spellings", R.refusal_category("FinishReason.SAFETY") == "safety" and R.refusal_category("content_filter") == "content_filter" and R.refusal_category("refusal") == "refusal")
check("recovery step kinds are the closed set", set(R.STEP_KINDS) == {"retry", "continuation", "fallback", "refusal"})

print("[2] continuation")
check("should_continue: truncated + partial + under the bound", R.should_continue("length", 0, "part") and R.should_continue("max_tokens", 1, "p"))
check("should_continue: never past MAX_CONTINUATIONS", not R.should_continue("length", R.MAX_CONTINUATIONS, "p"))
check("should_continue: an EMPTY truncation is an error, not a continuation", not R.should_continue("length", 0, "   "))
check("should_continue: a stopped answer is not continued", not R.should_continue("stop", 0, "p"))
h, p = R.continuation([{"role": "user", "content": "earlier"}], "the question", "the first half")
check("the partial becomes the assistant's own prior turn; the prompt becomes the instruction",
      h[-2] == {"role": "user", "content": "the question"} and h[-1] == {"role": "assistant", "content": "the first half"} and p == R.CONTINUE_INSTRUCTION and h[0]["content"] == "earlier")
check("the instruction forbids restating", "do not repeat" in R.CONTINUE_INSTRUCTION.lower())
check("join: no separator (the model begins with the next character)", R.join_continuation(["The total is 4", "4,000 miles."]) == "The total is 44,000 miles.")
check("join: a continuation that restates the tail (≥20 chars) is trimmed", R.join_continuation(["abc the quick brown fox jumps over", "the quick brown fox jumps over the lazy dog"]) == "abc the quick brown fox jumps over the lazy dog")
check("join: a short overlap is NOT trimmed (it may be real text)", R.join_continuation(["price 12", "12 dollars"]) == "price 1212 dollars")

print("[3] refusal → honest answer")
ans = R.refusal_answer("safety", "Google", "gemini")
check("names the provider, the model and the category in plain words", "Google" in ans and "gemini" in ans and "safety" in ans.lower())
check("never a canned technical error; says what to do", "technical error" not in ans.lower() and "rephras" in ans.lower())
check("ProviderRefusal classifies as REFUSAL with its category", R.classify(R.ProviderRefusal("safety", "G", "m")).kind == R.REFUSAL and R.classify(R.ProviderRefusal("safety", "G", "m")).category == "safety")
check("should_retry never retries a refusal", not R.should_retry(R.classify(R.ProviderRefusal("safety", "G", "m")), 1, 5))

print("[4] the breaker")
PH.reset()
b = PH.Breaker("P"); t = 1000.0
check("closed by default, allows", b.state == PH.CLOSED and b.allows(t))
b.record_failure("overloaded", t); b.record_failure("overloaded", t + 1)
check("two failures stay closed", b.state == PH.CLOSED)
tr = b.record_failure("overloaded", t + 2)
check("the third consecutive failure opens it (transition named)", b.state == PH.OPEN and tr == "closed->open" and b.trips == 1)
check("open: denies, recovers_in counts down", not b.allows(t + 3) and abs(b.recovers_in(t + 10) - 52.0) < 1e-6)
check("after the open period: half_open, ONE probe allowed, a second denied", b.allows(t + 2 + 60) and b.state == PH.HALF_OPEN and not b.allows(t + 2 + 61))
tr = b.record_failure("overloaded", t + 70)
check("a failed probe re-opens for twice as long", b.state == PH.OPEN and b.open_for == 120.0 and tr == "half_open->open" and b.trips == 2)
check("…capped at OPEN_MAX_SECONDS", all([b.record_failure("x", t + 200 + i * 700) or True for i in range(6)]) and b.open_for <= PH.OPEN_MAX_SECONDS)
b2 = PH.Breaker("Q"); b2.record_failure("x", t); b2.record_failure("x", t + 1); b2.record_failure("x", t + 2)
check("a successful probe closes it and resets the period", b2.allows(t + 62) and b2.record_success(t + 63) == "half_open->closed" and b2.state == PH.CLOSED and b2.open_for == PH.OPEN_SECONDS)
b3 = PH.Breaker("R"); b3.record_failure("x", t); b3.record_success(t + 1); b3.record_failure("x", t + 2); b3.record_failure("x", t + 3)
check("a success clears the consecutive count", b3.state == PH.CLOSED and b3.consecutive_failures == 2)
b4 = PH.Breaker("S"); b4.record_failure("x", t); b4.record_failure("x", t + 200)
check("failures outside the window do not accumulate", b4.consecutive_failures == 1)
PH.reset()
seen = []
PH.add_listener(lambda snap: seen.append(snap["transition"]))
for _ in range(3): PH.record_failure("Z", "overloaded")
check("the registry notifies listeners on a transition", seen == ["closed->open"] and PH.degraded()[0]["provider"] == "Z")
PH.reset(); PH._LISTENERS.clear()

print("[5] the handler, driven with the switch on")
from trusted_data_agent.llm import handler as H
class _U: prompt_tokens = 11; completion_tokens = 7; prompt_tokens_details = None
class _R:
    def __init__(s, c, f="stop"): s.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=c), finish_reason=f)]; s.usage = _U()
    def dict(s): return {}
class Fake:
    def __init__(s, script):
        s.script = list(script); s.calls = []; o = s
        class C:
            async def create(self_, **kw):
                o.calls.append(kw); item = o.script.pop(0)
                if item[0] == "err": raise item[1]
                return _R(item[1], item[2])
        class Ch: completions = C()
        s.chat = Ch()
sleeps = []
async def fs(d): sleeps.append(d)
_orig_sleep, _orig_rand = H.asyncio.sleep, H.random.uniform
H.asyncio.sleep = fs; H.random.uniform = lambda a, b: 0.0
def err(status, msg="boom", ra=None):
    e = OAErr(msg, request=None, body=None); e.status_code = status
    if ra is not None: e.response = types.SimpleNamespace(status_code=status, headers={"retry-after": str(ra)})
    return e
def call(f, **kw):
    return run(H.call_llm_api(f, "hi", current_provider="OpenAI", current_model="m", raise_on_error=True, disabled_history=True, **kw))
def arm(fb=None, prov="OpenAI"):
    PH.reset()
    return RE.set_turn(RE.TurnResilience(user_uuid="u", session_id="s", primary_provider=prov, primary_model="m", fallback=fb))
APP_CONFIG.LLM_API_MAX_RETRIES = 3; APP_CONFIG.LLM_API_BASE_DELAY = 2

tok = arm()
f = Fake([("err", err(429, "rate limit", ra=3)), ("ok", "hello", "stop")]); sleeps.clear()
r = call(f)
st = RE.stamp_for_turn()
check("a retryable error is retried, the provider's retry-after honoured, the step RECORDED", r[0] == "hello" and len(f.calls) == 2 and sleeps == [3.0] and st["steps"][0]["kind"] == "retry" and st["steps"][0]["reason"] == "rate_limit" and st["steps"][0]["delay"] == 3.0)
check("the 5-tuple names the configured model when it served", r[3] == "OpenAI" and r[4] == "m" and st["served_by"] is None)
RE.reset_turn(tok)

tok = arm(); f = Fake([("err", err(400, "bad request"))] * 3); sleeps.clear()
try: call(f); raised = None
except Exception as e: raised = e
check("a terminal error fails fast: ONE call, no sleep, the error raised", len(f.calls) == 1 and sleeps == [] and isinstance(raised, OAErr) and not RE.stamp_for_turn())
check("…and a terminal error is NOT a breaker failure (the provider is healthy; the request was wrong)", PH.get("OpenAI").consecutive_failures == 0)
RE.reset_turn(tok)

tok = arm(); f = Fake([("err", ValueError("This model's maximum context length is 8192 tokens"))]); 
try: call(f); raised = None
except Exception as e: raised = e
check("context-too-long (a non-SDK exception) is terminal on the first attempt", len(f.calls) == 1 and isinstance(raised, ValueError))
RE.reset_turn(tok)

tok = arm(); f = Fake([("err", ConnectionResetError("reset")), ("ok", "ok", "stop")]); sleeps.clear()
r = call(f)
check("a non-SDK retryable exception (connection reset) is retried on the schedule", r[0] == "ok" and len(f.calls) == 2 and sleeps == [2.0])
RE.reset_turn(tok)

tok = arm(); f = Fake([("ok", "The total is 4", "length"), ("ok", "4,000 miles.", "stop")])
r = call(f)
st = RE.stamp_for_turn()
m2 = f.calls[1]["messages"]
check("a truncated answer is CONTINUED: the partial rides as the assistant turn, the instruction as the prompt, the text joined",
      r[0] == "The total is 44,000 miles." and len(f.calls) == 2 and m2[-1]["content"] == R.CONTINUE_INSTRUCTION and m2[-2]["role"] == "assistant" and m2[-2]["content"] == "The total is 4" and m2[-3]["content"] == "hi")
check("…recorded as a continuation step", st["counts"] == {"continuation": 1})
RE.reset_turn(tok)

tok = arm(); f = Fake([("ok", "a", "length"), ("ok", "b", "length"), ("ok", "c", "length"), ("ok", "d", "stop")])
r = call(f)
check("continuation is BOUNDED to MAX_CONTINUATIONS (3 calls, the rest not asked for)", len(f.calls) == 1 + R.MAX_CONTINUATIONS and r[0] == "abc")
RE.reset_turn(tok)

tok = arm(); f = Fake([("ok", "", "content_filter"), ("ok", "should not be called", "stop")])
try: call(f); raised = None
except R.ProviderRefusal as e: raised = e
st = RE.stamp_for_turn()
check("a refusal raises ProviderRefusal on the FIRST attempt — never retried, never a fallback", isinstance(raised, R.ProviderRefusal) and len(f.calls) == 1 and st["refusal"]["category"] == "content_filter" and st["counts"] == {"refusal": 1})
RE.reset_turn(tok)
tok = arm(); f = Fake([("ok", "", "content_filter")])
r = run(H.call_llm_api(f, "hi", current_provider="OpenAI", current_model="m", raise_on_error=False, disabled_history=True))
check("…with raise_on_error=False the answer is empty (a utility call), the refusal still recorded", r[0] == "" and RE.stamp_for_turn()["refusal"])
RE.reset_turn(tok)

fb = {"id": "fb", "provider": "Anthropic", "model": "fb-model", "credentials": {"apiKey": "k"}}
served = []
class AFake:
    def __init__(s, text): s.text = text; s.calls = 0; s.messages = types.SimpleNamespace(create=s._create)
    async def _create(s, **kw):
        s.calls += 1; served.append(kw.get("model"))
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=s.text)], stop_reason="end_turn",
                                     usage=types.SimpleNamespace(input_tokens=5, output_tokens=3, cache_read_input_tokens=0, cache_creation_input_tokens=0), dict=lambda: {})
afake = AFake("from the fallback")
async def _fbc(fb_, uu): return afake
H._fallback_client = _fbc
tok = arm(fb); f = Fake([("err", err(503, "overloaded"))] * 5); sleeps.clear()
r = call(f)
st = RE.stamp_for_turn()
check("an EXHAUSTED primary is served by the signed fallback; the 5-tuple names the model that SERVED",
      r[0] == "from the fallback" and r[3] == "Anthropic" and r[4] == "fb-model" and len(f.calls) == 3 and afake.calls == 1 and served == ["fb-model"])
check("…recorded: 2 retries then ONE fallback step naming from → to", st["counts"] == {"retry": 2, "fallback": 1} and st["steps"][-1]["from"] == "OpenAI/m" and st["steps"][-1]["to"] == "Anthropic/fb-model" and st["served_by"] == {"provider": "Anthropic", "model": "fb-model"} and st["fallback"] is True)
RE.reset_turn(tok)

# a fallback never falls back: depth 1 exhausted → the error
afake2 = AFake("x")
async def _boom(**kw): raise err(503, "fallback overloaded")
afake2.messages = types.SimpleNamespace(create=_boom)
async def _fbc2(fb_, uu): return afake2
H._fallback_client = _fbc2
tok = arm(fb); f = Fake([("err", err(503, "overloaded"))] * 5)
try: call(f); raised = None
except Exception as e: raised = e
check("a fallback that is itself exhausted raises (never a second fallback)", raised is not None and RE.stamp_for_turn()["counts"].get("fallback") == 1)
RE.reset_turn(tok)

# breaker open → fallback immediately, no attempt on the primary
H._fallback_client = _fbc; afake.calls = 0; served.clear()
tok = arm(fb)
for _ in range(3): PH.record_failure("OpenAI", "overloaded")
f = Fake([("ok", "primary answered", "stop")])
r = call(f)
st = RE.stamp_for_turn()
check("breaker OPEN → the fallback serves IMMEDIATELY (zero calls on the primary), reason breaker_open",
      r[0] == "from the fallback" and len(f.calls) == 0 and st["steps"][0]["kind"] == "fallback" and st["steps"][0]["reason"] == "breaker_open")
RE.reset_turn(tok)
# breaker open, NO fallback → still attempted, once, no backoff
tok = arm(None)
for _ in range(3): PH.record_failure("OpenAI", "overloaded")
f = Fake([("err", err(503))] * 3); sleeps.clear()
try: call(f); raised = None
except Exception as e: raised = e
check("breaker OPEN with no fallback → ONE attempt, no backoff, the error (never refused for lack of an alternative)", len(f.calls) == 1 and sleeps == [] and raised is not None)
RE.reset_turn(tok)

# a refusal is never handed to the fallback
H._fallback_client = _fbc; afake.calls = 0
tok = arm(fb); f = Fake([("ok", "", "content_filter")])
try: call(f)
except R.ProviderRefusal: pass
check("a refusal with a fallback configured: the fallback is NOT asked (a second model must not override a safety decision)", afake.calls == 0)
RE.reset_turn(tok)

# the primary's success closes what its failures opened
tok = arm(); f = Fake([("err", err(503)), ("ok", "ok", "stop")])
call(f)
check("a success after failures leaves the breaker closed with the count cleared", PH.get("OpenAI").state == PH.CLOSED and PH.get("OpenAI").consecutive_failures == 0)
RE.reset_turn(tok)
H.asyncio.sleep, H.random.uniform = _orig_sleep, _orig_rand

print("[6] the signed fallback")
class _CM:
    def __init__(s, cfgs): s.cfgs = cfgs
    def get_llm_configurations(s, uu): return s.cfgs
import trusted_data_agent.core.config_manager as _cm_mod
_real_gcm = _cm_mod.get_config_manager
_cm_mod.get_config_manager = lambda: _CM([{"id": "a", "provider": "OpenAI", "model": "gpt", "credentials": {"apiKey": "k"}}, {"id": "b", "provider": "Google", "model": "gem"}])
try:
    check("resolves the named config the user owns", RE.resolve_fallback({"llmConfigurationId": "a", "llmFallbackConfigurationId": "b"}, "u") == {"id": "b", "provider": "Google", "model": "gem", "credentials": {}})
    check("no key → None (no fallback is improvised)", RE.resolve_fallback({"llmConfigurationId": "a"}, "u") is None)
    check("a config the user does NOT own → None (refused, not guessed)", RE.resolve_fallback({"llmConfigurationId": "a", "llmFallbackConfigurationId": "zzz"}, "u") is None)
    check("the primary as its own fallback → None", RE.resolve_fallback({"llmConfigurationId": "a", "llmFallbackConfigurationId": "a"}, "u") is None)
    check("no user → None", RE.resolve_fallback({"llmFallbackConfigurationId": "b"}, None) is None)
finally:
    _cm_mod.get_config_manager = _real_gcm
from trusted_data_agent.core import profile_config_contract as PCC
check("llmFallbackConfigurationId is an instance BINDING (never portable)", "llmFallbackConfigurationId" in PCC.IMPORT_BINDING_KEYS and "llmFallbackConfigurationId" not in PCC.portable_keys())

print("[7] the turn accumulator, the stamp, the live event, the seal")
check("no armed turn → record is a no-op, stamp None", RE.record(R.RecoveryStep(kind="retry", reason="x")) is None and RE.stamp_for_turn() is None)
tok = RE.set_turn(RE.TurnResilience(user_uuid="u", session_id="s", primary_provider="P", primary_model="m"))
check("an armed turn with nothing recovered stamps NOTHING (byte-identical baseline)", RE.stamp_for_turn() is None)
d = RE.record(R.RecoveryStep(kind="retry", reason="overloaded", from_model="P/m", to_model="P/m", attempt=2, delay=2.0, status=503))
check("a recorded step carries kind/reason/from/to/attempt/delay/status/at + n", d["kind"] == "retry" and d["from"] == "P/m" and d["n"] == 1 and "at" in d)
ev = RE.recovery_event(d, "s")
check("the live event: step title per kind, type llm_recovery, a summary sentence, session-tagged", ev["type"] == "llm_recovery" and ev["step"] == "Provider Retry" and "retried after 2.0s" in ev["details"]["summary"] and ev["details"]["session_id"] == "s")
RE.record(R.RecoveryStep(kind="fallback", reason="overloaded", from_model="P/m", to_model="Q/n"))
st = RE.stamp_for_turn()
check("the stamp: steps, counts, primary, served_by, fallback flag", st["counts"] == {"retry": 1, "fallback": 1} and st["served_by"] == {"provider": "Q", "model": "n"} and st["fallback"] and st["primary"] == {"provider": "P", "model": "m"})
check("describe() names the served model and the billing", "Q/n served instead" in RE.describe(st["steps"][1]) and "billed at Q/n" in RE.describe(st["steps"][1]))
from trusted_data_agent.core import provenance as PV
chain = PV.ProvenanceChain(session_id="s", turn_number=1, user_uuid="u", profile_type="llm_only")
chain.add_step("query_intake", "q", "q")
env = chain.finalize()
types_ = [s_["step_type"] for s_ in env["provenance_chain"]]
check("finalize() seals every recorded step as llm_recovery (ONE chokepoint, every engine)", types_.count("llm_recovery") == 2)
check("sealable content is timestamp-free (an identical recovery hashes identically)", "at" not in json.loads(PV.recovery_step_content(st["steps"][0])) and R.sealable(st["steps"][0])["kind"] == "retry")
v = PV.verify_content(env, {"resilience": st})
check("verify_content re-derives the sealed steps from the stamp", v["verified"] >= 2 and not [m for m in v["mismatches"] if "llm_recovery" in m])
bad = dict(st); bad["steps"] = [dict(st["steps"][0], reason="tampered")] + st["steps"][1:]
v2 = PV.verify_content(env, {"resilience": bad})
check("a tampered step is a NAMED mismatch", any("llm_recovery" in m for m in v2["mismatches"]))
RE.reset_turn(tok)
check("stamps never carry private keys", all(not k.startswith("_") for s_ in st["steps"] for k in s_))
tok = RE.set_turn(RE.TurnResilience(user_uuid="u", session_id="s"))
check("served_model: the configured pair until a fallback served", RE.served_model("P", "m") == ("P", "m"))
RE.record(R.RecoveryStep(kind="fallback", reason="x", from_model="P/m", to_model="Q/n"))
check("served_model: the fallback once it served (found live: models_used named the model that never answered)", RE.served_model("P", "m") == ("Q", "n"))
RE.reset_turn(tok)
check("served_model outside a turn: the pair given", RE.served_model("P", "m") == ("P", "m"))
for _eng in ("conversation_engine", "focus_engine", "coordinate_engine"):
    _e = src(f"src/trusted_data_agent/agent/engines/{_eng}.py")
    check(f"{_eng} records models_used through served_model (never the configured pair alone)", _e.count("update_models_used(") == _e.count("_renv.served_model("), (_e.count("update_models_used("), _e.count("_renv.served_model(")))

print("[8] the LangChain envelope")
from trusted_data_agent.llm import resilient_chat as RC
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult, ChatGenerationChunk
from langchain_core.messages import AIMessageChunk
class FakeChat(BaseChatModel):
    script: list = []
    calls: list = []
    bound: list = []
    @property
    def _llm_type(self): return "fake"
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        item = self.script.pop(0)
        if item[0] == "err": raise item[1]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=item[1], response_metadata={"finish_reason": item[2]}, usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}))])
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs, "stream": True})
        item = self.script.pop(0)
        if item[0] == "err": raise item[1]
        if item[0] == "errmid":
            yield ChatGenerationChunk(message=AIMessageChunk(content="partial "))
            raise item[1]
        for i, ch in enumerate(item[1].split(" ")):
            last = i == len(item[1].split(" ")) - 1
            yield ChatGenerationChunk(message=AIMessageChunk(content=ch + ("" if last else " "), response_metadata=({"finish_reason": item[2]} if last else {})))
    def bind_tools(self, tools, **kwargs):
        self.bound.append(list(tools))
        return self.bind(tools=[{"fmt": self._llm_type, "name": t} for t in tools])
def mk(script):
    m = FakeChat(); m.script = list(script); m.calls = []; m.bound = []; return m
m = mk([("ok", "hi", "stop")])
w = RC.wrap(m, provider="OpenAI", model="m")
check("wrap re-classes IN PLACE (same object, a Resilient subclass, fields intact)", w is m and type(m).__name__ == "ResilientFakeChat" and isinstance(m, FakeChat) and RC.is_wrapped(m))
check("wrap is idempotent", RC.wrap(m, provider="OpenAI", model="m") is m and type(m).__name__ == "ResilientFakeChat")
import trusted_data_agent.llm.resilient_chat as _rcmod
_rc_sleep = _rcmod.asyncio.sleep; _rcmod.asyncio.sleep = fs; _rcmod.random.uniform = lambda a, b: 0.0
tok = arm()
m = mk([("err", err(503, "overloaded")), ("ok", "hello", "stop")]); RC.wrap(m, provider="OpenAI", model="m"); sleeps.clear()
res = run(m._agenerate([HumanMessage(content="q")]))
check("_agenerate retries a retryable error and records", res.generations[0].message.content == "hello" and len(m.calls) == 2 and sleeps == [2.0] and RE.stamp_for_turn()["counts"] == {"retry": 1})
RE.reset_turn(tok)
tok = arm(); m = mk([("err", err(401, "bad key"))]); RC.wrap(m, provider="OpenAI", model="m")
try: run(m._agenerate([HumanMessage(content="q")])); raised = None
except Exception as e: raised = e
check("_agenerate fails fast on a terminal error", isinstance(raised, OAErr) and len(m.calls) == 1)
RE.reset_turn(tok)
tok = arm(); m = mk([("ok", "", "content_filter")]); RC.wrap(m, provider="OpenAI", model="m")
res = run(m._agenerate([HumanMessage(content="q")]))
check("a refused completion → an honest AIMessage (0 tokens), recorded", "declined" in res.generations[0].message.content and res.generations[0].message.usage_metadata["input_tokens"] == 0 and RE.stamp_for_turn()["refusal"]["category"] == "content_filter")
RE.reset_turn(tok)
tok = arm(); m = mk([("ok", "The total is 4", "length"), ("ok", "4,000 miles.", "stop")]); RC.wrap(m, provider="OpenAI", model="m")
res = run(m._agenerate([HumanMessage(content="q")]))
check("_agenerate continues a truncated FINAL answer (bounded) and joins", res.generations[0].message.content == "The total is 44,000 miles." and len(m.calls) == 2 and isinstance(m.calls[1]["messages"][-2], AIMessage) and RE.stamp_for_turn()["counts"] == {"continuation": 1})
RE.reset_turn(tok)
# fallback with tool re-binding
class FakeChatB(FakeChat):
    @property
    def _llm_type(self): return "fake-b"
fbm = FakeChatB(); fbm.script = [("ok", "from fb", "stop")]; fbm.calls = []; fbm.bound = []
tok = arm(); m = mk([("err", err(503))] * 3); RC.wrap(m, provider="OpenAI", model="m", fallback_factory=lambda: fbm, fallback_identity={"provider": "Anthropic", "model": "fb"})
bound = m.bind_tools(["t1", "t2"])
res = run(m._agenerate([HumanMessage(content="q")], **bound.kwargs))
check("an exhausted primary is served by the fallback, the RAW tools re-bound in the fallback's format",
      res.generations[0].message.content == "from fb" and fbm.bound == [["t1", "t2"]] and fbm.calls[0]["kwargs"]["tools"][0]["fmt"] == "fake-b" and m.calls[0]["kwargs"]["tools"][0]["fmt"] == "fake")
check("…served_by stamped on the message; the fallback step recorded", res.generations[0].message.response_metadata["uderia_served_by"] == {"provider": "Anthropic", "model": "fb"} and RE.stamp_for_turn()["served_by"] == {"provider": "Anthropic", "model": "fb"})
RE.reset_turn(tok)
# streams
async def collect(agen):
    out = []
    async for c in agen: out.append(c)
    return out
tok = arm(); m = mk([("err", err(503)), ("ok", "a b c", "stop")]); RC.wrap(m, provider="OpenAI", model="m"); sleeps.clear()
chunks = run(collect(m._astream([HumanMessage(content="q")])))
check("_astream retries an OPENING error", "".join(c.message.content for c in chunks) == "a b c" and len(m.calls) == 2)
RE.reset_turn(tok)
tok = arm(); m = mk([("errmid", err(503)), ("ok", "never", "stop")]); RC.wrap(m, provider="OpenAI", model="m")
try: run(collect(m._astream([HumanMessage(content="q")]))); raised = None
except Exception as e: raised = e
check("a stream that already YIELDED is never re-run — the error propagates", raised is not None and len(m.calls) == 1)
RE.reset_turn(tok)
tok = arm(); m = mk([("ok", "the first", "length"), ("ok", "half done.", "stop")]); RC.wrap(m, provider="OpenAI", model="m")
chunks = run(collect(m._astream([HumanMessage(content="q")])))
check("_astream continues a truncated final answer, yielding the continuation's chunks", "".join(c.message.content for c in chunks) == "the firsthalf done." and RE.stamp_for_turn()["counts"] == {"continuation": 1})
RE.reset_turn(tok)
_rcmod.asyncio.sleep = _rc_sleep
# construction-time: breaker open → the lane is built on the fallback
from trusted_data_agent.llm import langchain_adapter as LA
class _CM2:
    def get_llm_configurations(self, uu): return [{"id": "p", "provider": "OpenAI", "model": "gpt-4o", "credentials": {"apiKey": "k"}}, {"id": "f", "provider": "OpenAI", "model": "gpt-4o-mini", "credentials": {"apiKey": "k"}}]
_real = LA.get_config_manager; LA.get_config_manager = lambda: _CM2()
try:
    PH.reset()
    tok = RE.set_turn(RE.TurnResilience(user_uuid="u", session_id="s", fallback={"id": "f", "provider": "OpenAI", "model": "gpt-4o-mini"}))
    inst = LA.create_langchain_llm("p", "u")
    check("create_langchain_llm wraps the lane's model under the switch (a Resilient subclass)", type(inst).__name__.startswith("Resilient") and RC.envelope_of(inst).fallback_identity == {"provider": "OpenAI", "model": "gpt-4o-mini"})
    for _ in range(3): PH.record_failure("OpenAI", "overloaded")
    inst2 = LA.create_langchain_llm("p", "u")
    check("breaker OPEN at construction → the lane is built on the fallback model, recorded", getattr(inst2, "model_name", None) == "gpt-4o-mini" and RE.stamp_for_turn()["steps"][0]["reason"] == "breaker_open")
    RE.reset_turn(tok); PH.reset()
finally:
    LA.get_config_manager = _real


print("[8b] review fixes: the served-by stamp, the breaker on the LangChain lanes, the probe lease, timeouts")
# (H1) the served-by stamp is on the FIRST fallback chunk only — LangChain's merge_dicts
# CONCATENATES equal strings, so a stamp on every chunk read 'AnthropicAnthropic…' summed
_rcmod.asyncio.sleep = fs
fbm = FakeChatB(); fbm.script = [("ok", "a b c", "stop")]; fbm.calls = []; fbm.bound = []
tok = arm(); m = mk([("err", err(503))] * 3); RC.wrap(m, provider="OpenAI", model="m", fallback_factory=lambda: fbm, fallback_identity={"provider": "Anthropic", "model": "fb"})
chunks = run(collect(m._astream([HumanMessage(content="q")])))
summed = chunks[0]
for c in chunks[1:]: summed = summed + c
stamped = [c for c in chunks if (c.message.response_metadata or {}).get("uderia_served_by")]
check("_astream fallback: exactly ONE chunk carries the served-by stamp", len(stamped) == 1 and stamped[0] is chunks[0])
check("…and the SUMMED message reads the identity once (never 'AnthropicAnthropic…')", summed.message.response_metadata.get("uderia_served_by") == {"provider": "Anthropic", "model": "fb"} and "".join(c.message.content for c in chunks) == "a b c")
RE.reset_turn(tok)
# (M1) the LangChain lanes consult the breaker BEFORE the first attempt
fbm = FakeChatB(); fbm.script = [("ok", "from fb", "stop"), ("ok", "x y", "stop")]; fbm.calls = []; fbm.bound = []
tok = arm(); m = mk([("ok", "primary", "stop")] * 2); RC.wrap(m, provider="OpenAI", model="m", fallback_factory=lambda: fbm, fallback_identity={"provider": "Anthropic", "model": "fb"})
for _ in range(3): PH.record_failure("OpenAI", "overloaded")
res = run(m._agenerate([HumanMessage(content="q")]))
st = RE.stamp_for_turn()
check("_agenerate: breaker OPEN + a fallback → served by the fallback with ZERO calls on the primary, reason breaker_open",
      res.generations[0].message.content == "from fb" and len(m.calls) == 0 and st["steps"][0]["reason"] == "breaker_open" and res.generations[0].message.response_metadata["uderia_served_by"] == {"provider": "Anthropic", "model": "fb"})
chunks = run(collect(m._astream([HumanMessage(content="q")])))
check("_astream: breaker OPEN + a fallback → streamed from the fallback, zero calls on the primary", "".join(c.message.content for c in chunks) == "x y" and len(m.calls) == 0)
RE.reset_turn(tok)
tok = arm(); m = mk([("err", err(503))] * 3); RC.wrap(m, provider="OpenAI", model="m"); sleeps.clear()
for _ in range(3): PH.record_failure("OpenAI", "overloaded")
try: run(m._agenerate([HumanMessage(content="q")])); raised = None
except Exception as e: raised = e
check("_agenerate: breaker OPEN, no fallback → ONE attempt, no backoff, the error", len(m.calls) == 1 and sleeps == [] and raised is not None)
RE.reset_turn(tok)
# (M2) HALF_OPEN → the probe is ONE call (handler + LangChain lanes)
PH.reset(); b = PH.get("OpenAI"); t0 = 1000.0
for _ in range(3): b.record_failure("overloaded", now=t0)
check("the breaker re-opens as HALF_OPEN after its open period", b.allows(now=t0 + PH.OPEN_SECONDS + 1) and b.state == PH.HALF_OPEN and b.probe_in_flight)
# (H3) the probe is a LEASE: a probe cancelled from outside never reports, so its lease expires
check("a second call while the probe is in flight is refused (one probe)", not b.allows(now=t0 + PH.OPEN_SECONDS + 10))
check("…but a probe that never reported back releases its lease — the next call becomes the probe", b.allows(now=t0 + PH.OPEN_SECONDS + 1 + PH.PROBE_LEASE_SECONDS + 1) and b.probe_in_flight)
b.record_probe_answered(now=t0 + PH.OPEN_SECONDS + 200)
check("a TERMINAL answer to a half-open probe closes the breaker (the provider is reachable)", b.state == PH.CLOSED and not b.probe_in_flight)
b2 = PH.get("OpenAI"); b2.record_failure("overloaded", now=t0)
b2.record_probe_answered(now=t0 + 1)
check("…a terminal answer on a CLOSED breaker leaves its failure count alone", b2.consecutive_failures == 1 and b2.state == PH.CLOSED)
tok = arm(); m = mk([("err", err(503))] * 3); RC.wrap(m, provider="OpenAI", model="m"); sleeps.clear()
for _ in range(3): PH.record_failure("OpenAI", "overloaded", now=t0)
PH.get("OpenAI")._refresh(t0 + PH.OPEN_SECONDS + 1)
try: run(m._agenerate([HumanMessage(content="q")])); raised = None
except Exception as e: raised = e
check("_agenerate: HALF_OPEN → the probe is ONE attempt (three attempts would be three probes under one lease)", len(m.calls) == 1 and raised is not None)
RE.reset_turn(tok)
_rcmod.asyncio.sleep = _rc_sleep
# (H2) a hung provider: a timeout is capped at TIMEOUT_MAX_ATTEMPTS, and with a fallback bound
# it is served by the fallback after the FIRST attempt — never N × the attempt timeout
_to = R.classify(type("APITimeoutError", (Exception,), {})("timed out"))
check("should_retry caps a timeout at TIMEOUT_MAX_ATTEMPTS (never 5 waits on a hang)", R.should_retry(_to, 1, 5) and not R.should_retry(_to, R.TIMEOUT_MAX_ATTEMPTS, 5))
check("route_to_fallback_now: a timeout WITH a fallback goes now; without one, or any other reason, it does not",
      R.route_to_fallback_now(_to, True) and not R.route_to_fallback_now(_to, False) and not R.route_to_fallback_now(R.classify(E("x", status_code=503)), True))
H._fallback_client = _fbc; afake.calls = 0; served.clear()
tok = arm(fb); f = Fake([("err", type("APITimeoutError", (Exception,), {})("timed out"))] * 3); sleeps.clear()
r = call(f)
st = RE.stamp_for_turn()
check("handler: a TIMEOUT with a fallback bound → the fallback after ONE attempt, no backoff wait", r[0] == "from the fallback" and len(f.calls) == 1 and sleeps == [] and st["steps"][-1]["kind"] == "fallback" and st["steps"][-1]["reason"] == "timeout")
RE.reset_turn(tok)
tok = arm(None); f = Fake([("err", type("APITimeoutError", (Exception,), {})("timed out"))] * 5); sleeps.clear()
try: call(f); raised = None
except Exception as e: raised = e
check("handler: a TIMEOUT with no fallback is retried ONCE (2 attempts), then the error", len(f.calls) == R.TIMEOUT_MAX_ATTEMPTS and raised is not None)
RE.reset_turn(tok)
# the terminal branch releases a half-open probe
tok = arm(None); f = Fake([("err", err(401, "bad key"))])
for _ in range(3): PH.record_failure("OpenAI", "overloaded", now=t0)
PH.get("OpenAI")._refresh(t0 + PH.OPEN_SECONDS + 1)
try: call(f); raised = None
except Exception as e: raised = e
check("handler: a terminal answer from the half-open probe closes the breaker (never a latch)", raised is not None and PH.get("OpenAI").state == PH.CLOSED)
RE.reset_turn(tok)
# the attempt-level SDK timeout: only under the master; the baseline builds its clients as before
from trusted_data_agent.llm import client_factory as CF
_was = APP_CONFIG.RESILIENCE_ENVELOPE
try:
    APP_CONFIG.RESILIENCE_ENVELOPE = False
    check("master OFF → no attempt timeout is applied (baseline byte-identical)", CF._attempt_timeout_seconds() is None and float(getattr(run(CF.create_llm_client("Anthropic", "m", {"apiKey": "k"})).timeout, "read", run(CF.create_llm_client("Anthropic", "m", {"apiKey": "k"})).timeout) or 0) != float(APP_CONFIG.LLM_ATTEMPT_TIMEOUT_SECONDS))
    APP_CONFIG.RESILIENCE_ENVELOPE = True
    check("master ON → the Anthropic client carries the per-attempt timeout (a hang fails INSIDE the attempt loop)", float(getattr(run(CF.create_llm_client("Anthropic", "m", {"apiKey": "k"})).timeout, "read", run(CF.create_llm_client("Anthropic", "m", {"apiKey": "k"})).timeout) or 0) == float(APP_CONFIG.LLM_ATTEMPT_TIMEOUT_SECONDS))
    _cf = src("src/trusted_data_agent/llm/client_factory.py")
    check("…and the Google client is built with http_options(timeout=ms) under the same helper", "HttpOptions(timeout=int(_t * 1000))" in _cf and _cf.count("_attempt_timeout_seconds()") >= 2)
finally:
    APP_CONFIG.RESILIENCE_ENVELOPE = _was

print("[9] the assurance mechanism + the deployment signature")
from trusted_data_agent.agent import assurance_points as AP
check("llm_recovery is registered at SIGNED as a measure — evidence for the signature, not a control",
      AP.POINT_CLASS["llm_recovery"] == "measure" and any(p.key == "llm_recovery" and p.station == "signed" for p in AP.REGISTRY))
check("absent stamp → na (nothing recovered)", AP._read_llm_recovery({}, None).state == "na")
check("retries only → pass naming them", AP._read_llm_recovery({"resilience": {"steps": [{"kind": "retry"}], "counts": {"retry": 2}}}, None).state == "pass")
_fb = {"steps": [{"kind": "fallback"}], "counts": {"fallback": 1}, "served_by": {"provider": "Q", "model": "n"},
       "primary": {"provider": "P", "model": "m"}}
r9 = AP._read_llm_recovery({"resilience": dict(_fb, fallback_declared={"provider": "Q", "model": "n"})}, None)
check("served by the SIGNED fallback → pass, inside the signature, naming who served and the primary",
      r9.state == "pass" and r9.acted and "Q/n" in r9.detail and "P/m" in r9.detail
      and "inside the signature" in r9.detail)
check("…a turn recorded before the declared fallback was stamped also reads inside",
      AP._read_llm_recovery({"resilience": _fb}, None).state == "pass")
r9b = AP._read_llm_recovery({"resilience": dict(_fb, fallback_declared={"provider": "Z", "model": "z"})}, None)
check("a model that is NOT the signed fallback → warn, outside the signature (SIGNED's one finding)",
      r9b.state == "warn" and "outside the signature" in r9b.detail and "Z/z" in r9b.detail)
r9c = AP._read_llm_recovery({"resilience": {"steps": [{"kind": "refusal"}], "counts": {"refusal": 1}, "refusal": {"category": "safety"}}}, None)
check("a refusal → na (no model answered; never colours SIGNED), recorded as an act naming the category",
      r9c.state == "na" and r9c.acted and r9c.word == "refused" and "safety" in r9c.detail)
_tr = RE.TurnResilience(primary_provider="P", primary_model="m",
                        fallback={"id": "f", "provider": "Q", "model": "n", "credentials": {"k": "secret"}})
_tr.record(R.RecoveryStep(kind=R.STEP_RETRY, reason="rate_limit"))
_sum = _tr.summary()
check("the stamp declares the signed fallback — identity only, never credentials",
      _sum["fallback_declared"] == {"provider": "Q", "model": "n"} and "secret" not in str(_sum))
check("partition still conflict-free", AP.partition_conflicts() == [])
from trusted_data_agent.core.trust import deployment as DEP
sig_a = DEP.build_deployment_signature({"provider": "P", "model": "m"}, None, [])
sig_b = DEP.build_deployment_signature({"provider": "P", "model": "m"}, None, [], fallback_llm={"provider": "Q", "model": "n"})
check("the deployment signature carries the fallback identity; ABSENT when none (older attestations unchanged)", "fallback_llm" not in sig_a and sig_b["fallback_llm"] == {"provider": "Q", "model": "n"} and DEP.deployment_hash(sig_a) != DEP.deployment_hash(sig_b))
check("describe_drift names a changed fallback", any("fallback model changed" in d for d in DEP.describe_drift(sig_a, sig_b)))
# the LIVE signature must pass the fallback identity in (the pure builder accepting it is not
# enough): on the AST, `deployment_signature` hands `build_deployment_signature` a call to
# `_llm_identity(..., key="llmFallbackConfigurationId")`.
_dep_tree = ast.parse(src("src/trusted_data_agent/core/trust/deployment.py"))
_ds = next(n for n in ast.walk(_dep_tree) if isinstance(n, ast.FunctionDef) and n.name == "deployment_signature")
_wired = False
for n in ast.walk(_ds):
    if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "build_deployment_signature":
        for a in list(n.args) + [k.value for k in n.keywords]:
            if isinstance(a, ast.Call) and getattr(a.func, "id", "") == "_llm_identity" and any(
                    k.arg == "key" and isinstance(k.value, ast.Constant) and k.value.value == "llmFallbackConfigurationId" for k in a.keywords):
                _wired = True
check("the LIVE deployment signature resolves the fallback identity (AST: _llm_identity(key='llmFallbackConfigurationId') is passed in)", _wired)

print("[10] structural")
H_ = src("src/trusted_data_agent/llm/handler.py")
# the same three exception classes — resolved only for the SDKs already loaded
# (`lazy_sdk.loaded_attrs`: an error never imports a provider SDK just to be matched)
check("switch off ⇒ the pre-S4 loop is intact (freeze pins its behaviour)", 'except _loaded_attrs("anthropic:InternalServerError", "anthropic:RateLimitError",\n                             "openai:APIError")' in H_ and "delay = (base_delay * (2 ** attempt)) + random.uniform(0, 1)" in H_)
check("the handler classifies through the ONE policy (no private retry table)", H_.count("_res.classify(") >= 3 and "_res.should_retry(" in H_ and "_res.backoff(" in H_)
check("the fallback is the SIGNED one: resolved through resilience_envelope, never a global default", "_fallback_for_call(" in H_ and "resolve_fallback(" in H_ and "fallback_for_turn()" in H_)
check("a fallback never falls back (depth guard)", "_fallback_depth == 0" in H_ and "_fallback_depth=1" in H_)
RC_ = src("src/trusted_data_agent/llm/resilient_chat.py")
check("the LangChain envelope routes through the same policy", RC_.count("_res.classify(") >= 3 and "_res.should_continue(" in RC_)
SM = src("src/trusted_data_agent/core/session_manager.py")
check("the stamp lands at the ONE persistence chokepoint", 'setdefault("resilience"' in SM)
ES = src("src/trusted_data_agent/agent/execution_service.py")
check("armed at the execution seam, released in finally", "_renv_mod.set_turn(" in ES and "_renv_mod.reset_turn(_resilience_token)" in ES)

print(f"\n=== S4 resilience: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
