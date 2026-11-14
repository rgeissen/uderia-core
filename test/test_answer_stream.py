"""S1 — the Governed Stream. CI-pure gate: the in-flight guard agrees with the post-hoc
redaction, the stream orders and coalesces, every provider helper streams and returns the
non-streaming shape, `call_llm_api` streams through the sink and stays byte-identical
without one, the JSON-field sink extracts one field, a stop keeps the partial answer, and
every engine seam is wired (on the AST). Named assertions; every rule is mutation-tested."""
import ast, asyncio, io, json, logging, os, pathlib, random, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from trusted_data_agent.agent import answer_stream as AS
from trusted_data_agent.llm import streaming as ST
from trusted_data_agent.llm import handler as H
from trusted_data_agent.agent.guardrails.primitives import redact_pii, detect_pii

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src/trusted_data_agent"
def src(rel): return (ROOT / rel).read_text()
def chunks(text, rng, lo=1, hi=13):
    i = 0
    while i < len(text):
        n = rng.randint(lo, hi); yield text[i:i + n]; i += n

print("[1] StreamGuard — the in-flight guard agrees with the post-hoc redaction")
TEXTS = [
    "Contact Jane at jane.doe@example.com or +1 415 555 0134 regarding invoice 4471.",
    "Card 4111 1111 1111 1111 expires soon; SSN 123-45-6789 is on file. Plain tail sentence.",
    "No personal data here at all — just numbers 12, 34 and a date 2026-09-19 in prose that is long enough to release.",
    "IBAN GB82 WEST 1234 5698 7654 32 and a second email bob@corp.io end the paragraph. " * 2,
]
rng = random.Random(7)
for k, text in enumerate(TEXTS):
    for trial in range(6):
        g = AS.StreamGuard(redact_pii=True)
        out = []; halted = False
        for c in chunks(text, rng):
            r, h = g.feed(c); out.append(r); halted = halted or h
        out.append(g.flush())
        got = "".join(out)
        check(f"text {k} trial {trial}: released+flush == redact_pii(whole)", got == redact_pii(text), (got, redact_pii(text)))
        check(f"text {k} trial {trial}: never halted (no toxicity)", not halted)
# a span is never shown raw across the release boundary — released prefixes are prefix-consistent with the final
g = AS.StreamGuard(redact_pii=True); shown = ""
for c in chunks(TEXTS[0], random.Random(3)):
    r, _ = g.feed(c); shown += r
    check("released prefix never contains a raw PII value", not any(v in shown for _l, _s, _e, v in detect_pii(TEXTS[0])), shown)
shown += g.flush()
check("final released == post-hoc redaction", shown == redact_pii(TEXTS[0]))
g = AS.StreamGuard(redact_pii=True)
r, _ = g.feed("short"); check("under the lag nothing is released yet", r == "")
check("flush releases the held tail", g.flush() == "short")
g = AS.StreamGuard(redact_pii=True, allowed_values=frozenset({"jane.doe@example.com"}))
r = ""; 
for c in chunks(TEXTS[0], random.Random(1)): r += g.feed(c)[0]
r += g.flush()
check("a released (allow-listed) value streams unredacted; the rest is still masked", "jane.doe@example.com" in r and "[REDACTED:phone]" in r, r)
g = AS.StreamGuard(block_toxicity=True)
r1, h1 = g.feed("You are worthless and everyone hates you. More text follows here so the lag passes " * 2)
check("toxicity under an acting mode HALTS the stream (nothing released)", h1 and r1 == "" and g.halted, (h1, r1))
check("after a halt nothing is ever released", g.feed("more")== ("", True) and g.flush() == "")
g = AS.StreamGuard()
check("an inactive guard (observe/annotate/off) passes text verbatim, no lag", g.feed("jane@example.com now") == ("jane@example.com now", False))
check("StreamGuard.LAG covers every PII category's longest span", AS.StreamGuard.LAG >= 40)

