"""The history an agent receives — one rule for every engine. CI-pure.

FOUND (production, 2026-10-29, demo_walkthrough's @UDERIA session 36c22ee3, Focus on Celeris):
turns 2 and 3 asked "What is the uderia prompt caching architecture?" and both answered turn 1's
"How can I find the uderia version?". The model received, on turn 2:
    [HUMAN] You are a helpful assistant.
    [HUMAN] How can I find the uderia version?
    [HUMAN] KNOWLEDGE CONTEXT … What is the uderia prompt caching architecture?
— every stored ANSWER (role `model`, Gemini's convention) had been dropped by Focus's own copy of
the history builder, so turn 1's question read as still open; turn 3 also lost turn 2's question
(every earlier message equal to the current one was removed). The ReAct engine and the
coordinator each had a correct copy. Now: ONE builder (agent/agent_history.py), used by all three,
and ONE role rule in both executors that turn history into messages.
"""
import os, pathlib, re, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)
from trusted_data_agent.agent import agent_history as AH

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
ROOT = pathlib.Path(__file__).resolve().parents[1]
src = lambda rel: (ROOT / rel).read_text()

Q1, A1 = "How can I find the uderia version?", "To find the Uderia version, check the info box …"
Q2, A2 = "What is the uderia prompt caching architecture?", "The prompt cache follows one plan …"
PRIMER = [{"role": "user", "content": "You are a helpful assistant."}, {"role": "model", "content": "Understood."}]

print("\n── §1 the production session, turn by turn")
turn2 = PRIMER + [{"role": "user", "content": Q1}, {"role": "model", "content": A1}, {"role": "user", "content": Q2}]
h2 = AH.build(turn2, Q2)
check("turn 2 receives turn 1's question AND its answer (the answer was dropped)",
      h2 == [{"role": "user", "content": Q1}, {"role": "assistant", "content": A1}], h2)
turn3 = PRIMER + [{"role": "user", "content": Q1}, {"role": "model", "content": A1},
                  {"role": "user", "content": Q2}, {"role": "model", "content": A2}, {"role": "user", "content": Q2}]
h3 = AH.build(turn3, Q2)
check("turn 3, repeating turn 2's question, keeps turn 2 — only the trailing current question is removed",
      [m["content"] for m in h3] == [Q1, A1, Q2, A2] and h3[-1]["role"] == "assistant", h3)
check("the priming pair is not conversation", all(m["content"] not in AH.PRIMING for m in h2 + h3))

print("\n── §2 the rule")
check("roles: model / ai / assistant → assistant; user → user; anything else is skipped",
      [AH.role_of({"role": r}) for r in ("model", "ai", "assistant", "user", "system", "tool")] == ["assistant"] * 3 + ["user", None, None])
check("a message the user turned off (isValid false) is skipped",
      AH.build([{"role": "user", "content": "a", "isValid": False}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "now"}], "now")
      == [{"role": "assistant", "content": "b"}])
check("Gemini's list-shaped content is joined", AH.build([{"role": "model", "content": ["x", "y"]}, {"role": "user", "content": "q"}], "q")
      == [{"role": "assistant", "content": "x y"}])
check("a trailing message that is NOT the current question stays", AH.build([{"role": "user", "content": "earlier"}], "now")
      == [{"role": "user", "content": "earlier"}])
check("whitespace does not hide the current question", AH.build([{"role": "user", "content": "a"}, {"role": "user", "content": "  now \n"}], "now")
      == [{"role": "user", "content": "a"}])
many = [{"role": "user" if i % 2 == 0 else "model", "content": f"m{i}"} for i in range(30)] + [{"role": "user", "content": "now"}]
w = AH.build(many, "now", window=4)
check("the window is the last N messages after the current question", [m["content"] for m in w] == ["m26", "m27", "m28", "m29"], w)
check("no history and window 0 are empty", AH.build(None, "q") == [] and AH.build(many, "now", window=0) == [])

print("\n── §3 one builder, one role rule — no engine keeps its own copy")
for rel in ("src/trusted_data_agent/agent/engines/conversation_engine.py",
            "src/trusted_data_agent/agent/engines/focus_engine.py",
            "src/trusted_data_agent/agent/engines/coordinate_engine.py"):
    s = src(rel)
    check(f"{rel.split('/')[-1]} builds history through agent_history.build", "_ah.build(session_data" in s or "_ah.build(session_data_for_history" in s)
    check(f"{rel.split('/')[-1]} carries no private priming set or role mapping",
          '"You are a helpful assistant."' not in s and 'msg_role == "model"' not in s)
check("Focus no longer filters every message equal to the question", ".strip() != executor.original_user_input.strip()]" not in src("src/trusted_data_agent/agent/engines/focus_engine.py"))
for rel in ("src/trusted_data_agent/agent/conversation_agent.py", "src/trusted_data_agent/agent/genie_coordinator.py"):
    s = src(rel)
    check(f"{rel.split('/')[-1]} turns history into messages through the one role rule", "_ah.role_of(msg)" in s and "_ah.text_of(msg)" in s)

print("\n── §5 Turn Summaries mode (the Alt key): no history on any lane")
check("disabled ⇒ no history, whatever the chat holds", AH.build(turn3, Q2, disabled=True) == [] and AH.build(turn3, Q2, disabled=False) == h3)
for rel, flag in (("src/trusted_data_agent/agent/engines/conversation_engine.py", 'disabled=bool(getattr(executor, "disabled_history", False))'),
                  ("src/trusted_data_agent/agent/engines/focus_engine.py", 'disabled=bool(getattr(executor, "disabled_history", False))'),
                  ("src/trusted_data_agent/agent/engines/coordinate_engine.py", "disabled=bool(disabled_history)")):
    check(f"{rel.split('/')[-1]} honours the turn's disabled_history (ReAct and Focus never did)", flag in src(rel))

print("\n── §6 Focus reads the session once, and again only after a write")
fe = src("src/trusted_data_agent/agent/engines/focus_engine.py")
check("the history is built from the session read at turn start (no second read for it)",
      "session_data_for_history" not in fe and '_ah.build(session_data.get("chat_object", [])' in fe)
check("eight reads remain, each right after a write to the session (was twelve; four followed no write)",
      fe.count("await session_manager.get_session(") == 8, fe.count("await session_manager.get_session("))

print("\n── §4 the executor turns a stored answer into an AI message")
from langchain_core.messages import AIMessage, HumanMessage
msgs = []
for m in [{"role": "user", "content": Q1}, {"role": "model", "content": A1}]:
    r = AH.role_of(m)
    msgs.append(HumanMessage(content=AH.text_of(m)) if r == "user" else AIMessage(content=AH.text_of(m)))
check("a `model` answer becomes an AIMessage (it was dropped)", isinstance(msgs[1], AIMessage) and msgs[1].content == A1)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
