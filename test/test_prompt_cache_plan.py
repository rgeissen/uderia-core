"""The cache plan — one rule set for every provider (PROMPT_CACHE_ARCHITECTURE.md). CI-pure.

FOUND (2026-10-29, measured on the Optimize lane's captured strategic prompts, ~$0.10):
  * OpenRouter (MiMo) cached 2–15 tokens of an 8.7K identical prefix per live turn. Its
    `session_id` made routing sticky — but to whichever upstream answered first: replaying
    the live call sequence, the key pinned the agent to upstreams that served 27 cached
    tokens over nine calls, while the broker's own (cache-aware) stickiness reached one that
    served 6.9K. So OpenRouter takes no affinity key; its open-model caching is the upstream's.
  * xAI cached 128 tokens even on an exact repeat; with `x-grok-conv-id` 8,320–8,960.
  * Claude (Haiku 4.5, via OpenRouter) cached NOTHING: the one breakpoint sat on a 2.5K
    system prompt, under the model's 4,096-token minimum, and the 6.2K catalog in the user
    message was never marked. A breakpoint at the end of the stable prefix → 9,269 tokens
    cached on every call after the first.
  * OpenAI and Gemini already cached the prefix (stable content first); Gemini is
    best-effort (one run of two read nothing).
So the plan: the call site declares its stable prefix; breakpoint providers get a block at
its end; automatic providers get the agent's affinity key in their own field; a refused
field is learned. Off (the master switch / the agent's opt-out) ⇒ byte-identical requests.
"""
import asyncio, io, json, logging, os, pathlib, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from trusted_data_agent.llm import cache_policy as C
from trusted_data_agent.llm import handler as H
from trusted_data_agent.agent import prompt_cache as PC
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
ROOT = pathlib.Path(__file__).resolve().parents[1]

STABLE = "S" * 3000
PROMPT = STABLE + "\n### Context\n- Overall Goal: how many customers?"

print("\n── §1 the stable split and the template's stable lead")
check("a true, long-enough prefix splits into (stable, volatile)", C.stable_split(PROMPT, STABLE) == (STABLE, PROMPT[3000:]))
check("a declaration that is not a prefix of the prompt is ignored, never trusted", C.stable_split(PROMPT, "X" * 3000) is None)
check("a short prefix (< MIN_STABLE_CHARS) is not worth a block", C.stable_split(PROMPT, STABLE[:500]) is None)
check("the whole prompt, or nothing, is not a split", C.stable_split(STABLE, STABLE) is None and C.stable_split(PROMPT, None) is None and C.stable_split(None, STABLE) is None)
TPL = "{skills}\n### Directives\n" + "D" * 2500 + "\n### Capabilities\n{tools}\n{prompts}\n### Context\nGoal: {goal}\n{history}"
vals = {"skills": "K" * 100, "tools": "T1 T2", "prompts": "P1", "goal": "how many?", "history": "h"}
lead = C.template_stable_prefix(TPL, vals, {"skills", "prompts", "tools"})
check("the stable lead ends at the first volatile value", lead is not None and lead.endswith("### Context\nGoal: ") and "how many" not in lead)
check("…and it is a true prefix of the real render", TPL.format(**vals).startswith(lead))
lead2 = C.template_stable_prefix(TPL, vals, {"skills", "prompts"})
check("a value not declared stable is volatile (the tool catalog of a governed surface)", lead2 is not None and lead2.endswith("### Capabilities\n") and "T1" not in lead2)
check("a value added to the template later is volatile by default", C.template_stable_prefix("{new}" + TPL, {**vals, "new": "n"}, {"skills", "prompts", "tools"}) is None)
check("a template that cannot render is simply not split", C.template_stable_prefix("{missing}", {}, set()) is None)

print("\n── §2 the breakpoint shapes")
mb = C.mark_stable_blocks(PROMPT, STABLE)
check("Anthropic shape: the stable prefix is its own cached block, the volatile rest a plain block",
      isinstance(mb, list) and mb[0] == {"type": "text", "text": STABLE, "cache_control": {"type": "ephemeral"}} and mb[1] == {"type": "text", "text": PROMPT[3000:]})
check("…1h ttl rides the block; no usable split ⇒ the prompt unchanged (a plain string)",
      C.mark_stable_blocks(PROMPT, STABLE, "1h")[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} and C.mark_stable_blocks(PROMPT, "nope") == PROMPT)
