"""Session cost is a property of the SESSION — Σ every turn's cost, one definition, every reader.

Prod session 856afac4 (2026-09-21): the session TOKENS read the whole session (both answers of
a regenerate) while the session COST read the ADOPTED turn's frozen `session_cost_usd`
snapshot, so adopting the other answer changed the session cost. This gate pins:
  - the ONE definition (`session_manager.session_cost_total`) and its truth table
  - that every reader derives from it (reload endpoint, session GET, the engines' snapshot)
  - that the frontend session-load path prefers the served total over the last turn's snapshot

CI-pure: no server, no network, no DB.
"""
import ast, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
ROOT = Path(__file__).resolve().parent.parent

passed = failed = 0
def check(name, cond, detail=""):
    global passed, failed
    if cond: passed += 1
    else:
        failed += 1; print(f"  FAIL {name} {detail}")

from trusted_data_agent.core import session_manager as sm

def sess(turns, **extra):
    d = {"last_turn_data": {"workflow_history": turns}, "provider": "Google", "model": "gemini-3.1-flash-lite"}
    d.update(extra); return d

# ── 1. the definition ─────────────────────────────────────────────────────────────────────
t1 = {"turn": 1, "turn_cost": 0.003871, "session_cost_usd": 0.003871}
t2 = {"turn": 2, "turn_cost": 0.0055, "session_cost_usd": 0.009371, "sibling_of": 1, "isValid": False}
both = round(0.003871 + 0.0055, 6)
check("Σ every turn — a non-adopted sibling (isValid:false) still counts: it spent the money",
      round(sm.session_cost_total(sess([t1, t2])), 6) == both)
check("the total is invariant under adoption (the same two turns, either order, either flag)",
      round(sm.session_cost_total(sess([dict(t2, isValid=True), dict(t1, isValid=False)])), 6) == both)
check("a purged / toggled-out turn (the pre-S8 isValid) counts too — the token counters never subtract",
      round(sm.session_cost_total(sess([dict(t1, isValid=False)])), 6) == 0.003871)
check("never the frozen snapshot: turn 1's session_cost_usd (0.003871) is NOT what the session reads",
      round(sm.session_cost_total(sess([t1, t2])), 6) != 0.003871)
check("empty / malformed records → 0.0, never an exception",
      sm.session_cost_total({}) == 0.0 and sm.session_cost_total(sess([None, "x", {"turn": 3}])) == 0.0 and sm.session_cost_total(None) == 0.0)
check("a bad turn_cost value contributes nothing rather than raising",
      round(sm.session_cost_total(sess([t1, {"turn": 2, "turn_cost": "abc"}])), 6) == 0.003871)
check("a flow-run turn contributes its turn_cost (0 — its nodes bill their own turns), never its flow aggregate",
      round(sm.session_cost_total(sess([t1, {"turn": 2, "profile_type": "flow", "turn_cost": 0.0, "session_cost_usd": 0.42, "flow_total_cost": 0.42}])), 6) == 0.003871)

# legacy turn (no turn_cost) is priced from its tokens through the cost manager
class _CM:
    def __init__(self): self.calls = []
    def calculate_cost(self, provider, model, input_tokens, output_tokens):
        self.calls.append((provider, model, input_tokens, output_tokens)); return 0.001 * input_tokens
import trusted_data_agent.core.cost_manager as _cmm
_orig = _cmm.get_cost_manager; _fake = _CM(); _cmm.get_cost_manager = lambda: _fake
try:
    v = sm.session_cost_total(sess([t1, {"turn": 2, "turn_input_tokens": 3, "turn_output_tokens": 1, "provider": "OpenRouter", "model": "m"}]))
    check("a legacy turn without turn_cost is priced from its tokens at its OWN provider/model", round(v, 6) == round(0.003871 + 0.003, 6) and _fake.calls == [("OpenRouter", "m", 3, 1)])
    _fake.calls.clear()
    sm.session_cost_total(sess([{"turn": 1, "turn_input_tokens": 2, "turn_output_tokens": 0}]))
    check("… falling back to the session's provider/model when the turn has none", _fake.calls == [("Google", "gemini-3.1-flash-lite", 2, 0)])
    class _Boom:
        def calculate_cost(self, *a, **k): raise RuntimeError("no pricing")
    _cmm.get_cost_manager = lambda: _Boom()
    check("a legacy turn that cannot be priced contributes nothing rather than raising", round(sm.session_cost_total(sess([t1, {"turn": 2, "turn_input_tokens": 5}])), 6) == 0.003871)
finally:
    _cmm.get_cost_manager = _orig

# ── 2. every reader derives from it ───────────────────────────────────────────────────────
routes = (ROOT / "src/trusted_data_agent/api/routes.py").read_text()
def fn_src(src, name):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return ast.get_source_segment(src, n)
    return ""
reload_fn = fn_src(routes, "get_turn_data") or ""
if not reload_fn:
    # locate by its distinctive overlay line
    i = routes.index('turn_data_copy["session_input_tokens"] = session_data.get("input_tokens", 0)')
    reload_fn = routes[routes.rfind("\nasync def ", 0, i): routes.index("\n@", i) if "\n@" in routes[i:] else None]
check("reload endpoint: session_cost_usd is overlaid UNCONDITIONALLY from the one definition, beside the token overlay",
      'turn_data_copy["session_cost_usd"] = session_manager.session_cost_total(session_data)' in reload_fn
      and reload_fn.index('turn_data_copy["session_input_tokens"]') < reload_fn.index('turn_data_copy["session_cost_usd"] = session_manager.session_cost_total'))
check("reload endpoint: the legacy 'only if missing' prefix-sum is GONE (it served the frozen snapshot whenever one existed)",
      'if "session_cost_usd" not in turn_data_copy' not in routes and "for i in range(turn_id):" not in reload_fn)
check("session GET serves the total at the top level",
      re.search(r'"session_cost_usd":\s*session_manager\.session_cost_total\(session_data\)', routes) is not None)
ex = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
calc = fn_src(ex, "_calculate_session_cost_at_turn")
check("the engines' per-turn snapshot is the SAME arithmetic (delegates, keeps no private sum)",
      "session_cost_total(session_data" in calc and "for past_turn in" not in calc)

# ── 3. the frontend session-load path prefers the served total ────────────────────────────
smjs = (ROOT / "static/js/handlers/sessionManagement.js").read_text()
check("session load: the served session_cost_usd wins; the last turn's snapshot is only the fallback",
      re.search(r"const sessionCost = \(data\.session_cost_usd != null\) \? \(parseFloat\(data\.session_cost_usd\) \|\| 0\) : \(lastTurn\.session_cost_usd \|\| 0\);", smjs) is not None)
check("session load: seeds the live ledger's base so a turn run before any plan reload builds on the real session cost",
      "window.sessionCostAccumulator.sessionBaseCost = sessionCost;" in smjs)

print(f"\nSession cost total: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