print("[2] AnswerStream — ordered, coalesced, reset-aware")
async def _stream_basics():
    events = []
    async def emit(p, t): events.append((t, dict(p)))
    st = AS.AnswerStream(emit, "sess", flush_ms=1)
    st.turn_id = 7
    st.delta("Hel"); st.delta("lo "); st.delta("world")
    await asyncio.sleep(0.02)
    check("consecutive deltas coalesce into ONE answer_delta event", len(events) == 1 and events[0][1]["text"] == "Hello world", events)
    check("event type + identity fields", events[0][0] == AS.EVENT_TYPE and events[0][1]["session_id"] == "sess" and events[0][1]["turn_id"] == 7 and events[0][1]["message_index"] == 0)
    st.delta("A"); st.reset(); st.delta("B")
    await st.finish()
    kinds = [("reset" if p.get("reset") else p.get("text")) for _, p in events]
    check("reset lands between the withdrawn text and the new text, in order", kinds == ["Hello world", "A", "reset", "B"], kinds)
    check("message_index advanced on reset; seq strictly increasing", events[-1][1]["message_index"] == 1 and [p["seq"] for _, p in events] == [1, 2, 3, 4])
    check("raw_text/delivered_text reflect the CURRENT message only", st.raw_text == "B" and st.delivered_text == "B")
    s = st.summary()
    check("summary reports facts", s["deltas"] == 5 and s["events"] == 4 and s["messages"] == 2 and s["provider_streamed"] is True and s["guarded"] is False, s)
    st.note("provider_no_stream"); check("note(provider_no_stream) is recorded", st.summary()["provider_streamed"] is False)
    # a failing emitter never raises into the engine
    async def bad(p, t): raise RuntimeError("boom")
    st2 = AS.AnswerStream(bad, "s", flush_ms=1); st2.delta("x"); await st2.finish()
    check("an emitter failure is swallowed (a lost delta is not a lost answer)", st2.emitted == 0 and st2.delivered_text == "x")
    # guarded stream: finish releases the held tail BEFORE returning
    ev3 = []
    async def e3(p, t): ev3.append(p)
    st3 = AS.AnswerStream(e3, "s", guard=AS.StreamGuard(redact_pii=True), flush_ms=1)
    st3.delta("mail me at a@b.co please"); await st3.finish()
    check("finish() releases the guarded tail before returning (redacted)", ev3 and ev3[-1]["text"] == "mail me at [REDACTED:email] please", ev3)
    # a toxic halt withdraws the provisional message
    ev4 = []
    async def e4(p, t): ev4.append(p)
    st4 = AS.AnswerStream(e4, "s", guard=AS.StreamGuard(block_toxicity=True), flush_ms=1)
    st4.delta("Fine so far. " * 10); await asyncio.sleep(0.01); st4.delta("Everyone hates you."); await st4.finish()
    check("a toxicity halt emits a reset (the provisional text is withdrawn) and delivers nothing after", any(p.get("reset") for p in ev4) and st4.halted and st4.summary()["halted"])
asyncio.run(_stream_basics())

print("[3] provider helpers — stream, then return the non-streaming shape")
class _AUsage: input_tokens = 11; output_tokens = 7
class _AMsg:
    def __init__(self, t): self.content = [types.SimpleNamespace(text=t)]; self.usage = _AUsage()
    def dict(self): return {}
class _AStream:
    def __init__(self, parts): self.parts = parts
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    @property
    def text_stream(self):
        async def g():
            for p in self.parts: yield p
        return g()
    async def get_final_message(self): return _AMsg("".join(self.parts))
class FakeAnthropic:
    def __init__(self, parts=("Hel", "lo")):
        self.parts = list(parts); self.calls = []
        self.messages = types.SimpleNamespace(create=self._c, stream=self._s)
    async def _c(self, **kw): self.calls.append(("create", kw)); return _AMsg("".join(self.parts))
    def _s(self, **kw): self.calls.append(("stream", kw)); return _AStream(self.parts)
class _Rec:
    def __init__(self): self.d = []; self.resets = 0; self.notes = []
    def delta(self, t): self.d.append(t)
    def reset(self): self.resets += 1
    def note(self, k): self.notes.append(k)
def _oai_chunk(text=None, usage=None):
    ch = types.SimpleNamespace(choices=[], usage=usage)
    if text is not None: ch.choices = [types.SimpleNamespace(delta=types.SimpleNamespace(content=text))]
    return ch
class FakeOpenAI:
    def __init__(self, refuse_stream_options=False):
        self.calls = []; self.refuse = refuse_stream_options
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))
    async def _create(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):
            if self.refuse and "stream_options" in kw: raise ValueError("Unrecognized request argument: stream_options")
            async def g():
                yield _oai_chunk("hi "); yield _oai_chunk("there")
                if "stream_options" in kw: yield _oai_chunk(None, types.SimpleNamespace(prompt_tokens=5, completion_tokens=2))
            return g()
        r = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi there"))],
                                  usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=2)); r.dict = lambda: {}
        return r
class _GChunk:
    def __init__(self, text, last=False):
        self._t = text
        self.candidates = [types.SimpleNamespace(finish_reason=types.SimpleNamespace(name="STOP" if last else "UNKNOWN"),
                                                 content=types.SimpleNamespace(parts=[types.SimpleNamespace(text=text)]))] if text or last else []
        self.usage_metadata = types.SimpleNamespace(prompt_token_count=9, candidates_token_count=3, cached_content_token_count=0, thoughts_token_count=0) if last else None
    @property
    def text(self):
        if self._t is None: raise ValueError("no parts")
        return self._t