check("Nova shape: cachePoint after the stable prefix; whole marks the system prompt's end",
      C.bedrock_blocks(PROMPT, STABLE) == [{"text": STABLE}, {"cachePoint": {"type": "default"}}, {"text": PROMPT[3000:]}]
      and C.bedrock_blocks("SYS", whole=True) == [{"text": "SYS"}, {"cachePoint": {"type": "default"}}] and C.bedrock_blocks(PROMPT, None) == [{"text": PROMPT}])
cb = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 9, "system": "SYS",
      "messages": [{"role": "user", "content": "h"}, {"role": "assistant", "content": "a"}, {"role": "user", "content": PROMPT}]}
cm = C.mark_bedrock_body(cb, "us.anthropic.claude-haiku-4-5-20251001-v1:0", "SYS", STABLE)
check("Bedrock Claude: cached system block, last history message marked, question split", cm["system"][0]["cache_control"] and isinstance(cm["messages"][1]["content"], list)
      and cm["messages"][2]["content"][0]["cache_control"] and cm["messages"][0]["content"] == "h")
check("…the input body is never mutated", cb["system"] == "SYS" and cb["messages"][2]["content"] == PROMPT)
nb = {"system": [{"text": "SYS"}], "messages": [{"role": "user", "content": [{"text": PROMPT}]}], "inferenceConfig": {"maxTokens": 9}}
nm = C.mark_bedrock_body(nb, "amazon.nova-lite-v1:0", "SYS", STABLE)
check("Bedrock Nova: cachePoint after the system prompt and after the stable prefix", nm["system"][-1] == {"cachePoint": {"type": "default"}}
      and nm["messages"][0]["content"][1] == {"cachePoint": {"type": "default"}} and nm["inferenceConfig"] == {"maxTokens": 9})
check("Bedrock: a family without a prompt cache (Llama, Titan, an ARN) gets no body", C.mark_bedrock_body(nb, "meta.llama3-70b-instruct-v1:0", "SYS") is None
      and C.mark_bedrock_body(nb, "amazon.titan-text-express-v1", "SYS") is None and C.mark_bedrock_body(nb, "arn:aws:bedrock:x", "SYS") is None)
mm = {"system": [{"text": "S"}], "messages": [{"role": "user", "content": [{"text": PROMPT}, {"image": {}}]}]}
check("Bedrock Nova: a multimodal question keeps its blocks unmarked", C.mark_bedrock_body(mm, "amazon.nova-pro-v1:0", "S", STABLE)["messages"][0]["content"] == mm["messages"][0]["content"])

print("\n── §3 the affinity key")
k1, k2 = C.affinity_key("user-1", "profile-a"), C.affinity_key("user-1", "profile-b")
check("one key per (owner, agent), stable across calls, different per agent", k1 == C.affinity_key("user-1", "profile-a") and k1 != k2 and k1.startswith("uderia-"))
check("hashed: no identifier leaves the platform", "user-1" not in k1 and "profile-a" not in k1)
check("no owner or no agent ⇒ no key", C.affinity_key(None, "p") is None and C.affinity_key("u", None) is None)
check("each automatic provider takes it in its own field",
      C.affinity_fields("OpenAI", "gpt-4o-mini", "k") == {"kwargs": {"prompt_cache_key": "k"}}
      and C.affinity_fields("Azure", "dep", "k") == {"kwargs": {"prompt_cache_key": "k"}}
      and C.affinity_fields("XAI", "grok-4", "k") == {"extra_headers": {"x-grok-conv-id": "k"}})
check("OpenRouter takes NO key: its own routing is cache-aware, session_id pinned the agent to an upstream that did not cache (measured)",
      C.affinity_fields("OpenRouter", "xiaomi/mimo-v2.6-flash", "k") == {} and "OpenRouter" not in C._AFFINITY_FIELDS)
check("breakpoint, implicit and cacheless providers take no key (Claude via OpenRouter, Anthropic, Google, Friendli, Bedrock)",
      all(C.affinity_fields(p, m, "k") == {} for p, m in (("OpenRouter", "anthropic/claude-haiku-4.5"), ("Anthropic", "claude-x"),
                                                          ("Google", "gemini"), ("Friendli", "x"), ("Amazon", "amazon.nova-lite-v1:0"))))
check("a (provider, model) that refused the field gets nothing", C.affinity_fields("Azure", "dep", "k", {("Azure", "dep")}) == {})
check("the refusal is recognised by the field's own name, nothing broader",
      C.affinity_refused("Azure", "Unrecognized request argument supplied: prompt_cache_key") and not C.affinity_refused("Azure", "rate limited")
      and C.affinity_refused("XAI", "x-grok-conv-id: invalid") and not C.affinity_refused("Google", "prompt_cache_key"))
