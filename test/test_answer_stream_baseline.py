"""S1 baseline freeze — committed GREEN against the code BEFORE the Governed Stream landed.

The no-regression contract for the answer stream (docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md
§0.2): with streaming OFF (the default), every provider branch of `call_llm_api` calls the
provider's NON-streaming API with the same arguments and returns the same 5-tuple; the ReAct
agent emits exactly the event types it emits today; the executor's LLM wrapper returns the same
3-tuple. Every assertion here must stay green through S1 and after it. CI-pure (provider fakes)."""
import ast, asyncio, logging, os, pathlib, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from trusted_data_agent.llm import handler as H
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPS = {"STATE": {}}

print("[1] the feature switch defaults OFF (or does not exist yet)")
check("ANSWER_STREAMING is absent or False by default", not getattr(APP_CONFIG, "ANSWER_STREAMING", False), getattr(APP_CONFIG, "ANSWER_STREAMING", None))

print("[2] every provider branch takes the NON-streaming API when no delta sink is supplied")
class _AUsage: input_tokens = 11; output_tokens = 7
class _AMsg:
    def __init__(self): self.content = [types.SimpleNamespace(text="hello **world**")]; self.usage = _AUsage()
    def dict(self): return {"ok": 1}
class FakeAnthropic:
    def __init__(self): self.calls = []; self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)
    async def _create(self, **kw): self.calls.append(("create", kw)); return _AMsg()
    def _stream(self, **kw): self.calls.append(("stream", kw)); raise AssertionError("stream must not be used without a delta sink")
class FakeOpenAI:
    def __init__(self): self.calls = []; self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))
    async def _create(self, **kw):
        self.calls.append(kw)
        r = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi there"))],
                                  usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=2))
        r.dict = lambda: {"ok": 1}; return r
class FakeGoogleResp:
    text = "gemini says"
    candidates = [types.SimpleNamespace(finish_reason=types.SimpleNamespace(name="STOP"), content=types.SimpleNamespace(parts=[types.SimpleNamespace(text="gemini says")]))]
    usage_metadata = types.SimpleNamespace(prompt_token_count=9, candidates_token_count=3, cached_content_token_count=0, thoughts_token_count=0)
class FakeGoogle:
    def __init__(self): self.calls = []; self.aio = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=self._gc, generate_content_stream=self._gcs))
    async def _gc(self, **kw): self.calls.append("generate_content"); return FakeGoogleResp()
    async def _gcs(self, **kw): self.calls.append("generate_content_stream"); raise AssertionError("stream must not be used without a delta sink")
class FakeOllamaHTTP:
    def __init__(self): self.posts = []
    async def post(self, path, json=None):
        self.posts.append(json); r = types.SimpleNamespace(); r.raise_for_status = lambda: None
        r.json = lambda: {"message": {"content": "llama"}, "prompt_eval_count": 4, "eval_count": 1}; return r

async def _drive():
    fa = FakeAnthropic()
    out = await H.call_llm_api(fa, "hi", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-x", dependencies=DEPS, disabled_history=True)
    check("Anthropic: messages.create, 5-tuple (text, in, out, provider, model)", out[:3] == ("hello **world**", 11, 7) and len(out) == 5 and [c[0] for c in fa.calls] == ["create"], (out, [c[0] for c in fa.calls]))
    check("Anthropic: no `stream` argument reaches create", "stream" not in fa.calls[0][1], fa.calls[0][1].keys())
    fo = FakeOpenAI()
    out = await H.call_llm_api(fo, "hi", user_uuid="u", session_id="s", current_provider="OpenAI", current_model="gpt-x", dependencies=DEPS, disabled_history=True)
    check("OpenAI-compatible: chat.completions.create without stream=True", out[:3] == ("hi there", 5, 2) and fo.calls[0].get("stream") is None and "stream_options" not in fo.calls[0], fo.calls[0].keys())
    for prov in ("Azure", "Friendli", "OpenRouter", "XAI", "MLX"):
        fo2 = FakeOpenAI()
        out = await H.call_llm_api(fo2, "hi", user_uuid="u", session_id="s", current_provider=prov, current_model="m", dependencies=DEPS, disabled_history=True)
        check(f"{prov}: same non-streaming shape", out[:3] == ("hi there", 5, 2) and fo2.calls[0].get("stream") is None, (prov, out[:3]))
    fg = FakeGoogle()
    out = await H.call_llm_api(fg, "hi", user_uuid="u", session_id="s", current_provider="Google", current_model="gemini-3.1-flash-lite", dependencies=DEPS, disabled_history=True)
    check("Google: generate_content (never the stream variant)", out[:3] == ("gemini says", 9, 3) and fg.calls == ["generate_content"], (out[:3], fg.calls))
    oc = H.OllamaClient("localhost:11434"); oc.client = FakeOllamaHTTP()
    out = await H.call_llm_api(oc, "hi", user_uuid="u", session_id="s", current_provider="Ollama", current_model="llama3", dependencies=DEPS, disabled_history=True)
    check("Ollama: /api/chat with stream False", out[:3] == ("llama", 4, 1) and oc.client.posts[0]["stream"] is False, (out[:3], oc.client.posts))
asyncio.run(_drive())

print("[3] the ReAct agent's event vocabulary is exactly today's")
ca = (ROOT / "src/trusted_data_agent/agent/conversation_agent.py").read_text()
tree = ast.parse(ca)
emitted = sorted({n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "_emit_event"
                  and n.args and isinstance(n.args[0], ast.Constant)})
FROZEN = ["conversation_agent_complete", "conversation_agent_start", "conversation_llm_complete", "conversation_llm_step",
          "conversation_tool_completed", "conversation_tool_invoked", "conversation_tool_rewrite"]
check("the emitted event set is a SUPERSET of the frozen set (S1 may add, never remove or rename)", set(FROZEN) <= set(emitted), sorted(set(FROZEN) - set(emitted)))

print("[4] the executor's LLM wrapper contract")
ex = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
sig = ex.split("async def _call_llm_and_update_tokens(")[1].split(")")[0]
check("_call_llm_and_update_tokens returns (text, input, output) — the 3-tuple every engine unpacks", "-> tuple[str, int, int]" in ex.split("async def _call_llm_and_update_tokens(")[1][:len(sig) + 40])
check("final_answer is emitted with `final_answer` text and `turn_id` on the conversation engine",
      '"final_answer": final_html' in (ROOT / "src/trusted_data_agent/agent/engines/conversation_engine.py").read_text())

print("[5] the frontend renders the answer from final_answer through addMessage (the seam S1 must keep)")
ev = (ROOT / "static/js/eventHandlers.js").read_text()
check("processStream handles final_answer via UI.addMessage('assistant', eventData.final_answer, eventData.turn_id, …)",
      "UI.addMessage('assistant', eventData.final_answer, eventData.turn_id" in ev)

print(f"\n=== S1 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
