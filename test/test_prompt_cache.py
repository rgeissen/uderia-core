"""S2 — the Attested Cache. CI-pure gate: the pure policy (capability, breakpoints, prefix
identity, usage normalisation, the one pricing rule, the attested-prefix rule), the per-turn
accumulator and its stamp, the handler with provider fakes (breakpoints only with caching on,
usage read as TOTAL, the 1h-TTL refusal learned), the LangChain lanes' shared helpers, the
scorer, the SIGNED reading, and the editor/contract wiring. Named assertions; mutation-tested."""
import asyncio, logging, os, pathlib, sys, time, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from trusted_data_agent.llm import cache_policy as C
from trusted_data_agent.agent import prompt_cache as PC
from trusted_data_agent.llm import handler as H
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()

print("[1] the pure policy")
check("capability: Anthropic → breakpoints; Claude on OpenRouter → breakpoints; other OpenRouter → automatic; OpenAI/Azure/xAI → automatic; Google → implicit; Bedrock Claude/Nova → breakpoints; Ollama/MLX/Friendli/other Bedrock → none",
      C.capability("Anthropic") == C.BREAKPOINTS and C.capability("OpenRouter", "anthropic/claude-sonnet-4") == C.BREAKPOINTS
      and C.capability("OpenRouter", "meta-llama/llama-4-scout") == C.AUTOMATIC and C.capability("OpenAI") == C.AUTOMATIC and C.capability("Azure") == C.AUTOMATIC
      and C.capability("XAI") == C.AUTOMATIC and C.capability("Google") == C.IMPLICIT
      and C.capability("Amazon", "amazon.nova-lite-v1:0") == C.BREAKPOINTS and C.capability("Amazon", "us.anthropic.claude-haiku-4-5-20251001-v1:0") == C.BREAKPOINTS
      and all(C.capability(p) == C.NONE for p in ("Ollama", "MLX", "Friendli", "Amazon", None))
      and C.capability("Amazon", "meta.llama3-70b-instruct-v1:0") == C.NONE and C.capability("Amazon", "arn:aws:bedrock:us-east-1:1:inference-profile/x") == C.NONE)
check("resolve: master off → off whatever the profile says", C.resolve({"cacheConfig": {"enabled": True}}, False) == {"enabled": False, "ttl": "5m"})
check("resolve: master on + absent block → on at 5m (caching never changes what the agent says)", C.resolve({}, True) == {"enabled": True, "ttl": "5m"})
check("resolve: explicit opt-out honoured; ttl validated (bad → 5m); 1h kept", C.resolve({"cacheConfig": {"enabled": False}}, True)["enabled"] is False
      and C.resolve({"cacheConfig": {"ttl": "2d"}}, True)["ttl"] == "5m" and C.resolve({"cacheConfig": {"ttl": "1h"}}, True)["ttl"] == "1h")