check("a Bedrock cache-marker refusal is recognised", C.cache_point_refused("ValidationException: cachePoint is not supported") and not C.cache_point_refused("ThrottlingException"))

print("\n── §4 usage and price on Bedrock")
check("read_usage Amazon/Nova: TOTAL = fresh + read + write", C.read_usage("Amazon", {"inputTokens": 100, "outputTokens": 5, "cacheReadInputTokenCount": 900, "cacheWriteInputTokenCount": 10})
      == {"input": 1010, "output": 5, "cached": 900, "written": 10})
check("read_usage Amazon/Claude: Anthropic-shaped", C.read_usage("Amazon", {"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0})
      == {"input": 1000, "output": 5, "cached": 900, "written": 0})
check("read factor: Bedrock Claude 0.10, Nova 0.25", C.read_price_factor("Amazon", "anthropic.claude-3-haiku") == 0.10 and C.read_price_factor("Amazon", "amazon.nova-lite-v1:0") == 0.25)
check("write factor: Claude 1.25× (5m) / 2× (1h), Nova 1.0×, no breakpoints 1.0×",
      C.write_price_factor("Anthropic", "claude-x") == 1.25 and C.write_price_factor("Amazon", "eu.anthropic.claude-x", "1h") == 2.0
      and C.write_price_factor("Amazon", "amazon.nova-lite-v1:0") == 1.0 and C.write_price_factor("OpenAI", "gpt") == 1.0)
base = lambda p, m, i, o: i / 1e6
check("priced_cost: a Nova write is not surcharged", abs(C.priced_cost(base, "Amazon", "amazon.nova-lite-v1:0", 10000, 0, written=9000) - 0.01) < 1e-12)
check("Nova's minimum is 1,024 tokens (not the Anthropic table)", C.min_cacheable_tokens("Amazon", "amazon.nova-lite-v1:0") == 1024)

print("\n── §5 the handler applies the plan per provider (fakes)")
class _U:
    def __init__(self, **kw): self.__dict__.update(kw)
class FakeAnthropic:
    def __init__(self): self.calls = []; self.messages = types.SimpleNamespace(create=self._c, stream=None)
    async def _c(self, **kw):
        self.calls.append(kw)
        m = types.SimpleNamespace(content=[types.SimpleNamespace(text="ok")], stop_reason="end_turn",
                                  usage=_U(input_tokens=50, output_tokens=2, cache_read_input_tokens=3000, cache_creation_input_tokens=0))
        m.dict = lambda: {}; return m
class FakeOpenAI:
    def __init__(self, refuse=None): self.calls = []; self.refuse = refuse; self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._c))
    async def _c(self, **kw):
        self.calls.append(kw)
        if self.refuse and self.refuse in json.dumps({k: v for k, v in kw.items() if k != "timeout"}, default=str):
            raise ValueError(f"Unrecognized request argument supplied: {self.refuse}")
        r = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"), finish_reason="stop")],
                                  usage=types.SimpleNamespace(prompt_tokens=3100, completion_tokens=2, prompt_tokens_details=types.SimpleNamespace(cached_tokens=3000)))
        r.dict = lambda: {}; return r
class FakeBedrock:
    def __init__(self, refuse=False): self.calls = []; self.refuse = refuse
    def invoke_model(self, body, modelId):
        self.calls.append(json.loads(body))
        if self.refuse and "cachePoint" in body:
            raise ValueError("ValidationException: cachePoint is not supported for this model")
        out = {"output": {"message": {"content": [{"text": "ok"}]}}, "stopReason": "end_turn",
               "usage": {"inputTokens": 100, "outputTokens": 2, "cacheReadInputTokenCount": 3000, "cacheWriteInputTokenCount": 0}}
        return {"body": io.BytesIO(json.dumps(out).encode())}

