"""Regression guard for single-source session-token accounting.

Root rule (must hold for every IFOC profile class):
  each LLM call's tokens are persisted to the session counter EXACTLY ONCE,
  in BOTH live (SSE handler attached) and headless modes.

The LangChain-agent path (ConversationAgentExecutor, used by rag_focused +
conversation_with_tools) is the single owner of its own session persistence, and
it must NOT be gated on a live event handler (that made persistence mode-dependent
and led wrapping engines to double-persist / under-persist inconsistently).
Wrapping engines must NOT re-persist the agent's tokens.

These are AST/structural assertions so they stay robust across refactors.
"""
import ast, os

ROOT = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent")

def _src(rel): return open(os.path.join(ROOT, rel)).read()

def _guards_of_call(src, call_attr):
    """Return, for each `X.<call_attr>(...)` call, the list of enclosing `if` test source strings."""
    tree = ast.parse(src)
    lines = src.splitlines()
    results = []
    class V(ast.NodeVisitor):
        def __init__(self): self.stack = []
        def visit_If(self, node):
            self.stack.append(node.test)
            self.generic_visit(node)
            self.stack.pop()
        def visit_Call(self, node):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == call_attr:
                results.append([ast.get_source_segment(src, t) or "" for t in self.stack])
            self.generic_visit(node)
    V().visit(tree)
    return results

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name); print(("  ok  " if cond else " FAIL ") + name)

# ---- 1. conversation_agent: session persist is UNCONDITIONAL on the event handler ----
ca = _src("agent/conversation_agent.py")
persist_guards = _guards_of_call(ca, "update_token_count")
check("conversation_agent persists tokens exactly once (single call site)", len(persist_guards) == 1)
guards = persist_guards[0] if persist_guards else []
joined = " || ".join(guards)
check("agent persist is NOT gated on async_event_handler (mode-independent)",
      "async_event_handler" not in joined)
check("agent persist IS guarded by session_id + token amount",
      any("session_id" in g for g in guards) and any("input_tokens" in g or "output_tokens" in g for g in guards))

# the token_update EVENT emission must STILL be gated on the live handler (UI concern only)
def _emits_event_under_handler(src):
    tree = ast.parse(src)
    found = {"v": False}
    class V(ast.NodeVisitor):
        def __init__(self): self.h = 0
        def visit_If(self, node):
            t = ast.get_source_segment(src, node.test) or ""
            inc = "async_event_handler" in t
            if inc: self.h += 1
            self.generic_visit(node)
            if inc: self.h -= 1
        def visit_Call(self, node):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "async_event_handler" and self.h > 0:
                found["v"] = True
            self.generic_visit(node)
    V().visit(tree)
    return found["v"]
check("token_update EVENT still emitted only under a live async_event_handler",
      _emits_event_under_handler(ca))

# ---- 2. FocusEngine: agent-synthesis branch does NOT re-persist agent tokens ----
fe = _src("agent/engines/focus_engine.py")
# every update_token_count in focus must be a session-NAME persist (guarded by name-token vars)
fe_guards = _guards_of_call(fe, "update_token_count")
check("focus_engine has no stray agent-token persist (only name-gen persists remain)",
      len(fe_guards) == 2 and all(any("name_" in g for g in gs) for gs in fe_guards))

# ---- 3. ConversationEngine: still does not re-persist the agent's LangChain tokens ----
ce = _src("agent/engines/conversation_engine.py")
ce_guards = _guards_of_call(ce, "update_token_count")
# conversation_engine persists comp-LLM tokens and name-gen tokens, never the raw agent input_tokens
raw_agent_repersist = any(
    (any("comp_llm" not in g and "name_" not in g for g in gs) and gs and
     all("input_tokens" in g and "comp_llm" not in g and "name_" not in g for g in gs))
    for gs in ce_guards
)
check("conversation_engine does not re-persist raw agent LangChain tokens", not raw_agent_repersist)

# ---- 4. genie coordinator self-persists via the engine exactly once (no agent/handler self-persist) ----
gc = _src("agent/genie_coordinator.py")
check("genie coordinator does not call update_token_count itself (engine owns persist)",
      "update_token_count" not in gc)
check("genie coordinator does not route through call_llm_api (no handler double-persist)",
      "call_llm_api" not in gc)

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILURES:", FAIL); raise SystemExit(1)
