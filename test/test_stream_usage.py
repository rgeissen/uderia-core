"""One usage per streamed call on the OpenAI-compatible LangChain lanes (llm/stream_usage.py).

The defect (prod sessions 84399ee0 vs 86ac6b28, Oct 2026): Celeris repeats its cumulative usage
on 3 of ~4 stream chunks; LangChain's chunk merge SUMS usage_metadata, so the ReAct lane recorded
22,887 input tokens for a 7,629-token prompt (3x in, 2x out) — in the trace, the turn cost, the
session totals and the consumption ledger. The chunk sequences below are the real Celeris shape
observed in the prod container.

CI-pure: no network, no provider. Run: python test/test_stream_usage.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


from langchain_core.messages import AIMessageChunk, HumanMessage  # noqa: E402
from langchain_core.outputs import ChatGenerationChunk  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from trusted_data_agent.llm import stream_usage as su  # noqa: E402

CELERIS = [  # (content, usage, finish_reason) — the observed Celeris stream
    ("", {"input_tokens": 7629, "output_tokens": 0, "total_tokens": 7629,
          "input_token_details": {}, "output_token_details": {"reasoning": 0}}, None),
    ("Two tables exist.", None, None),
    ("", {"input_tokens": 7629, "output_tokens": 51, "total_tokens": 7680,
          "input_token_details": {}, "output_token_details": {"reasoning": 2}}, None),
    ("", {"input_tokens": 7629, "output_tokens": 51, "total_tokens": 7680,
          "input_token_details": {"cache_read": 7616}, "output_token_details": {"reasoning": 2}}, "stop"),
]
COMPLIANT = [  # the OpenAI contract: usage once, on the final chunk
    ("Hello", None, None),
    (" world", None, None),
    ("", {"input_tokens": 120, "output_tokens": 2, "total_tokens": 122}, "stop"),
]


def _chunk(content, usage, finish):
    md = {"finish_reason": finish} if finish else {}
    return ChatGenerationChunk(message=AIMessageChunk(content=content, usage_metadata=usage, response_metadata=md))


def fake_cls(seq):
    class _Fake(ChatOpenAI):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            for c in seq:
                yield _chunk(*c)

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            for c in seq:
                yield _chunk(*c)
    return _Fake


def make(seq):
    return fake_cls(seq)(model="celeris-1", api_key="sk-test", base_url="http://127.0.0.1:9/v1")


async def aggregate(llm):
    agg, n = None, 0
    async for ch in llm.astream([HumanMessage(content="q")]):
        n += 1
        agg = ch if agg is None else agg + ch
    return agg, n


async def via_events(llm):
    """The ReAct lane's read: astream_events → on_chat_model_end → output.usage_metadata."""
    out = None
    async for ev in llm.astream_events([HumanMessage(content="q")], version="v2"):
        if ev["event"] == "on_chat_model_end":
            out = ev["data"]["output"]
    return out


print("\n[1] merge_usage — field-wise max, nested, pure")
a = {"input_tokens": 5, "output_tokens": 0, "input_token_details": {}}
b = {"input_tokens": 5, "output_tokens": 9, "input_token_details": {"cache_read": 3}}
m = su.merge_usage(a, b)
check("max per field", m["input_tokens"] == 5 and m["output_tokens"] == 9, m)
check("nested field an earlier chunk lacked is kept", m["input_token_details"] == {"cache_read": 3}, m)
check("inputs not mutated", a == {"input_tokens": 5, "output_tokens": 0, "input_token_details": {}})
check("None + dict = dict", su.merge_usage(None, b) == b)
check("dict + None = dict", su.merge_usage(b, None) == b)

print("\n[2] baseline: WITHOUT the fix the Celeris stream is summed (the defect, reproduced)")
raw, n_raw_celeris = asyncio.run(aggregate(make(CELERIS)))
check("unfixed aggregate reports 3x input", raw.usage_metadata["input_tokens"] == 3 * 7629, raw.usage_metadata)
check("unfixed aggregate reports 2x output", raw.usage_metadata["output_tokens"] == 2 * 51, raw.usage_metadata)