DEPS = {"STATE": {}}
async def _drive():
    orig_res, orig_gs, prev = H._resolve_cache_for_call, H.get_session, APP_CONFIG.PROMPT_CACHING
    async def no_session(u, s): return None
    H.get_session = no_session
    def armed(on):
        H._resolve_cache_for_call = lambda pid, uid, prov, model: {"enabled": on, "ttl": "5m", "capability": C.capability(prov, model)}
    call = lambda cli, prov, model, **kw: H.call_llm_api(cli, PROMPT, user_uuid="u-1", session_id=None, current_provider=prov, current_model=model,
                                                         active_profile_id="agent-1", dependencies=DEPS, disabled_history=True, **kw)
    key = C.affinity_key("u-1", "agent-1")
    try:
        armed(True)
        fa = FakeAnthropic(); out = await call(fa, "Anthropic", "claude-haiku-4-5", cache_prefix=STABLE)
        last = fa.calls[0]["messages"][-1]["content"]
        check("Anthropic: the question's stable prefix is a cached block", isinstance(last, list) and last[0]["text"] == STABLE and last[0]["cache_control"])
        check("Anthropic: the system prompt keeps its own breakpoint (3 of 4 used at most)", isinstance(fa.calls[0]["system"], list))
        check("Anthropic: the reading is TOTAL (fresh 50 + cached 3000)", out[1] == 3050)
        fa = FakeAnthropic(); await call(fa, "Anthropic", "claude-haiku-4-5", cache_prefix="not-a-prefix" * 300)
        check("Anthropic: a declaration that is not a prefix leaves the question a plain string", isinstance(fa.calls[0]["messages"][-1]["content"], str))
        fo = FakeOpenAI(); await call(fo, "OpenAI", "gpt-4o-mini", cache_prefix=STABLE)
        check("OpenAI: the affinity key rides prompt_cache_key; no cache_control", fo.calls[0].get("prompt_cache_key") == key and "cache_control" not in repr(fo.calls[0]))
        fo = FakeOpenAI(); await call(fo, "OpenRouter", "xiaomi/mimo-v2.6-flash", cache_prefix=STABLE)
        check("OpenRouter (open model): no affinity field — the broker's own cache-aware routing decides", "extra_body" not in fo.calls[0] and "prompt_cache_key" not in fo.calls[0])
        fo = FakeOpenAI(); await call(fo, "OpenRouter", "anthropic/claude-haiku-4.5", cache_prefix=STABLE)
        lm = fo.calls[0]["messages"][-1]["content"]
        check("Claude via OpenRouter: the stable prefix is a cached part; no session key (breakpoints decide)",
              isinstance(lm, list) and lm[0]["cache_control"] and lm[0]["text"] == STABLE and "extra_body" not in fo.calls[0])
        fo = FakeOpenAI(); await call(fo, "XAI", "grok-4-1-fast-non-reasoning")
        check("xAI: the affinity key rides the x-grok-conv-id header", fo.calls[0].get("extra_headers") == {"x-grok-conv-id": key})
        fo = FakeOpenAI(refuse="prompt_cache_key"); out = await call(fo, "Azure", "dep-learn")
        check("Azure refusing the field: retried once without it and the answer arrives", len(fo.calls) == 2 and "prompt_cache_key" not in fo.calls[1] and out[0] == "ok")
        fo2 = FakeOpenAI(); await call(fo2, "Azure", "dep-learn")
        check("…and the refusal is learned: never sent again", ("Azure", "dep-learn") in H._AFFINITY_REFUSED and "prompt_cache_key" not in fo2.calls[0])
        fo = FakeOpenAI(refuse="something_else")
        try:
            await call(fo, "OpenAI", "gpt-4o-mini", raise_on_error=True); raised = False
        except Exception:
            raised = True
        check("an unrelated provider error is not mistaken for a refusal (no silent retry)", ("OpenAI", "gpt-4o-mini") not in H._AFFINITY_REFUSED)
        fb = FakeBedrock(); out = await call(fb, "Amazon", "amazon.nova-lite-v1:0", cache_prefix=STABLE)
        body = fb.calls[0]
        check("Bedrock Nova: cachePoint after the system prompt and the stable prefix", body["system"][-1] == {"cachePoint": {"type": "default"}}
              and body["messages"][-1]["content"][1] == {"cachePoint": {"type": "default"}})
        check("Bedrock Nova: the reading is TOTAL and published", out[1] == 3100 and H.LAST_CALL_USAGE.get({}).get("cached_tokens") == 3000)
        fb = FakeBedrock(refuse=True); out = await call(fb, "Amazon", "amazon.nova-micro-v1:0", cache_prefix=STABLE)
        check("Bedrock refusing the markers: sent plain once, answer arrives, refusal learned",
              len(fb.calls) == 2 and "cachePoint" not in json.dumps(fb.calls[1]) and out[0] == "ok" and ("Amazon", "amazon.nova-micro-v1:0") in H._CACHE_POINT_REFUSED)
        fb = FakeBedrock(); await call(fb, "Amazon", "amazon.nova-micro-v1:0", cache_prefix=STABLE)
        check("…and the next call goes plain without trying", len(fb.calls) == 1 and "cachePoint" not in json.dumps(fb.calls[0]))
        # OFF ⇒ byte-identical
        armed(False)
        fa = FakeAnthropic(); await call(fa, "Anthropic", "claude-haiku-4-5", cache_prefix=STABLE)
        fo = FakeOpenAI(); await call(fo, "OpenRouter", "xiaomi/mimo-v2.6-flash", cache_prefix=STABLE)
        fx = FakeOpenAI(); await call(fx, "XAI", "grok-4")
        fb = FakeBedrock(); await call(fb, "Amazon", "amazon.nova-lite-v1:0", cache_prefix=STABLE)
        check("caching off: Anthropic plain string system + question, no cache_control",
              isinstance(fa.calls[0]["system"], str) and fa.calls[0]["messages"][-1]["content"] == PROMPT and "cache_control" not in repr(fa.calls[0]))
        check("caching off: no affinity field on any provider", "extra_body" not in fo.calls[0] and "extra_headers" not in fx.calls[0] and "prompt_cache_key" not in fo.calls[0])
        check("caching off: Bedrock body plain", "cachePoint" not in json.dumps(fb.calls[0]))
    finally:
        H._resolve_cache_for_call, H.get_session, APP_CONFIG.PROMPT_CACHING = orig_res, orig_gs, prev
