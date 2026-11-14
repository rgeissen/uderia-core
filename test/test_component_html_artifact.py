"""A component's HTML is the READER's; the model's tool result is the structured half.

On every LangChain lane (Ideate + tools, Focus with component tools, the Genie
coordinator) the component tool's result string went to the model VERBATIM — spec,
metadata AND the rendered card. The Optimize lane's adapter never did that (it hands
the planner `{type, spec, metadata}`), so the model demonstrably works without the
markup; the LangChain lanes were simply paying for it. Measured on a real KG lookup
(five attributes, 50-value domains): the plain-text facts the model needs cost ~2K
tokens; the old card 2.4K more; the card-as-a-card 27K.

Now ONE chokepoint (`components.manager` builds every component tool with
`response_format="content_and_artifact"`) keeps the html on the ToolMessage ARTIFACT,
which LangChain never sends to the model, and ONE reader
(`components.utils.extract_component_payload`) re-attaches it for the renderers.

This gate drives the REAL manager and a REAL StructuredTool. CI-pure: no DB, no LLM.
"""
import sys, json, asyncio, inspect, ast
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "src"))

_p = _f = 0
def check(name, cond, detail=""):
    global _p, _f
    if cond: _p += 1; print(f"  ok  {name}")
    else: _f += 1; print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))

from trusted_data_agent.components import manager as M
from trusted_data_agent.components.utils import extract_component_payload
from trusted_data_agent.components.base import ComponentRenderPayload, RenderTarget

CARD = "<style>.kg-lookup{}</style><details class='kg-lookup'><summary>lookup</summary>" + \
       "".join(f"<div class='kg-lk-card'><span class='kg-lk-chip'>{i} = value {i}</span></div>" for i in range(60)) + "</details>"


def _kg_tool():
    """The real manager's KG tool with the handler's process() stubbed to a fixed card."""
    mgr = M.get_component_manager()
    comp = mgr.components.get("knowledge_graph")
    check("the real manager loads the knowledge_graph component", comp is not None)
    async def fake_process(args, ctx):
        return ComponentRenderPayload(component_id="knowledge_graph", render_target=RenderTarget.INLINE,
                                      html=CARD, spec={"query": args.get("query_text")},
                                      metadata={"tool_name": "TDA_KnowledgeGraph", "action": "query",
                                                "entities": [{"name": f"E{i}", "facts": f"values: {i}"} for i in range(5)]})
    comp.handler.process = fake_process
    profile = {"profile_type": "llm_only", "componentConfig": {"knowledge_graph": {"enabled": True}}}
    # no user / profile ids: enablement then reads the profile dict alone and never
    # touches the auth database (a uuid here would bootstrap a preferences row)
    tools = mgr.get_langchain_tools(profile, {"session_id": "s", "user_uuid": None, "profile_id": None})
    kg = next((t for t in tools if t.name == "TDA_KnowledgeGraph"), None)
    check("the KG tool is built", kg is not None, [t.name for t in tools])
    return kg


def t_model_never_receives_html():
    kg = _kg_tool()
    if kg is None:
        return
    check("every component tool is content_and_artifact", kg.response_format == "content_and_artifact")
    msg = asyncio.run(kg.ainvoke({"name": "TDA_KnowledgeGraph", "args": {"action": "query", "query_text": "x"},
                                  "id": "call-1", "type": "tool_call"}))
    content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
    check("the model-facing content carries the structured halves", '"spec"' in content and '"metadata"' in content)
    check("...and NOT the card", "kg-lk-card" not in content and '"html"' not in content, content[:120])
    check("the card rides the artifact", isinstance(msg.artifact, dict) and "kg-lk-card" in msg.artifact.get("html", ""))
    facts = json.loads(content)["metadata"]["entities"]
    check("the facts the model needs are intact", len(facts) == 5 and facts[0]["facts"] == "values: 0")
    # the size argument, made concrete
    check("the content is a fraction of the markup it no longer carries",
          len(content) * 4 < len(CARD), (len(content), len(CARD)))
    # the reader gets the payload it always got
    p = extract_component_payload(msg)
    check("extract_component_payload re-attaches the html for the renderers",
          p is not None and "kg-lk-card" in p.get("html", "") and p["component_id"] == "knowledge_graph")
    # a direct invocation without a tool_call_id returns a bare (content, artifact) tuple —
    # LangChain drops the artifact then; the leaked-call recovery invokes AS a tool call
    bare = asyncio.run(kg.ainvoke({"action": "query", "query_text": "x"}))
    check("a bare ainvoke(args) yields content only (why the recovery path passes a tool call)",
          isinstance(bare, str) and "kg-lk-card" not in bare)
    tup = extract_component_payload((bare, {"html": CARD}))
    check("...and a (content, artifact) tuple is read the same way", tup is not None and "kg-lk-card" in tup["html"])
    # error path: still a 2-tuple, still parseable by the old readers
    check("an older payload that inlines html is unchanged",
          extract_component_payload(json.dumps({"status": "success", "component_id": "c", "spec": {}, "html": "<b>"}))["html"] == "<b>")


def t_every_lane_reaches_the_chokepoint():
    """The LangChain lanes obtain component tools from ONE builder; the reader is ONE
    function; the recovery path invokes as a tool call."""
    src = (ROOT / "src/trusted_data_agent/components/manager.py").read_text()
    check("StructuredTool is built with content_and_artifact", 'response_format="content_and_artifact"' in src)
    fn = src[src.index("async def _run_component("):src.index("tool = StructuredTool.from_function(")]
    check("the model-facing dict has no html key", '"html": payload.html,' not in fn and 'return content, {"html": payload.html}' in fn)
    for lane in ("agent/engines/conversation_engine.py", "agent/genie_coordinator.py", "agent/engines/focus_engine.py"):
        check(f"{lane} takes its component tools from the one builder",
              "get_component_langchain_tools" in (ROOT / "src/trusted_data_agent" / lane).read_text())
    for reader in ("agent/conversation_agent.py", "agent/genie_coordinator.py"):
        check(f"{reader} reads payloads through the one reader",
              "extract_component_payload(" in (ROOT / "src/trusted_data_agent" / reader).read_text())
    ca = (ROOT / "src/trusted_data_agent/agent/conversation_agent.py").read_text()
    rec = ca[ca.index("def _recover_leaked_component_calls"):]
    check("the leaked-call recovery invokes AS a tool call (keeps the artifact)",
          '"type": "tool_call"' in rec[:4000] and ".ainvoke(args)" not in rec[:4000])
    adapter = (ROOT / "src/trusted_data_agent/mcp_adapter/adapter.py").read_text()
    check("the Optimize adapter still hands the planner {type, spec, metadata} — no html",
          'result = {"type": payload.component_id, "spec": payload.spec, "metadata": payload.metadata}' in adapter)


if __name__ == "__main__":
    print("=" * 74); print("Component html is the reader's — never the model's"); print("=" * 74)
    for t in (t_model_never_receives_html, t_every_lane_reaches_the_chokepoint):
        try: t()
        except Exception as exc:
            _f += 1; print(f"  FAIL: {t.__name__} raised {exc.__class__.__name__}: {exc}")
    print(f"\n{_p} passed, {_f} failed  ({_p+_f} assertions)")
    sys.exit(1 if _f else 0)