check("resolve: a non-dict profile or block reads as defaults, never raises", C.resolve(object(), True)["enabled"] is True and C.resolve({"cacheConfig": "x"}, True)["ttl"] == "5m")
check("system_blocks: one cached text block; 1h carries the ttl, 5m does not; empty → []", C.system_blocks("S", "1h") == [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
      and C.system_blocks("S")[0]["cache_control"] == {"type": "ephemeral"} and C.system_blocks("") == [])
hist = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
marked = C.mark_last_message(hist)
check("mark_last_message: breakpoint on the LAST message only, input never mutated, empty → []",
      marked[0] == hist[0] and marked[1]["content"][0]["cache_control"] == {"type": "ephemeral"} and hist[1]["content"] == "b" and C.mark_last_message([]) == [])
blocks_hist = [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image", "source": {}}]}]
m2 = C.mark_last_message(blocks_hist)
check("mark_last_message: a blocks message gets the mark on its last TEXT block", "cache_control" in m2[0]["content"][0] and "cache_control" not in m2[0]["content"][1] and "cache_control" not in blocks_hist[0]["content"][0])
check("prefix_hash: deterministic, order-sensitive, sees through blocks", C.prefix_hash("S", hist) == C.prefix_hash("S", hist) and C.prefix_hash("S", hist) != C.prefix_hash("S", hist[::-1])
      and C.prefix_hash("S", hist) == C.prefix_hash("S", marked) and C.prefix_hash("S", hist) != C.prefix_hash("T", hist))
u = C.read_usage("Anthropic", {"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50})
check("read_usage Anthropic: input is the TOTAL (fresh + cached + written)", u == {"input": 1050, "output": 5, "cached": 900, "written": 50})
u = C.read_usage("OpenAI", types.SimpleNamespace(prompt_tokens=1000, completion_tokens=5, prompt_tokens_details=types.SimpleNamespace(cached_tokens=768)))
check("read_usage OpenAI: cached from prompt_tokens_details, total already inclusive", u == {"input": 1000, "output": 5, "cached": 768, "written": 0})
check("read_usage OpenAI: no details → cached 0", C.read_usage("OpenAI", types.SimpleNamespace(prompt_tokens=10, completion_tokens=1))["cached"] == 0)
u = C.read_usage("Google", types.SimpleNamespace(prompt_token_count=2000, candidates_token_count=9, cached_content_token_count=1500))
check("read_usage Google: cached_content_token_count", u == {"input": 2000, "output": 9, "cached": 1500, "written": 0})
base = lambda p, m, i, o: i / 1e6 * 3 + o / 1e6 * 15
check("priced_cost: no reading == base", abs(C.priced_cost(base, "Anthropic", "m", 10000, 100) - base("Anthropic", "m", 10000, 100)) < 1e-12)
check("priced_cost: Anthropic cached read at 0.10 of the input price", abs(C.priced_cost(base, "Anthropic", "m", 10000, 100, cached=9000) - (0.0315 - 9000 * 3e-6 * 0.9)) < 1e-9)
check("priced_cost: Anthropic 1h write at 2× (5m at 1.25×)", abs(C.priced_cost(base, "Anthropic", "m", 10000, 100, written=9000, ttl="1h") - (0.0315 + 9000 * 3e-6)) < 1e-9
      and abs(C.priced_cost(base, "Anthropic", "m", 10000, 100, written=9000, ttl="5m") - (0.0315 + 9000 * 3e-6 * 0.25)) < 1e-9)
check("priced_cost: Google implicit read at 0.25; OpenAI at 0.50; an unlisted provider at the default factor",
      abs(C.priced_cost(base, "Google", "m", 10000, 0, cached=10000) - 0.03 * 0.25) < 1e-9 and abs(C.priced_cost(base, "OpenAI", "m", 10000, 0, cached=10000) - 0.03 * 0.5) < 1e-9
      and abs(C.priced_cost(base, "Ollama", "m", 10000, 0, cached=10000, read_factor_default=0.4) - 0.03 * 0.4) < 1e-9)
check("priced_cost: a write on a provider without breakpoints is ignored; never negative; cached capped at input",
      abs(C.priced_cost(base, "OpenAI", "m", 10000, 0, written=9000) - 0.03) < 1e-12 and C.priced_cost(base, "Anthropic", "m", 100, 0, cached=100000) >= 0.0)
check("priced_cost: a raising base falls back honestly to 0, never raises", C.priced_cost(lambda *a: 1 / 0, "Anthropic", "m", 1, 1, cached=1) == 0.0)
F = lambda **kw: C.miss_is_finding(**{"cap": C.BREAKPOINTS, "prev_hash": "h", "this_hash": "h", "cached": 0, "input_tokens": 5000, "seconds_since_prev": 10, **kw})
check("miss_is_finding: same prefix, inside TTL, above minimum, nothing cached, provider caches → FINDING", F() is True)
check("…first call (no previous) → not", F(prev_hash=None) is False)
check("…changed prefix → not", F(this_hash="h2") is False)
check("…cache read → not", F(cached=1) is False)
check("…below the provider minimum → not", F(input_tokens=500) is False)
check("…outside the TTL → not (5m: 301 s; 1h keeps it)", F(seconds_since_prev=301) is False and F(seconds_since_prev=301, ttl="1h") is True)
check("…a provider with no cache → not", F(cap=C.NONE) is False)

print("[2] the per-turn accumulator + stamp")
tc = PC.TurnCache(enabled=True, ttl="5m")
r1 = tc.record(provider="Anthropic", model="m", input_tokens=5000, output_tokens=10, cached=0, written=4000, prefix_hash="h")
r2 = tc.record(provider="Anthropic", model="m", input_tokens=5000, output_tokens=10, cached=4000, written=0, prefix_hash="h")
r3 = tc.record(provider="Anthropic", model="m", input_tokens=5000, output_tokens=10, cached=0, written=0, prefix_hash="h")
s = tc.summary()
check("record: first call writes (no finding), second hits, third misses the SAME prefix → finding", r1["finding"] is False and r2["finding"] is False and r3["finding"] is True)
check("summary: totals, share, hits, misses_found, capability, prefix hash", s["calls"] == 3 and s["input_tokens"] == 15000 and s["cached_tokens"] == 4000 and s["written_tokens"] == 4000
      and abs(s["cached_share"] - 4000 / 15000) < 1e-4 and s["hits"] == 1 and s["misses_found"] == 1 and s["capability"] == C.BREAKPOINTS and s["prefix_hash"] == "h" and s["enabled"] is True)
tc2 = PC.TurnCache(enabled=True, previous_prefix_hash="h", previous_at=__import__("time").time() - 5)
check("the previous TURN's hash seeds the rule (a miss on turn 2's first call is a finding)", tc2.record(provider="Anthropic", model="m", input_tokens=5000, cached=0, prefix_hash="h")["finding"] is True)
tc3 = PC.TurnCache(enabled=False)
tc3.record(provider="Anthropic", model="m", input_tokens=5000, cached=0, prefix_hash="h"); tc3.record(provider="Anthropic", model="m", input_tokens=5000, cached=0, prefix_hash="h")
check("caching off → the rule never fires (nothing was asked to be cached)", tc3.misses_found == 0)
check("record_call outside a turn → None; stamp_for_turn outside a turn → None", PC.record_call(provider="x", model="m", input_tokens=1) is None and PC.stamp_for_turn() is None)
tok = PC.set_turn_cache(PC.TurnCache(enabled=False))
try:
    check("stamp_for_turn: armed-off with nothing cached → None (byte-identical turn)", PC.stamp_for_turn() is None)
    PC.record_call(provider="Google", model="g", input_tokens=2000, cached=1500)
    check("…but a provider that reports a cache anyway is kept (Gemini implicit)", (PC.stamp_for_turn() or {}).get("cached_tokens") == 1500)
finally:
    PC.reset_turn_cache(tok)
check("reset releases the accumulator", PC.current() is None)

print("[3] the handler — breakpoints only with caching on; usage read as TOTAL; the 1h refusal learned")
class _Usage:
    def __init__(self, **kw): self.__dict__.update(kw)
class _AMsg:
    def __init__(self, usage): self.content = [types.SimpleNamespace(text="hello")]; self.usage = usage
    def dict(self): return {}
class FakeAnthropic:
    def __init__(self, usage=None, refuse_1h=False):
        self.calls = []; self.usage = usage or _Usage(input_tokens=100, output_tokens=7, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        self.refuse_1h = refuse_1h; self.messages = types.SimpleNamespace(create=self._c, stream=None)
    async def _c(self, **kw):
        self.calls.append(kw)
        if self.refuse_1h and '"ttl": "1h"' in __import__("json").dumps(kw.get("system")):
            raise ValueError("cache_control.ttl: 1h is not supported for this model")
        return _AMsg(self.usage)
class FakeOpenAI:
    def __init__(self): self.calls = []; self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._c))
    async def _c(self, **kw):
        self.calls.append(kw)
        r = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi"))],
                                  usage=types.SimpleNamespace(prompt_tokens=3000, completion_tokens=2, prompt_tokens_details=types.SimpleNamespace(cached_tokens=2048)))
        r.dict = lambda: {}; return r
DEPS = {"STATE": {}}
async def _handler():
    prev = APP_CONFIG.PROMPT_CACHING
    orig_gs = H.get_session
    async def fake_session(u, s): return {"chat_object": [{"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}]}
    try:
        APP_CONFIG.PROMPT_CACHING = True; H.get_session = fake_session
        tok = PC.set_turn_cache(PC.TurnCache(enabled=True, ttl="5m"))
        fa = FakeAnthropic(_Usage(input_tokens=100, output_tokens=7, cache_read_input_tokens=900, cache_creation_input_tokens=0))
        out = await H.call_llm_api(fa, "now", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-x", dependencies=DEPS, disabled_history=False)
        kw = fa.calls[0]
        check("caching on: system is ONE cached text block", isinstance(kw["system"], list) and kw["system"][0]["cache_control"] == {"type": "ephemeral"} and kw["system"][0]["type"] == "text")
        check("caching on: the LAST history message carries the breakpoint; the question does not; the first history message is plain",
              isinstance(kw["messages"][-2]["content"], list) and kw["messages"][-2]["content"][0]["cache_control"] and isinstance(kw["messages"][-1]["content"], str) and isinstance(kw["messages"][0]["content"], str))
        check("tokens: input is the TOTAL prompt (fresh 100 + cached 900)", out[1] == 1000 and out[2] == 7)
        lcu = H.LAST_CALL_USAGE.get({})
        check("LAST_CALL_USAGE carries the reading + the prefix identity", lcu.get("cached_tokens") == 900 and lcu.get("cache_capability") == C.BREAKPOINTS and lcu.get("prefix_hash") and lcu.get("cache_enabled") is True)
        check("the turn accumulator recorded the call", PC.current().calls == 1 and PC.current().cached_tokens == 900)
        # a second identical call reads from cache → a hit, no finding; a third with nothing cached → finding
        await H.call_llm_api(fa, "now", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-x", dependencies=DEPS, disabled_history=False)
        fa2 = FakeAnthropic(_Usage(input_tokens=5000, output_tokens=7, cache_read_input_tokens=0, cache_creation_input_tokens=0))
        await H.call_llm_api(fa2, "now", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-x", dependencies=DEPS, disabled_history=False)
        check("the attested-prefix rule fired on the unchanged prefix that missed", PC.current().misses_found == 1 and PC.current().hits == 2)
        PC.reset_turn_cache(tok)
        # 1h refusal learned
        tok = PC.set_turn_cache(PC.TurnCache(enabled=True, ttl="1h"))
        orig_resolve = H._resolve_cache_for_call
        H._resolve_cache_for_call = lambda *a, **k: {"enabled": True, "ttl": "1h", "capability": C.BREAKPOINTS}
        try:
            fa3 = FakeAnthropic(refuse_1h=True)
            out = await H.call_llm_api(fa3, "now", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-old", dependencies=DEPS, disabled_history=False)
            check("a provider refusing ttl=1h is retried ONCE at 5m and the answer still arrives", len(fa3.calls) == 2 and out[0] == "hello" and fa3.calls[1]["system"][0]["cache_control"] == {"type": "ephemeral"})
            check("…and the refusal is LEARNED (the next resolution reads 5m)", ("Anthropic", "claude-old") in H._NO_TTL_1H)
        finally:
            H._resolve_cache_for_call = orig_resolve; PC.reset_turn_cache(tok)
        check("the learned refusal steers the real resolver to 5m", H._resolve_cache_for_call(None, None, "Anthropic", "claude-old")["ttl"] == "5m")
        # OpenAI-compatible: automatic — no cache_control, cached read from details
        tok = PC.set_turn_cache(PC.TurnCache(enabled=True))
        fo = FakeOpenAI()
        out = await H.call_llm_api(fo, "now", user_uuid="u", session_id="s", current_provider="OpenAI", current_model="gpt-x", dependencies=DEPS, disabled_history=False)
        check("OpenAI: no cache_control in the request (automatic prefix caching), cached tokens read from the details", "cache_control" not in repr(fo.calls[0]) and out[1] == 3000 and PC.current().cached_tokens == 2048)
        PC.reset_turn_cache(tok)
        # master ON but the profile opted out → plain request
        APP_CONFIG.PROMPT_CACHING = True
        H._resolve_cache_for_call = lambda *a, **k: {"enabled": False, "ttl": "5m", "capability": C.BREAKPOINTS}
        try:
            fa4 = FakeAnthropic()
            await H.call_llm_api(fa4, "now", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-x", dependencies=DEPS, disabled_history=False)
            check("an agent opted out gets the plain request", isinstance(fa4.calls[0]["system"], str) and "cache_control" not in repr(fa4.calls[0]))
        finally:
            H._resolve_cache_for_call = orig_resolve
        # master OFF → plain (the freeze pins this too)
        APP_CONFIG.PROMPT_CACHING = False
        fa5 = FakeAnthropic()
        await H.call_llm_api(fa5, "now", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-x", dependencies=DEPS, disabled_history=False)
        check("master off → plain string system, plain history, no cache_control", isinstance(fa5.calls[0]["system"], str) and "cache_control" not in repr(fa5.calls[0]))
    finally:
        APP_CONFIG.PROMPT_CACHING = prev; H.get_session = orig_gs
asyncio.run(_handler())

print("[4] the LangChain lanes' shared helpers")
from langchain_core.messages import HumanMessage, AIMessage
prev = APP_CONFIG.PROMPT_CACHING
try:
    APP_CONFIG.PROMPT_CACHING = True
    sm, hist, ph, pol = PC.langchain_prefix("SYS", [HumanMessage(content="q1"), AIMessage(content="a1")], profile={}, provider="Anthropic", model="claude-x")
    check("langchain_prefix (Anthropic, on): system as a cached block; the last history message marked; the first plain",
          isinstance(sm.content, list) and sm.content[0]["cache_control"] and isinstance(hist[-1].content, list) and hist[-1].content[0]["cache_control"] and isinstance(hist[0].content, str) and isinstance(hist[-1], AIMessage))
    sm2, hist2, ph2, pol2 = PC.langchain_prefix("SYS", [HumanMessage(content="q1")], profile={}, provider="Google", model="gemini")
    check("langchain_prefix (Google, on): plain messages (implicit caching is order-only), capability implicit", sm2.content == "SYS" and hist2[0].content == "q1" and pol2["capability"] == C.IMPLICIT)
    check("prefix hash is the same whether or not blocks were applied", ph == C.prefix_hash("SYS", [{"role": "human", "content": "q1"}, {"role": "ai", "content": "a1"}]))
    APP_CONFIG.PROMPT_CACHING = False
    sm3, hist3, _, pol3 = PC.langchain_prefix("SYS", [HumanMessage(content="q1")], profile={}, provider="Anthropic", model="claude-x")
    check("langchain_prefix (off): the plain messages the lanes always built", sm3.content == "SYS" and hist3[0].content == "q1" and pol3["enabled"] is False)
finally:
    APP_CONFIG.PROMPT_CACHING = prev
check("langchain_usage: input_token_details.cache_read/cache_creation; total already inclusive", PC.langchain_usage({"input_tokens": 3000, "output_tokens": 5, "input_token_details": {"cache_read": 2048, "cache_creation": 0}}) == {"input": 3000, "output": 5, "cached": 2048, "written": 0}
      and PC.langchain_usage(None) == {"input": 0, "output": 0, "cached": 0, "written": 0})
from trusted_data_agent.core import cost_manager as CM
class _FakeCM:
    def calculate_cost(self, provider, model, input_tokens, output_tokens): return input_tokens / 1e6 * 3 + output_tokens / 1e6 * 15
    def calculate_cost_with_cache(self, provider, model, input_tokens, output_tokens, cached_tokens=0, cache_write_tokens=0, ttl="5m"):
        return C.priced_cost(self.calculate_cost, provider, model, input_tokens, output_tokens, cached=cached_tokens, written=cache_write_tokens, ttl=ttl)
_orig_gcm = CM.get_cost_manager; CM.get_cost_manager = lambda: _FakeCM()
try:
    check("priced(): a cache reading routes through the one rule; none → plain", abs(PC.priced("Anthropic", "m", {"input": 10000, "output": 100, "cached": 9000, "written": 0}) - (0.0315 - 9000 * 3e-6 * 0.9)) < 1e-9
          and abs(PC.priced("Anthropic", "m", {"input": 10000, "output": 100, "cached": 0, "written": 0}) - 0.0315) < 1e-12)
finally:
    CM.get_cost_manager = _orig_gcm
from trusted_data_agent.core.cost_manager import CostManager
cm = CostManager.__new__(CostManager); cm.get_model_cost = lambda p, m: (3.0, 15.0)
check("CostManager.calculate_cost_with_cache delegates to the one rule; calculate_cost untouched", abs(cm.calculate_cost_with_cache("Anthropic", "m", 10000, 100, cached_tokens=9000) - (0.0315 - 9000 * 3e-6 * 0.9)) < 1e-9 and abs(cm.calculate_cost("Anthropic", "m", 10000, 100) - 0.0315) < 1e-12)

print("[5] the scorer and the SIGNED reading")
from trusted_data_agent.eval.scorers.reliability import CacheEfficiencyScorer
from trusted_data_agent.eval.types import RunRecord, EvalCase
sc = CacheEfficiencyScorer(); case = EvalCase(id="c", query="q", profile_class="llm_only")
v = sc.score(case, RunRecord({"cache": {"capability": "breakpoints", "input_tokens": 10000, "cached_tokens": 7500, "written_tokens": 0, "misses_found": 0, "calls": 3, "hits": 2}}))
check("cache_efficiency: the cached share, informational (passed None), evidence carried", v.score == 0.75 and v.passed is None and "75%" in v.explanation and any("hits: 2" in e for e in v.evidence))
check("cache_efficiency abstains: no stamp · provider without a cache · nothing sent", sc.score(case, RunRecord({})).passed is None and sc.score(case, RunRecord({})).score is None
      and sc.score(case, RunRecord({"cache": {"capability": "none", "input_tokens": 100}})).score is None and sc.score(case, RunRecord({"cache": {"capability": "implicit", "input_tokens": 0}})).score is None)
from trusted_data_agent.eval import stages as ST
check("cache_efficiency sits in THE RUN and the partition holds", "cache_efficiency" in ST.STAGE_DIMENSIONS[ST.THE_RUN] and not ST.partition_gaps())
from trusted_data_agent.agent import assurance_points as AP
pt = next((p for p in AP.REGISTRY if p.key == "prompt_cache"), None)
check("prompt_cache is registered at SIGNED, turn_key cache, class measure, owns no dimension (cache_efficiency is the residue), post-door", pt is not None and pt.station == "signed" and pt.turn_key == "cache"
      and AP.POINT_CLASS["prompt_cache"] == "measure" and AP.POINT_DIMENSIONS["prompt_cache"] == () and "prompt_cache" in AP._POST_DOOR_POINTS and "prompt_cache" in AP.UNGRADED_REASON)
R = lambda t: AP._read_prompt_cache(t, "llm_only")
check("reading: no stamp → na", R({}).state == "na")
check("reading: provider without a cache → na naming it", R({"cache": {"capability": "none"}}).state == "na" and "no prompt cache" in R({"cache": {"capability": "none"}}).detail)
check("reading: an unchanged prefix that missed → warn, naming the cause", R({"cache": {"capability": "breakpoints", "misses_found": 1, "cached_tokens": 0, "input_tokens": 5000, "calls": 2}}).state == "warn" and "moved under the agent" in R({"cache": {"capability": "breakpoints", "misses_found": 1, "input_tokens": 5000}}).detail)
# Capability-aware (prod d9a60903): only an explicit BREAKPOINT attests a prefix. Under
# best-effort caching (Gemini implicit / OpenAI-shaped automatic) the provider decides what
# it serves and the platform placed no breakpoint — so a same-prefix miss is the provider's
# choice, NOT a prefix that moved, and the platform's own unchanged hash says so.
_prod = {"cache": {"capability": "implicit", "calls": 5, "hits": 2, "misses_found": 1, "cached_tokens": 24449, "input_tokens": 58670, "enabled": True}}
check("reading (implicit, served + a same-prefix miss) → pass, names the provider's choice, never claims the prefix moved",
      R(_prod).state == "pass" and "provider's choice" in R(_prod).detail and "24,449 of 58,670" in R(_prod).detail and "moved under the agent" not in R(_prod).detail)
for _cap in ("implicit", "automatic"):
    _t = {"cache": {"capability": _cap, "calls": 2, "misses_found": 1, "cached_tokens": 0, "input_tokens": 5000, "enabled": True}}
    check(f"reading ({_cap}, nothing served + a same-prefix miss) → na 'best-effort', not a finding, never a warn",
          R(_t).state == "na" and R(_t).word == "best-effort" and "not a finding" in R(_t).detail and "moved under the agent" not in R(_t).detail)
_mx = {"cache": {"capability": "mixed", "calls": 3, "misses_found": 1, "cached_tokens": 100, "input_tokens": 5000}}
check("reading (a turn that crossed providers) → informational, never a warn", R(_mx).state == "pass" and "mixed providers" in R(_mx).detail)
check("reading (breakpoints) still warns, and says what the UNCHANGED hash implies — something outside it moved, or the provider dropped the entry",
      R({"cache": {"capability": "breakpoints", "misses_found": 1, "input_tokens": 5000}}).state == "warn"
      and "did not change" in R({"cache": {"capability": "breakpoints", "misses_found": 1, "input_tokens": 5000}}).detail)
# One fact, every surface: the Guide's DEFINITION and the reading must agree about when a
# same-prefix miss is a finding. Stating it unconditionally in the definition while the
# reader conditions it was exactly the contradiction this fix removed.
# Asserted PER DICT, never over their union: the first version joined every dict carrying
# the key, so the derivation's wording masked a definition that had drifted back.
for _dict_name in ("MECHANISM_NOTE", "MECHANISM_DERIVATION"):
    _d = str(getattr(AP, _dict_name).get("prompt_cache") or "")
    check(f"{_dict_name} conditions the finding on an explicit breakpoint (never states it unconditionally)",
          "breakpoint" in _d and "moved under the agent" not in _d, _d[:220])
check("the policy module's own docstring states the same condition",
      "BREAKPOINTS" in src("src/trusted_data_agent/llm/cache_policy.py")[:3000] and "provider's choice" in src("src/trusted_data_agent/llm/cache_policy.py")[:3000])
check("the reader's capability vocabulary is pinned to llm.cache_policy (one vocabulary, two modules)",
      AP.CACHE_CAP_BREAKPOINTS == C.BREAKPOINTS and AP.CACHE_CAPS_BEST_EFFORT == frozenset({C.IMPLICIT, C.AUTOMATIC}))
check("reading: served from cache → pass with the share", R({"cache": {"capability": "breakpoints", "cached_tokens": 900, "input_tokens": 1000, "calls": 1, "hits": 1}}).state == "pass" and "900 of 1,000" in R({"cache": {"capability": "breakpoints", "cached_tokens": 900, "input_tokens": 1000, "calls": 1, "hits": 1}}).detail)
check("reading: armed but nothing yet → na (never a pass)", R({"cache": {"capability": "breakpoints", "enabled": True, "cached_tokens": 0, "input_tokens": 500}}).state == "na" and R({"cache": {"capability": "breakpoints", "enabled": False}}).state == "na")

print("[6] wiring — the seam, the persistence chokepoint, the lanes, the editor, the contract")
es = src("src/trusted_data_agent/agent/execution_service.py")
check("the seam arms ONE TurnCache per turn under the master switch and seeds it from the previous turn's stamp; released in finally",
      "_pc_mod.set_turn_cache(_pc_mod.TurnCache(" in es and 'getattr(APP_CONFIG, "PROMPT_CACHING", False)' in es and '_prev_cache.get("prefix_hash")' in es and "_pc_mod.reset_turn_cache(_cache_token)" in es and 'workflow_history' in es[es.index("_prev_turn =") - 200:es.index("_prev_turn =") + 300])
sm = src("src/trusted_data_agent/core/session_manager.py")
check("update_last_turn_data stamps `cache` from the accumulator via setdefault (the authority precedent)", 'turn_data.setdefault("cache", _cache_stamp)' in sm and "_pc.stamp_for_turn()" in sm)
ex = src("src/trusted_data_agent/agent/executor.py")
check("executor prices through the ONE rule and accumulates the turn's cached/written totals", "cost_manager.calculate_cost_with_cache(" in ex and "self.turn_cached_tokens = getattr(self, \"turn_cached_tokens\", 0) + _cached_tokens" in ex and '"cache_write_tokens": _cache_write_tokens' in ex)
ca = src("src/trusted_data_agent/agent/conversation_agent.py"); gc = src("src/trusted_data_agent/agent/genie_coordinator.py")
check("ReAct + genie build their prefix through langchain_prefix, price through priced(), record into the turn, and carry cached_tokens on their step events",
      all(x in ca for x in ("_pc.langchain_prefix(", "_pc.priced(", "_pc.record_call(", '"cached_tokens": cached_tokens')) and all(x in gc for x in ("_pc.langchain_prefix(", "_pc.priced(", "_pc.record_call(", '"cached_tokens": cached_tokens')))
hd = src("src/trusted_data_agent/llm/handler.py")
check("every provider branch records a reading (Google, Anthropic, OpenAI-compatible, Ollama, Amazon)", hd.count("_record_cache_reading(") >= 6)
from trusted_data_agent.core import profile_config_contract as PCC
spec = next((s_ for s_ in PCC.REGISTRY if s_.key == "cacheConfig"), None) if hasattr(PCC, "REGISTRY") else None
check("cacheConfig is a registered PORTABLE, UNSIGNED profile key", spec is not None and spec.portable and not spec.signed, spec)
idx = src("templates/index.html"); ch = src("static/js/handlers/configurationHandler.js")
check("the editor's Prompt Cache card exists (enabled toggle + ttl select) and populates/saves cacheConfig, spread-preserved",
      'id="profile-cache-enabled"' in idx and 'id="profile-cache-ttl"' in idx and "profile.cacheConfig" in ch and "profileData.cacheConfig = {" in ch and "...((profile && profile.cacheConfig) || {})" in ch)
ui = src("static/js/ui.js"); qd = src("static/js/handlers/qualityDimensions.js")
check("the step cards show the cached share only when something was cached (a warm cache is invisible)", "function _cachedTokensHtml(" in ui and "if (!(c > 0)) return '';" in ui and ui.count("_cachedTokensHtml(details)") >= 3)
check("the Guide knows cache_efficiency (label, long text, informational)", "cache_efficiency:" in qd and "'cache_efficiency'," in qd)
check("PROMPT_CACHING defaults off (env TDA_PROMPT_CACHING)", getattr(APP_CONFIG, "PROMPT_CACHING", None) is False or os.environ.get("TDA_PROMPT_CACHING"))

print("[7] the broker + the per-model minimum (found by the live OpenRouter/Haiku run)")
from trusted_data_agent.llm import cache_policy as CP
# A broker fronting an Anthropic model takes the same breakpoints the native branch places.
_m = [{"role": "system", "content": "S"}, {"role": "user", "content": "h1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q"}]
_o = CP.mark_openai_messages(_m, "5m")
check("broker marking: the system message becomes a cached text block", isinstance(_o[0]["content"], list) and _o[0]["content"][0].get("cache_control") == {"type": "ephemeral"})
check("broker marking: the LAST history message carries the breakpoint; the volatile question never does", isinstance(_o[2]["content"], list) and "cache_control" in _o[2]["content"][-1] and _o[3]["content"] == "q" and _o[1]["content"] == "h1")
check("broker marking never mutates its input; 1h ttl rides the block", _m[0]["content"] == "S" and CP.mark_openai_messages(_m, "1h")[0]["content"][0]["cache_control"].get("ttl") == "1h")
check("broker marking: no history ⇒ the system block alone; no system ⇒ the last history message", CP.mark_openai_messages([{"role": "system", "content": "S"}, {"role": "user", "content": "q"}])[1]["content"] == "q" and isinstance(CP.mark_openai_messages([{"role": "user", "content": "h"}, {"role": "user", "content": "q"}])[0]["content"], list))
check("the OpenAI-compatible branch marks under BREAKPOINTS only, and hashes the PLAIN prefix before marking",
      "mark_openai_messages(messages_for_api" in hd and hd.index("_prefix_hash = _cache_policy.prefix_hash(system_prompt, messages_for_api[1:-1], _stable_o)") < hd.index("mark_openai_messages(messages_for_api"))
check("the broker's write count is read (prompt_tokens_details.cache_write_tokens)", CP.read_usage("OpenRouter", {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 20}}) == {"input": 100, "output": 5, "cached": 60, "written": 20})
# The minimum is per MODEL on Anthropic (Haiku 4.5 = 4096; a 3K prompt is never cached, so a miss there is not a finding).
check("per-model minimum: Haiku 4.5 = 4096, Sonnet 5 = 1024, Opus 5 = 512, Opus 4.7 = 2048 (longest needle wins)",
      CP.min_cacheable_tokens("Anthropic", "claude-haiku-4-5-20251001") == 4096 and CP.min_cacheable_tokens("OpenRouter", "anthropic/claude-haiku-4.5") == 4096
      and CP.min_cacheable_tokens("Anthropic", "claude-sonnet-5") == 1024 and CP.min_cacheable_tokens("Anthropic", "claude-opus-5") == 512 and CP.min_cacheable_tokens("Anthropic", "claude-opus-4-7") == 2048)
check("an unknown Anthropic model takes the LARGEST minimum (never a false finding); other families keep 1024", CP.min_cacheable_tokens("Anthropic", "claude-future-9") == 4096 and CP.min_cacheable_tokens("Google", "gemini-3.1-flash-lite") == 1024)
check("a 3K-token unchanged Haiku prefix that missed is NOT a finding; the same on Sonnet 5 IS",
      CP.miss_is_finding(cap="breakpoints", prev_hash="a", this_hash="a", cached=0, input_tokens=3000, seconds_since_prev=5, min_tokens=CP.min_cacheable_tokens("Anthropic", "claude-haiku-4-5")) is False
      and CP.miss_is_finding(cap="breakpoints", prev_hash="a", this_hash="a", cached=0, input_tokens=3000, seconds_since_prev=5, min_tokens=CP.min_cacheable_tokens("Anthropic", "claude-sonnet-5")) is True)
_tc = PC.TurnCache(enabled=True, previous_prefix_hash="p", previous_at=time.time())
_tc.record(provider="Anthropic", model="claude-haiku-4-5", input_tokens=3000, cached=0, prefix_hash="p")
check("TurnCache consults the per-model minimum when it records", _tc.misses_found == 0 and "min_cacheable_tokens(provider, model)" in src("src/trusted_data_agent/agent/prompt_cache.py"))
check("read factor: Anthropic 0.1 (Fable 5.1 / Mythos 5.1 = 0.025), a broker fronting Anthropic 0.1, other broker models 0.5",
      CP.read_price_factor("Anthropic", "claude-haiku-4-5") == 0.10 and CP.read_price_factor("Anthropic", "claude-fable-5-1") == 0.025
      and CP.read_price_factor("OpenRouter", "anthropic/claude-haiku-4.5") == 0.10 and CP.read_price_factor("OpenRouter", "meta-llama/llama-4-scout") == 0.50)

print("[8] the stable prefix — retrieved knowledge rides the volatile side on the ReAct lane (found live: the system block missed on every turn)")
import ast as _ast
_tree = _ast.parse(ca)
_lsp = next(n for n in _ast.walk(_tree) if isinstance(n, _ast.FunctionDef) and n.name == "_load_system_prompt")
_lsp_src = _ast.get_source_segment(ca, _lsp)
check("the ReAct system prompt is built WITHOUT the per-question knowledge (no read of knowledge_context inside _load_system_prompt)",
      "knowledge_context" not in _ast.unparse(_lsp) and "KNOWLEDGE CONTEXT" not in _ast.unparse(_lsp))
check("the knowledge section is prepended to the QUESTION (after canvas/documents), worded as before", "_knowledge = self._knowledge_section()" in ca and ca.index("_knowledge = self._knowledge_section()") > ca.index("[User has uploaded documents]") and "KNOWLEDGE CONTEXT:" in ca)
_CA = ca
class _FakeAgent:
    knowledge_context = "chunk-1"
_ks = __import__("trusted_data_agent.agent.conversation_agent", fromlist=["ConversationAgentExecutor"]).ConversationAgentExecutor._knowledge_section
check("the section is empty without knowledge and carries the chunks with it", _ks(types.SimpleNamespace(knowledge_context=None)) == "" and "chunk-1" in _ks(types.SimpleNamespace(knowledge_context="chunk-1")))
check("LangChain lanes read the broker's write count from response_metadata when usage_metadata lacks it",
      PC.langchain_usage({"input_tokens": 6259, "output_tokens": 5, "input_token_details": {"cache_read": 0}}, {"token_usage": {"prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 6250}}})["written"] == 6250
      and PC.langchain_usage({"input_tokens": 10, "output_tokens": 1, "input_token_details": {"cache_read": 4, "cache_creation": 3}}, {"token_usage": {"prompt_tokens_details": {"cache_write_tokens": 99}}})["written"] == 3
      and 'getattr(output, "response_metadata", None)' in ca and 'getattr(llm_output, "response_metadata", None)' in gc)

print(f"\n=== S2 prompt cache: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
