"""The model's answer, with its own reasoning left out — one normaliser, four sites.

LangChain's `.content` is a plain string when the model did not reason and a LIST
of typed parts when it did — `thinking` FIRST, then `text` (measured 2026-09-09 on
gemini-3.5-flash and gemini-3.7-flash). Four sites read that value and three of
them mishandled the list:

  * conversation_agent (final answer) — correct, and the implementation this
    module was extracted from;
  * conversation_agent (Live Status preview) — sliced the LIST and reported
    `len() == 2` for a 500-word reply;
  * session_name_generator — `' '.join(str(part) …)` stringified the THINKING
    block into the session title;
  * extensions/base — passed the list on unchanged, so `decision.py` ran
    `json.loads()` on a list and raised TypeError.

That is why the LLM editor's `thinking_budget: 0` was accidentally load-bearing:
it kept thinking off, so the list shape never arose.
"""
import ast, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from trusted_data_agent.llm.response_text import text_from_content, response_text

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name} {detail}")

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The exact shape the API returns with thinking enabled.
REASONED = [{"type": "thinking", "thinking": "The user wants revenue by region. Join on customer_id."},
            {"type": "text", "text": "Here is the SQL query:\nSELECT ..."}]
PLAIN_LIST = [{"type": "text", "text": "Here is the SQL query:\nSELECT ...", "extras": {"signature": "x"}}]

def t_reasoning_never_leaks():
    out = text_from_content(REASONED)
    check("the answer survives", out == "Here is the SQL query:\nSELECT ...")
    check("the reasoning does NOT reach the caller",
          "customer_id" not in out and "The user wants" not in out, out)
    check("a length is characters, not parts", len(out) > 20)

def t_shapes():
    check("a plain string passes through", text_from_content("hello") == "hello")
    check("a single-part list (thinking off) is unwrapped",
          text_from_content(PLAIN_LIST) == "Here is the SQL query:\nSELECT ...")
    check("None is empty, never the string 'None'", text_from_content(None) == "")
    check("an empty list is empty", text_from_content([]) == "")
    check("bare string parts are kept", text_from_content(["a", "b"]) == "a\nb")
    check("an untyped mapping carrying text is kept",
          text_from_content([{"text": "kept"}]) == "kept")
    check("multiple text parts keep their breaks",
          text_from_content([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb")

def t_unknown_reasoning_names_cannot_leak():
    """Conservative by construction — a part is kept only if it is plainly text."""
    for kind in ("reasoning", "thought", "redacted_thinking", "reasoning_summary", "tool_use"):
        got = text_from_content([{"type": kind, "text": "SECRET"}, {"type": "text", "text": "answer"}])
        check(f"a '{kind}' part is dropped", got == "answer", got)

def t_response_object():
    class R:  # a LangChain AIMessage stand-in
        content = REASONED
    check("a response object is unwrapped", response_text(R()) == "Here is the SQL query:\nSELECT ...")
    check("a bare value still works", response_text("x") == "x")
    check("None is empty", response_text(None) == "")

def t_json_consumer_no_longer_breaks():
    """`decision.py` does json.loads on this value; a list raised TypeError."""
    import json
    payload = [{"type": "thinking", "thinking": "deciding"},
               {"type": "text", "text": '{"decision": "yes"}'}]
    class R: content = payload
    try:
        parsed = json.loads(response_text(R()))
        check("an extension can json.loads the result", parsed == {"decision": "yes"})
    except TypeError as e:
        check("an extension can json.loads the result", False, str(e))

# ── every site routes through the ONE normaliser ────────────────────────────
SITES = {
    "src/trusted_data_agent/agent/conversation_agent.py": 2,
    "src/trusted_data_agent/agent/session_name_generator.py": 1,
    "src/trusted_data_agent/extensions/base.py": 1,
}

def t_all_sites_delegate():
    """Each site must IMPORT from the shared module and CALL what it imported —
    not merely mention a name that happens to match."""
    for rel, expected_calls in SITES.items():
        src = (ROOT / rel).read_text()
        tree = ast.parse(src)
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module == "trusted_data_agent.llm.response_text":
                names |= {(a.asname or a.name) for a in n.names}
        check(f"{pathlib.Path(rel).name} imports from the shared module", bool(names), rel)
        calls = sum(1 for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names)
        check(f"{pathlib.Path(rel).name} calls it at all {expected_calls} of its site(s)",
              calls >= expected_calls, f"{calls} call(s), expected >= {expected_calls}")

def t_no_site_rebuilds_it():
    """A second hand-rolled extractor is the drift this module exists to end."""
    for rel in SITES:
        src = (ROOT / rel).read_text()
        # comment-stripped: prose ABOUT the old code must not satisfy the rule
        code = "\n".join(l.split("#")[0] for l in src.splitlines())
        check(f"{pathlib.Path(rel).name} keeps no private thinking-block branch",
              'block.get("type") == "thinking"' not in code
              and "' '.join(str(part)" not in code, rel)

def t_helper_is_pure():
    """No I/O, no imports of platform state — it must be safe to call anywhere."""
    tree = ast.parse((ROOT / "src/trusted_data_agent/llm/response_text.py").read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    check("the normaliser imports nothing from the platform",
          not any(str(m).startswith("trusted_data_agent") for m in imported), imported)

for fn in (t_reasoning_never_leaks, t_shapes, t_unknown_reasoning_names_cannot_leak,
           t_response_object, t_json_consumer_no_longer_breaks,
           t_all_sites_delegate, t_no_site_rebuilds_it, t_helper_is_pure):
    try: fn()
    except Exception as e:
        FAIL += 1; print(f"  FAIL {fn.__name__} raised {type(e).__name__}: {e}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