class FakeGoogle:
    def __init__(self): self.calls = []; self.aio = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=self._gc, generate_content_stream=self._gcs))
    async def _gc(self, **kw):
        self.calls.append("generate_content"); return _GChunk("gemini says", last=True)
    async def _gcs(self, **kw):
        self.calls.append("generate_content_stream")
        async def g():
            yield _GChunk("gemini "); yield _GChunk("says"); yield _GChunk(None, last=True)
        return g()
class FakeOllamaHTTP:
    def __init__(self): self.posts = []; self.streams = []
    async def post(self, path, json=None):
        self.posts.append(json); r = types.SimpleNamespace(); r.raise_for_status = lambda: None
        r.json = lambda: {"message": {"content": "llama"}, "prompt_eval_count": 4, "eval_count": 1}; return r
    def stream(self, method, path, json=None):
        self.streams.append(json); outer = self
        class _R:
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False
            def raise_for_status(s): pass
            async def aiter_lines(s):
                for line in ['{"message":{"content":"lla"},"done":false}', '', 'not json',
                             '{"message":{"content":"ma"},"done":false}',
                             '{"message":{"content":""},"done":true,"prompt_eval_count":4,"eval_count":1}']:
                    yield line
        return _R()
class FakeBedrock:
    def __init__(self, provider="anthropic"): self.calls = []; self.provider = provider
    def invoke_model(self, body, modelId):
        self.calls.append(("invoke_model", modelId))
        if self.provider == "anthropic": payload = {"content": [{"text": "claude on bedrock"}], "usage": {"input_tokens": 3, "output_tokens": 2}}
        else: payload = {"generation": "llama on bedrock", "prompt_token_count": 6, "generation_token_count": 4}
        return {"body": io.BytesIO(json.dumps(payload).encode())}
    def invoke_model_with_response_stream(self, body, modelId):
        self.calls.append(("stream", modelId))
        evs = [{"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
               {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "claude "}},
               {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "on bedrock"}},
               {"type": "message_delta", "usage": {"output_tokens": 2}},
               {"type": "message_stop", "amazon-bedrock-invocationMetrics": {"inputTokenCount": 3, "outputTokenCount": 2}}]
        return {"body": [{"chunk": {"bytes": json.dumps(e).encode()}} for e in evs]}

async def _helpers():
    rec = _Rec(); fa = FakeAnthropic()
    msg = await ST.anthropic_stream(fa, rec, model="m", messages=[], max_tokens=10)
    check("anthropic_stream: deltas + final Message shape", rec.d == ["Hel", "lo"] and msg.content[0].text == "Hello" and msg.usage.input_tokens == 11)
    rec = _Rec(); fo = FakeOpenAI()
    r = await ST.openai_compatible_stream(fo, rec, provider="OpenAI", model="m", messages=[])
    check("openai stream: stream=True + stream_options, deltas, synthesized completion + usage", fo.calls[0]["stream"] is True and fo.calls[0]["stream_options"] == {"include_usage": True}
          and rec.d == ["hi ", "there"] and r.choices[0].message.content == "hi there" and r.usage.prompt_tokens == 5 and ST.was_streamed(r))
    rec = _Rec(); fo = FakeOpenAI(refuse_stream_options=True); ST._NO_STREAM_OPTIONS.discard(("MLX", "m2"))
    r = await ST.openai_compatible_stream(fo, rec, provider="MLX", model="m2", messages=[])
    check("openai stream: stream_options refusal is LEARNED from the provider's error, retried without", len(fo.calls) == 2 and "stream_options" not in fo.calls[1] and r.usage is None and rec.d == ["hi ", "there"] and ("MLX", "m2") in ST._NO_STREAM_OPTIONS)
    fo2 = FakeOpenAI(refuse_stream_options=True); await ST.openai_compatible_stream(fo2, _Rec(), provider="MLX", model="m2", messages=[])
    check("…and the next call skips the doomed argument outright", len(fo2.calls) == 1 and "stream_options" not in fo2.calls[0])
    rec = _Rec(); fg = FakeGoogle()
    r = await ST.google_stream(fg, rec, model="g", contents="x", config=None)
    check("google stream: deltas, joined text, last candidates (finish_reason), usage from the chunk that carried it",
          rec.d == ["gemini ", "says"] and r.text == "gemini says" and r.candidates[0].finish_reason.name == "STOP" and r.usage_metadata.candidates_token_count == 3)
    rec = _Rec(); oc = H.OllamaClient("localhost:11434"); oc.client = FakeOllamaHTTP()
    r = await oc.chat("m", [], "sys", on_delta=rec)
    check("ollama stream: NDJSON deltas (blank/non-JSON lines ignored), same dict shape, stream:true on the wire",
          rec.d == ["lla", "ma"] and r["message"]["content"] == "llama" and r["prompt_eval_count"] == 4 and r["eval_count"] == 1 and oc.client.streams[0]["stream"] is True and not oc.client.posts)
    rec = _Rec(); fb = FakeBedrock()
    r = await ST.bedrock_anthropic_stream(fb, rec, body="{}", modelId="anthropic.claude")
    await asyncio.sleep(0)   # deltas were marshalled to the loop thread
    body = json.loads(r.get("body").read())
    check("bedrock anthropic stream: deltas marshalled to the loop, body shape == invoke_model's", rec.d == ["claude ", "on bedrock"] and body["content"][0]["text"] == "claude on bedrock" and {k: body["usage"][k] for k in ("input_tokens", "output_tokens")} == {"input_tokens": 3, "output_tokens": 2}
          and body["usage"].get("cache_read_input_tokens") == 0 and ST.was_streamed(r))
