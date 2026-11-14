"""A DENIED component call comes back to the model as a refusal it can read — never as a
crash that ends the turn.

FOUND LIVE (trust campaign, 2026-10-27, every engine on a weak model): the Coordinate
engine died on "Error during coordination: Since response_format='content_and_artifact' a
two-tuple … is expected. Instead, generated response is of type: <class 'str'>." The
model had asked for the database-navigator component; the agent may not act
autonomously; the authority chokepoint denied it — and returned the denial as a bare
JSON STRING on a tool the manager builds with `response_format="content_and_artifact"`
(the html-artifact chokepoint, `test_component_html_artifact.py`). LangChain's contract
for that format is a (content, artifact) tuple, so the refusal raised, the ReAct graph
aborted, and the error text was delivered AS THE ANSWER with every assurance station
reading pass or na (campaign invariant I6). Same shape at the enablement deny.

A refusal is a tool RESULT. It must take the same shape as every other result on that
tool, so the model reads "denied, because …" and the turn goes on — which is also the
only way the BOUNDED station can ever report the denial.

Drives the REAL manager and a REAL StructuredTool through LangChain's own `ainvoke`,
exactly as the tool node would. CI-pure: no DB, no LLM, no server.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  {name}")
    else:
        _f += 1
        print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))


from trusted_data_agent.components import manager as M  # noqa: E402
from trusted_data_agent.components.base import ComponentRenderPayload, RenderTarget  # noqa: E402
from trusted_data_agent.core import agent_authority as AA  # noqa: E402


def _tool(enabled=True):
    mgr = M.get_component_manager()
    comp = mgr.components.get("knowledge_graph")
    check("the real manager loads the knowledge_graph component", comp is not None)

    async def fake_process(args, ctx):
        return ComponentRenderPayload(component_id="knowledge_graph", render_target=RenderTarget.INLINE,
                                      html="<p>card</p>", spec={}, metadata={"tool_name": "TDA_KnowledgeGraph"})
    comp.handler.process = fake_process
    # advertise with the component ON (so the tool is built); the enablement test below
    # flips the profile the effect-site guard reads
    profile = {"profile_type": "llm_only", "componentConfig": {"knowledge_graph": {"enabled": True}}}
    tools = mgr.get_langchain_tools(profile, {"session_id": "s", "user_uuid": None, "profile_id": None})
    kg = next((t for t in tools if t.name == "TDA_KnowledgeGraph"), None)
    check("the KG tool is built with the artifact contract", kg is not None and
          getattr(kg, "response_format", None) == "content_and_artifact")
    return kg


CALL = {"name": "TDA_KnowledgeGraph", "args": {"action": "query", "query_text": "x"},
        "type": "tool_call", "id": "c1"}


def t_authority_denial_is_a_result():
    print("\n§1 an authority denial comes back as a (content, artifact) result")
    kg = _tool()
    if kg is None:
        return
    real = AA.enforce_tool

    def deny(tool_name, **kw):
        return False, "this agent is not permitted to act autonomously"
    AA.enforce_tool = deny
    try:
        try:
            msg = asyncio.run(kg.ainvoke(CALL))
            raised = None
        except Exception as e:  # noqa: BLE001
            msg, raised = None, e
    finally:
        AA.enforce_tool = real
    check("the denied call does not raise (a str on a content_and_artifact tool raised ValueError)",
          raised is None, repr(raised)[:160])
    if raised is not None:
        return
    content = getattr(msg, "content", msg)
    try:
        body = json.loads(content if isinstance(content, str) else json.dumps(content))
    except Exception:  # noqa: BLE001
        body = {}
    check("the model reads a refusal, not a crash", body.get("status") == "error" and
          ("denied" in json.dumps(body).lower() or "not permitted" in json.dumps(body).lower()),
          str(content)[:160])
    check("the refusal names the reason", "autonomously" in json.dumps(body), str(body)[:160])
    check("the artifact half is empty (nothing to render for a refusal)",
          getattr(msg, "artifact", None) in (None, {}, ""), repr(getattr(msg, "artifact", None))[:80])


def t_enablement_denial_is_a_result():
    print("\n§2 an enablement denial comes back as a (content, artifact) result")
    kg = _tool()
    if kg is None:
        return
    from trusted_data_agent.components import enablement as EN
    real = EN.enforce_component_call

    def deny(comp, pcfg, uid):
        return "component 'knowledge_graph' is not enabled for this agent"
    EN.enforce_component_call = deny
    try:
        try:
            msg = asyncio.run(kg.ainvoke(CALL))
            raised = None
        except Exception as e:  # noqa: BLE001
            msg, raised = None, e
    finally:
        EN.enforce_component_call = real
    check("the enablement-denied call does not raise", raised is None, repr(raised)[:160])
    if raised is not None:
        return
    content = getattr(msg, "content", msg)
    body = json.loads(content) if isinstance(content, str) else {}
    check("the model reads `component_not_enabled`", body.get("denied_reason") == "component_not_enabled",
          str(content)[:160])


def t_every_return_in_the_wrapper_is_a_pair():
    """The structural rule, so the next early-return cannot regress it: inside
    `_run_component` every `return` hands back a 2-tuple."""
    print("\n§3 every return inside _run_component is a (content, artifact) pair")
    import ast
    import inspect
    src = inspect.getsource(M)
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_component":
            fn = node
            break
    check("_run_component found", fn is not None)
    if fn is None:
        return
    bad = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            ok = isinstance(v, ast.Tuple) and len(v.elts) == 2
            if not ok:
                bad.append(f"line {node.lineno}: {ast.unparse(v)[:60]}")
    check("no bare return in _run_component", not bad, "; ".join(bad))


if __name__ == "__main__":
    t_authority_denial_is_a_result()
    t_enablement_denial_is_a_result()
    t_every_return_in_the_wrapper_is_a_pair()
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