asyncio.run(_drive())

print("\n── §6 the Bedrock stream keeps Claude's cache reading")
from trusted_data_agent.llm import streaming as ST
class _Sink:
    def push(self, t): pass
class FakeStreamBedrock:
    def invoke_model_with_response_stream(self, body, modelId):
        ev = [{"type": "message_start", "message": {"usage": {"input_tokens": 40, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 5}}},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
              {"type": "message_delta", "usage": {"output_tokens": 2}}]
        return {"body": [{"chunk": {"bytes": json.dumps(e).encode()}} for e in ev]}
async def _stream():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: ST.bedrock_anthropic_stream_sync(FakeStreamBedrock(), _Sink(), loop, body="{}", modelId="anthropic.claude-x"))
r = asyncio.run(_stream())
u = json.loads(r["body"].read())["usage"]
check("message_start's cache counts reach the usage the handler reads", u.get("cache_read_input_tokens") == 3000 and u.get("cache_creation_input_tokens") == 5 and u["input_tokens"] == 40)
check("…read as TOTAL", C.read_usage("Amazon", u)["input"] == 3045)

print("\n── §7 the strategic planner declares its stable prefix")
from trusted_data_agent.agent import planner as P
src = (ROOT / "src/trusted_data_agent/agent/planner.py").read_text()
check("the strategic call passes cache_prefix", "cache_prefix=planning_cache_prefix" in src and "_strategic_cache_prefix(planning_prompt, _rendered_plan_prompt, _tv)" in src)
check("the question, history, champions, knowledge and constraints are volatile", not ({"workflow_goal", "original_user_input", "turn_action_history", "rag_few_shot_examples",
      "knowledge_context", "constraints_section", "replan_instructions", "kg_schema_directive"} & P._STRATEGIC_STABLE_KEYS))
_orig_tpl = P.WORKFLOW_META_PLANNING_PROMPT
try:
    P.WORKFLOW_META_PLANNING_PROMPT = "### Directives\n" + "D" * 3000 + "\n### Tools\n{available_tools}\n{available_prompts}\n### Context\n{workflow_goal}"
    tv = {"available_tools": "T1", "available_prompts": "P1", "workflow_goal": "how many?"}
    rendered = P.WORKFLOW_META_PLANNING_PROMPT.format(**tv)
    final = "SKILLS\n\n" + "CHART" + rendered
    from trusted_data_agent.agent import tool_surface as TS
    orig_cur = TS.current
    TS.current = lambda: None
    pre_all = P._strategic_cache_prefix(final, rendered, tv)
    TS.current = lambda: object()
    pre_gov = P._strategic_cache_prefix(final, rendered, tv)
    TS.current = orig_cur
    check("all tools shown: the catalog is part of the stable prefix; the question is not", pre_all is not None and final.startswith(pre_all) and "T1" in pre_all and "how many" not in pre_all)
    check("governed surface: the prefix ends before the per-turn tool catalog", pre_gov is not None and final.startswith(pre_gov) and "T1" not in pre_gov and pre_gov.startswith("SKILLS\n\nCHART"))
finally:
    P.WORKFLOW_META_PLANNING_PROMPT = _orig_tpl

print("\n── §8 the executor and both fallbacks carry the declaration")
ex = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text(); hd = (ROOT / "src/trusted_data_agent/llm/handler.py").read_text()
check("executor wrapper forwards cache_prefix to call_llm_api", "cache_prefix: str = None) -> tuple[str, int, int]:" in ex and "cache_prefix=cache_prefix," in ex)
check("both signed-fallback calls forward it (a fallback keeps the plan)", hd.count("cache_prefix=cache_prefix)") == 2)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