asyncio.run(_helpers())

print("[4] call_llm_api — streams through the sink; without one, the freeze holds")
DEPS = {"STATE": {}}
async def _call(inst, prov, sink=None, model="m"):
    return await H.call_llm_api(inst, "hi", user_uuid="u", session_id="s", current_provider=prov, current_model=model, dependencies=DEPS, disabled_history=True, on_delta=sink)
async def _handler():
    rec = _Rec(); fa = FakeAnthropic()
    out = await _call(fa, "Anthropic", rec)
    check("Anthropic: with a sink → messages.stream, same 5-tuple, sink saw every byte", fa.calls[0][0] == "stream" and out[:3] == ("Hello", 11, 7) and "".join(rec.d) == "Hello" and not rec.notes)
    check("Anthropic: stream() got the same kwargs create() gets", set(fa.calls[0][1]) == {"model", "system", "messages", "max_tokens", "timeout"})
    rec = _Rec(); fo = FakeOpenAI()
    out = await _call(fo, "OpenRouter", rec)
    check("OpenAI-compatible: with a sink → stream=True, same 5-tuple", fo.calls[0].get("stream") is True and out[:3] == ("hi there", 5, 2) and "".join(rec.d) == "hi there")
    rec = _Rec(); fg = FakeGoogle()
    out = await _call(fg, "Google", rec, model="gemini-3.1-flash-lite")
    check("Google: with a sink → generate_content_stream, same 5-tuple", fg.calls == ["generate_content_stream"] and out[:3] == ("gemini says", 9, 3) and "".join(rec.d) == "gemini says")
    rec = _Rec(); oc = H.OllamaClient("localhost:11434"); oc.client = FakeOllamaHTTP()
    out = await _call(oc, "Ollama", rec, model="llama3")
    check("Ollama: with a sink → streamed, same 5-tuple", out[:3] == ("llama", 4, 1) and "".join(rec.d) == "llama" and oc.client.streams)
    # honest degradation: a Bedrock family without a text stream hands the sink the whole text once
    rec = _Rec(); fb = FakeBedrock(provider="meta")
    out = await H.call_llm_api(fb, "hi", user_uuid="u", session_id="s", current_provider="Amazon", current_model="meta.llama3-70b", dependencies=DEPS, disabled_history=True, on_delta=rec)
    check("Bedrock (meta): no stream API → non-streaming call, ONE whole-text delta, note(provider_no_stream)",
          fb.calls == [("invoke_model", "meta.llama3-70b")] and rec.d == ["llama on bedrock"] and rec.notes == ["provider_no_stream"] and out[:3] == ("llama on bedrock", 6, 4), (fb.calls, rec.d, rec.notes, out[:3]))
    rec = _Rec(); fb = FakeBedrock(provider="anthropic")
    out = await H.call_llm_api(fb, "hi", user_uuid="u", session_id="s", current_provider="Amazon", current_model="anthropic.claude-3", dependencies=DEPS, disabled_history=True, on_delta=rec)
    await asyncio.sleep(0)
    check("Bedrock (anthropic): with a sink → invoke_model_with_response_stream, same 5-tuple", fb.calls[0][0] == "stream" and out[:3] == ("claude on bedrock", 3, 2) and "".join(rec.d) == "claude on bedrock" and not rec.notes)
    # a retry after an overload error withdraws what attempt 1 delivered
    import httpx
    from anthropic import InternalServerError
    class Flaky(FakeAnthropic):
        def __init__(self): super().__init__(("Hel", "lo")); self.n = 0
        def _s(self, **kw):
            self.n += 1
            if self.n == 1:
                class _S(_AStream):
                    async def get_final_message(s):
                        raise InternalServerError("overloaded", response=httpx.Response(500, request=httpx.Request("POST", "http://x")), body=None)
                return _S(["Par", "tial"])
            return _AStream(self.parts)
    rec = _Rec(); fl = Flaky()
    H.APP_CONFIG.LLM_API_BASE_DELAY = 0
    out = await _call(fl, "Anthropic", rec)
    check("a retry after an overload error RESETS the sink first (attempt 1's text is withdrawn)", rec.resets == 1 and out[0] == "Hello" and rec.d == ["Par", "tial", "Hel", "lo"], (rec.resets, rec.d, out[:1]))
    # a bare callable is a sink too
    got = []
    out = await _call(FakeAnthropic(), "Anthropic", got.append)
    check("a bare callable works as a delta-only sink", "".join(got) == "Hello")
    try:
        ST.as_sink(42); check("as_sink rejects a non-callable", False)
    except TypeError:
        check("as_sink rejects a non-callable", True)