print("\n[3] with the fix: one usage per call")
llm = su.apply(make(CELERIS))
check("applied to a ChatOpenAI instance", su.is_applied(llm))
agg, n = asyncio.run(aggregate(llm))
u = agg.usage_metadata
check("input = the true prompt count", u["input_tokens"] == 7629, u)
check("output = the true completion count", u["output_tokens"] == 51, u)
check("total recomputed from parts", u["total_tokens"] == 7680, u)
check("cache_read (only on the last chunk) kept", u["input_token_details"].get("cache_read") == 7616, u)
check("reasoning not doubled", u["output_token_details"].get("reasoning") == 2, u)
check("chunk count identical to the unfixed stream (nothing added or dropped)", n == n_raw_celeris, (n, n_raw_celeris))
check("content intact", agg.content == "Two tables exist.", agg.content)
check("final chunk keeps its finish_reason", agg.response_metadata.get("finish_reason") == "stop", agg.response_metadata)

print("\n[4] the ReAct lane's actual read path (astream_events on_chat_model_end)")
out = asyncio.run(via_events(su.apply(make(CELERIS))))
check("on_chat_model_end carries the true input", out is not None and out.usage_metadata["input_tokens"] == 7629,
      getattr(out, "usage_metadata", None))
check("on_chat_model_end carries the true output", out is not None and out.usage_metadata["output_tokens"] == 51)

print("\n[5] compliant single-usage stream is unchanged (identity)")
_, n_raw_compliant = asyncio.run(aggregate(make(COMPLIANT)))
agg, n = asyncio.run(aggregate(su.apply(make(COMPLIANT))))
check("usage identical", agg.usage_metadata["input_tokens"] == 120 and agg.usage_metadata["output_tokens"] == 2, agg.usage_metadata)
check("content identical", agg.content == "Hello world")
check("chunk count identical to the unfixed stream", n == n_raw_compliant, (n, n_raw_compliant))

print("\n[6] sync stream path")
s_llm = su.apply(make(CELERIS))
s_agg = None
for ch in s_llm.stream([HumanMessage(content="q")]):
    s_agg = ch if s_agg is None else s_agg + ch
check("sync stream: true input", s_agg.usage_metadata["input_tokens"] == 7629, s_agg.usage_metadata)

print("\n[7] scope: only OpenAI-compatible models are touched")
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402
other = FakeListChatModel(responses=["x"])
cls_before = type(other)
check("non-OpenAI model returned unchanged", su.apply(other) is other and type(other) is cls_before)
check("None is tolerated", su.apply(None) is None)

print("\n[8] idempotent and composes with the resilience envelope")
llm = su.apply(make(CELERIS))
t1 = type(llm)
su.apply(llm)
check("second apply does not re-class", type(llm) is t1)
try:
    from trusted_data_agent.llm import resilient_chat as rc
    rc.wrap(llm, provider="Celeris", model="celeris-1")
    check("envelope wraps on top of the fix", rc.is_wrapped(llm) and su.is_applied(llm))
    su.apply(llm)
    check("apply after the envelope is still a no-op", rc.is_wrapped(llm))
    agg, _ = asyncio.run(aggregate(llm))
    check("enveloped stream still reports the true input", agg.usage_metadata["input_tokens"] == 7629, agg.usage_metadata)
except ImportError as e:
    check("resilient_chat importable", False, e)

print("\n[9] the adapter applies it at the one construction site")
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "llm", "langchain_adapter.py")).read()
i_apply, i_wrap = src.find("_su.apply(inst)"), src.find("_rc.wrap(inst")
check("create_langchain_llm calls stream_usage.apply", i_apply != -1)
check("...before the resilience envelope (so fallbacks + envelopes inherit it)", i_apply != -1 and i_wrap != -1 and i_apply < i_wrap)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