asyncio.run(_handler())

print("[5] JsonFieldSink — one field out of a JSON answer, as it is written")
def _jf(chunks_):
    r = _Rec(); j = AS.JsonFieldSink(r, "direct_answer")
    for c in chunks_: j.delta(c)
    return r, j
r, j = _jf(['{"dir', 'ect_answer": "Hel', 'lo \\"wor', 'ld\\" \\u00e9', 'x", "key_metric": {"value": "1"}}'])
check("extracts the field across chunk boundaries, decodes escapes (incl. a split \\u), stops at the closing quote", "".join(r.d) == 'Hello "world" éx' and j._state == "done")
r, j = _jf(["Plain prose ", "answer."]); check("a non-JSON reply streams as prose", r.d == ["Plain prose ", "answer."])
r, j = _jf(['```json\n{"direct_answer": "In a fence"}\n```']); check("a fenced JSON reply still yields the field", "".join(r.d) == "In a fence")
r, j = _jf(['{"other": "x", "direct_answer": "Second field', ' works"}']); check("the field need not be first", "".join(r.d) == "Second field works")
r, j = _jf(['{"direct_answer": "abc\\', 'n def"}']); check("an escape split across chunks is held, then decoded", "".join(r.d) == "abc\n def")
r, j = _jf(['{"direct_answer": "x"}']); j.reset(); check("reset propagates to the inner sink and re-arms", r.resets == 1 and j._state == "seek")
out, pend, closed = AS._decode_json_string('abc\\"d"tail'); check("_decode_json_string stops at the first unescaped quote", (out, pend, closed) == ('abc"d', "", True))

print("[6] a stop keeps the partial answer (the execution seam)")
from trusted_data_agent.agent import execution_service as ES
async def _partial():
    events = []
    async def emit(p, t): events.append((t, p))
    st = AS.AnswerStream(emit, "sess", flush_ms=1); st.turn_id = 3
    st.delta("The first half of the answer arrived")
    class FakeExec:
        current_turn_number = 3
        def __init__(self): self.saved = []
        async def _save_partial_turn_data(self, status, error_message=None, error_details=None): self.saved.append(status)
    fx = FakeExec()
    await ES._keep_partial_answer(st, fx, status="cancelled", error_message="stopped", user_uuid="u", session_id="sess", user_input="q", profile={"tag": "T", "profile_type": "llm_only"}, event_handler=emit)
    fa = [p for t, p in events if t == "final_answer"]
    check("executor path: the executor's partial save ran and a partial final_answer was emitted", fx.saved == ["cancelled"] and len(fa) == 1 and fa[0]["partial"] is True and fa[0]["status"] == "cancelled" and fa[0]["final_answer_text"] == "The first half of the answer arrived" and fa[0]["turn_id"] == 3, (fx.saved, fa))
    check("the partial HTML carries the text and the stopped note", "first half" in fa[0]["final_answer"] and 'data-partial="1"' in fa[0]["final_answer"] and AS.PARTIAL_NOTE in fa[0]["final_answer"])
    check("the provisional deltas were flushed BEFORE the partial final_answer", [t for t, _ in events].index("answer_delta") < [t for t, _ in events].index("final_answer"))
    # nothing arrived → nothing emitted, nothing saved
    events.clear(); fx = FakeExec(); st0 = AS.AnswerStream(emit, "sess", flush_ms=1)
    await ES._keep_partial_answer(st0, fx, status="cancelled", error_message="", user_uuid="u", session_id="sess", user_input="q", profile={}, event_handler=emit)
    check("no delivered text → no partial turn, no final_answer", fx.saved == [] and not events)
    check("no stream (streaming off) → no-op", await ES._keep_partial_answer(None, fx, status="cancelled", error_message="", user_uuid="u", session_id="s", user_input="q", profile={}, event_handler=emit) is None and fx.saved == [])
    # genie path (no executor): persist through the stream's helper
    saved = {}
    from trusted_data_agent.core import session_manager as SM
    orig = (SM.get_session, SM.add_message_to_histories, SM.update_last_turn_data)
    async def gs(u, s): return {"last_turn_data": {"workflow_history": [{}, {}]}}
    async def amh(u, s, role, content=None, html_content=None, **kw): saved["msg"] = (role, content, html_content, kw.get("profile_tag"))
    async def ultd(u, s, turn): saved["turn"] = turn
    SM.get_session, SM.add_message_to_histories, SM.update_last_turn_data = gs, amh, ultd
    try:
        events.clear(); stg = AS.AnswerStream(emit, "sess", flush_ms=1); stg.delta("genie partial")
        await ES._keep_partial_answer(stg, None, status="cancelled", error_message="stopped", user_uuid="u", session_id="sess", user_input="q", profile={"tag": "G", "profile_type": "genie"}, event_handler=emit)
    finally:
        SM.get_session, SM.add_message_to_histories, SM.update_last_turn_data = orig
    t = saved.get("turn") or {}
    check("genie path: partial turn persisted with the text, marked partial, turn number derived (3), tokens honestly unknown",
          t.get("final_summary_text") == "genie partial" and t.get("is_partial") is True and t.get("turn") == 3 and t.get("tokens_unknown") is True and t.get("profile_type") == "genie" and saved["msg"][3] == "G", t)
    check("genie path: the assistant message carries the note in plain text + the partial HTML", AS.PARTIAL_NOTE in saved["msg"][1] and 'data-partial="1"' in saved["msg"][2])
    check("genie path: partial final_answer emitted", any(t == "final_answer" and p.get("partial") for t, p in events))
asyncio.run(_partial())

print("[7] the master switch defaults OFF and the seam arms only for a live consumer")
from trusted_data_agent.core.config import APP_CONFIG
check("APP_CONFIG.ANSWER_STREAMING exists and defaults False (env TDA_ANSWER_STREAMING)", getattr(APP_CONFIG, "ANSWER_STREAMING", None) is False or os.environ.get("TDA_ANSWER_STREAMING"))
es = src("src/trusted_data_agent/agent/execution_service.py")
check("execution_service arms the stream only when stream_answer AND the master switch", 'if stream_answer and getattr(APP_CONFIG, "ANSWER_STREAMING", False):' in es)
check("the guard is resolved from the guardrail runtime's stream_policy (one resolution for both lanes)", "_gr.stream_policy(active_profile" in es)
_fa_branch = es[es.find('if event_type == "final_answer" and _answer_stream is not None:'):es.find('await event_handler(event_data, event_type)')]
check("the stream is finished BEFORE final_answer is forwarded (inside the final_answer branch of the drive loop)", 'if event_type == "final_answer" and _answer_stream is not None:' in es and "await _answer_stream.finish()" in _fa_branch, _fa_branch[:200])
check("final_answer carries the stream summary", 'event_data.setdefault("answer_stream", _answer_stream.summary())' in es)
_i_keep = es.find('_keep_partial_answer(_answer_stream, _executor, status="cancelled"'); _i_raise = es.find("raise\n    except Exception as e:")
check("CancelledError keeps the partial answer (with the REAL stream), then re-raises", "except asyncio.CancelledError:\n" in es and _i_keep > 0 and _i_raise > _i_keep, (_i_keep, _i_raise))
check("an unhandled error keeps the partial answer too", 'status="error", error_message=str(e)' in es)
check("the ambient stream is set for the executor drive and reset in finally", "_as_mod.set_current(_answer_stream)" in es and "_as_mod.reset_current(_stream_token)" in es)
rt = src("src/trusted_data_agent/api/routes.py")
check("the UI ask_stream + invoke_prompt_stream paths arm the stream", rt.count("stream_answer=True") == 2)
rr = src("src/trusted_data_agent/api/rest_routes.py") + src("src/trusted_data_agent/core/task_scheduler.py")
check("REST/scheduler/flow callers do NOT arm it (a polled task must not carry deltas)", "stream_answer=True" not in rr)

print("[8] every engine seam is wired (AST/structural)")
ie = src("src/trusted_data_agent/agent/engines/ideate_engine.py"); fe = src("src/trusted_data_agent/agent/engines/focus_engine.py")
check("Ideate's answer call passes on_delta=executor.answer_stream", "on_delta=executor.answer_stream" in ie)
check("Focus synthesis passes on_delta=executor.answer_stream AND its tools-path agent gets the stream", "on_delta=executor.answer_stream" in fe and "answer_stream=executor.answer_stream" in fe)
ce = src("src/trusted_data_agent/agent/engines/conversation_engine.py"); ca = src("src/trusted_data_agent/agent/conversation_agent.py")
check("Conversation engine hands the stream to the ReAct agent", "answer_stream=executor.answer_stream" in ce)
check("ReAct agent: on_chat_model_stream → delta; a tool-calling message resets", 'event_kind == "on_chat_model_stream" and self.answer_stream is not None' in ca and "if has_tool_calls and self.answer_stream is not None:" in ca)
gc = src("src/trusted_data_agent/agent/genie_coordinator.py"); co = src("src/trusted_data_agent/agent/engines/coordinate_engine.py")
check("Genie coordinator: on_chat_model_stream → delta; a routing message resets; the engine finishes before final_answer", 'event_kind == "on_chat_model_stream" and self.answer_stream is not None' in gc and "if has_tool_calls and self.answer_stream is not None:" in gc and "await answer_stream.finish()" in co and "answer_stream=answer_stream" in co)
ad = src("src/trusted_data_agent/mcp_adapter/adapter.py")
check("Optimize final report streams its direct_answer through JsonFieldSink on the ambient stream", '_as.JsonFieldSink(_turn_stream, "direct_answer")' in ad and "on_delta=_report_sink" in ad)
ex = src("src/trusted_data_agent/agent/executor.py")
check("executor: wrapper forwards on_delta; only depth-0 holds a stream; turn id stamped; partial save is idempotent + keeps the text",
      "on_delta=on_delta," in ex and "self.answer_stream = answer_stream if execution_depth == 0 else None" in ex and "self.answer_stream.turn_id = self.current_turn_number" in ex and "if self._partial_turn_saved:\n            return" in ex and '"partial_answer": bool(_partial_text)' in ex)
hd = src("src/trusted_data_agent/llm/handler.py")
check("handler: every provider branch is sink-aware; no branch reaches a stream API without a sink",
      hd.count("_streaming.anthropic_stream(") == 1 and hd.count("_streaming.openai_compatible_stream(") == 1 and hd.count("_streaming.bedrock_anthropic_stream(") == 1 and "_streaming.google_stream(" in hd and "on_delta=_sink" in hd)
gr = src("src/trusted_data_agent/agent/guardrails/runtime.py")
check("stream_policy alters only under an ACTING mode (enforce/review), resolved via the same adapter", 'acting = mode == "enforce" or _raw_mode(profile, Direction.OUTBOUND) == _modes.REVIEW' in gr)
tree = ast.parse(ca)
emitted = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "_emit_event" and n.args and isinstance(n.args[0], ast.Constant)}
check("the ReAct agent emits no NEW notification type for deltas (answer_delta is its own SSE type, never a Live-Status step)", "answer_delta" not in emitted)

print("[9] stream_policy — the guard follows the outbound guardrail's own resolution")
from trusted_data_agent.agent.guardrails import runtime as GR
_prev = APP_CONFIG.GUARDRAILS_ENABLED
try:
    APP_CONFIG.GUARDRAILS_ENABLED = False
    check("master off → alters nothing", GR.stream_policy({"guardrailConfig": {"outbound": {"mode": "enforce"}}}) == {"redact_pii": False, "block_toxicity": False, "allowed_values": frozenset()})
    APP_CONFIG.GUARDRAILS_ENABLED = True
    for mode in ("observe", "annotate"):
        check(f"{mode} → alters nothing (the final answer will not be altered either)", not GR.stream_policy({"guardrailConfig": {"outbound": {"mode": mode}}})["redact_pii"])
    p = GR.stream_policy({"guardrailConfig": {"outbound": {"mode": "enforce"}}})
    check("enforce → redact + block", p["redact_pii"] and p["block_toxicity"])
    p = GR.stream_policy({"guardrailConfig": {"outbound": {"mode": "review"}}})
    check("review (enforce with retention) → redact + block", p["redact_pii"] and p["block_toxicity"])
    p = GR.stream_policy({"guardrailConfig": {"outbound": {"mode": "enforce", "pii": False}}})
    check("enforce with PII checks off → no in-stream redaction", not p["redact_pii"] and p["block_toxicity"])
    check("an unresolvable profile reads as alter-nothing, never raises", GR.stream_policy(object()) ["redact_pii"] is False)
finally:
    APP_CONFIG.GUARDRAILS_ENABLED = _prev

print("[10] the stream in the FINAL answer's shape — one renderer, never two")
from trusted_data_agent.agent.formatter import OutputFormatter
_F = OutputFormatter(collected_data=[])
MD = "# Title\nFirst **bold** line.\n- one\n- two\n\n| A | B |\n|---|---|\n| 1 | 2 |\nClosing `x` sentence.\n"
h, up = AS.stream_html(MD, "markdown")
check("a complete text renders EXACTLY as the formatter renders it (the same function, never a copy)", h == _F._render_standard_markdown(MD) and up == len(MD))
_ok = True; _bad = None
for cut in range(len(MD) + 1):
    t = MD[:cut]; h, up = AS.stream_html(t, "markdown")
    if up > len(t) or (up and t[up - 1] != "\n") or h != (_F._render_standard_markdown(t[:up]) if up else ""):
        _ok = False; _bad = cut; break
check("at EVERY cut: html covers complete lines only, and is the formatter's render of exactly that prefix", _ok, _bad)
h, up = AS.stream_html("Intro\n| A | B |\n", "markdown")
check("a trailing pipe line (a table header still forming) is held back — it would jump from paragraph to table", up == len("Intro\n") and "|" not in h, (h, up))
h, up = AS.stream_html("Intro\n```sql\nSELECT 1\n", "markdown")
check("an unclosed fence is held back (its body would vanish until it closes)", up == len("Intro\n") and "SELECT" not in h, (h, up))
h, up = AS.stream_html("Intro\n```sql\nSELECT 1\n```\nAfter\n", "markdown")
check("a closed fence renders", "SELECT 1" in h and up == len("Intro\n```sql\nSELECT 1\n```\nAfter\n"))
h, up = AS.stream_html("The answer is **42** so far", "direct_answer")
check("direct_answer mode renders the Optimize sentence with the report's own function", h == _F._render_direct_answer("The answer is **42** so far") and up == len("The answer is **42** so far"))
check("an empty text renders nothing", AS.stream_html("", "markdown") == ("", 0) and AS.stream_html("  ", "direct_answer")[0] == "")

async def _html_stream():
    ev = []
    async def emit(p, t): ev.append(dict(p))
    st = AS.AnswerStream(emit, "s", flush_ms=1)
    st.delta("Line one.\nLine tw"); await st.finish()
    check("the delta carries html + html_upto for the complete lines", ev[-1].get("html") == _F._render_standard_markdown("Line one.\n") and ev[-1].get("html_upto") == len("Line one.\n"), ev[-1])
    st.delta("o grows"); await st.finish()
    check("html is NOT resent while no new line completed (same render, same upto)", "html" not in ev[-1], ev[-1])
    st.delta(".\n"); await st.finish()
    check("…and is sent again once one did", ev[-1].get("html_upto") == len("Line one.\nLine two grows.\n"))
    st.delta("gone\n"); st.reset(); st.delta("New\n"); await st.finish()
    withdrawn = [p for p in ev if p.get("text") == "gone\n"]
    check("a batch a reset withdrew carries no html (never render a withdrawn message)", withdrawn and "html" not in withdrawn[0], withdrawn)
    check("after a reset the new message's html starts from zero", ev[-1].get("html_upto") == len("New\n") and ev[-1]["message_index"] == 1, ev[-1])
    ev2 = []
    async def e2(p, t): ev2.append(dict(p))
    g = AS.AnswerStream(e2, "s", flush_ms=1, render_mode=None)
    g.delta("coordinator\ntext\n"); await g.finish()
    check("render_mode=None (genie: the browser renders its final) sends no html", all("html" not in p for p in ev2))
    ev3 = []
    async def e3(p, t): ev3.append(dict(p))
    o = AS.AnswerStream(e3, "s", flush_ms=1)
    j = AS.JsonFieldSink(o, "direct_answer")
    j.delta('{"direct_answer": "There are **100** items'); await o.finish()
    check("JsonFieldSink switches the stream to direct_answer mode — the report's sentence renders as the report renders it", o.render_mode == "direct_answer" and ev3[-1].get("html") == _F._render_direct_answer("There are **100** items"), ev3[-1])
    ev4 = []
    async def e4(p, t): ev4.append(dict(p))
    q = AS.AnswerStream(e4, "s", flush_ms=1)
    AS.JsonFieldSink(q, "direct_answer").delta("Plain prose answer.\n"); await q.finish()
    check("a report that is not JSON stays in markdown mode", q.render_mode == "markdown" and ev4[-1].get("html") == _F._render_standard_markdown("Plain prose answer.\n"))
    ev5 = []
    async def e5(p, t): ev5.append(dict(p))
    r = AS.AnswerStream(e5, "s", guard=AS.StreamGuard(redact_pii=True), flush_ms=1)
    r.delta("Mail a@b.co now.\n"); await r.finish()
    check("html renders the GUARDED text (the redaction the reader sees is the one rendered)", "a@b.co" not in ev5[-1].get("html", "") and "[REDACTED:email]" in ev5[-1].get("html", ""), ev5[-1])
asyncio.run(_html_stream())
check("execution_service: genie streams without html, every other lane in its final's shape", 'render_mode=None if active_profile_type == "genie" else "markdown"' in src("src/trusted_data_agent/agent/execution_service.py"))

print(f"\n=== S1 answer stream: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
