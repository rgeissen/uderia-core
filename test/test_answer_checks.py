"""S1.1 — answer_checks: the checks that RAN on an answer, as ONE record for the live settle and the reload.

CI-pure. The summary is what every engine's `final_answer` frame carries, so that a clean check (quiet on
the live stream by design) still reaches the answer's verdict, exactly as the persisted stamp does on a reload.
"""
import ast, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trusted_data_agent.agent import answer_checks as ac  # noqa: E402

P = F = 0
def check(name, cond, detail=""):
    global P, F
    if cond: P += 1
    else: F += 1; print("  FAIL", name, detail)

# truth table
s = ac.checks_summary({"decision": "pass"}, {"ran": True, "acted": False, "action": "pass"}, {"ran": True, "acted": True, "action": "rewrite"}, {"ran": True, "figures": 3, "receipts": 1, "unreceipted": []})
check("every check that ran is present, with the facts the verdict reads", s == {"grounding": {"decision": "pass"}, "guardrail": {"ran": True, "acted": False, "action": "pass"}, "honesty": {"ran": True, "acted": True, "action": "rewrite"}, "compute_lineage": {"ran": True, "figures": 3, "receipts": 1, "unreceipted": 0}}, s)
check("a grounding gate with nothing to check (decision None) is NOT a check that ran", "grounding" not in ac.checks_summary({"decision": None, "reason": "no groundable content"}))
check("a guardrail that did not run is absent (never 'verified' on a check that never happened)", "guardrail" not in ac.checks_summary(None, {"ran": False, "mode": "off"}))
check("a lineage record with no figures and no receipts is absent", "compute_lineage" not in ac.checks_summary(None, None, None, {"ran": True, "figures": 0, "receipts": 0}))
check("unreceipted is a COUNT whether the stamp carries a list or a number", ac.checks_summary(None, None, None, {"ran": True, "figures": 2, "receipts": 0, "unreceipted": [{}, {}]})["compute_lineage"]["unreceipted"] == 2 and ac.checks_summary(None, None, None, {"ran": True, "figures": 2, "receipts": 0, "unreceipted": 2})["compute_lineage"]["unreceipted"] == 2)
check("malformed inputs contribute nothing and never raise", ac.checks_summary(5, "x", [1], object()) == {} and ac.checks_summary() == {})
check("nothing ran → an empty record (the reader then sees 'Not checked')", ac.checks_summary({}, {}, {}, {}) == {})

# the module is pure
src = (ROOT / "src/trusted_data_agent/agent/answer_checks.py").read_text()
tree = ast.parse(src)
imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
check("answer_checks imports nothing from the platform (a pure summary)", all((getattr(n, "module", "") or "") .split(".")[0] in ("typing", "__future__") for n in imports if isinstance(n, ast.ImportFrom)) and all(a.name in ("typing",) for n in imports if isinstance(n, ast.Import) for a in n.names))

# every engine's final_answer carries it — the verdict must rest on the same facts live and on reload
sites = {
    "Optimize (executor.py)": ROOT / "src/trusted_data_agent/agent/executor.py",
    "Conversation": ROOT / "src/trusted_data_agent/agent/engines/conversation_engine.py",
    "Ideate": ROOT / "src/trusted_data_agent/agent/engines/ideate_engine.py",
    "Focus": ROOT / "src/trusted_data_agent/agent/engines/focus_engine.py",
    "Coordinate": ROOT / "src/trusted_data_agent/agent/engines/coordinate_engine.py",
}
for name, path in sites.items():
    t = path.read_text()
    check(f"{name}: the final_answer frame carries `checks` from answer_checks", '"checks": _answer_checks.checks_summary(' in t and "from trusted_data_agent.agent import answer_checks as _answer_checks" in t)
    seg = t[t.index('"checks": _answer_checks.checks_summary(') - 400 : t.index('"checks": _answer_checks.checks_summary(')]
    check(f"{name}: the same frame carries the turn's `duration_ms` (one clock, two moments)", '"duration_ms":' in seg)
conv = sites["Conversation"].read_text()
check("Conversation persists the WHOLE-turn duration (the canonical baseline every class reports), the agent's own run as the floor", 'max(int(result.get("duration_ms", 0) or 0), int(executor._turn_duration_ms() or 0))' in conv)
eh = (ROOT / "static/js/eventHandlers.js").read_text()
check("eventHandlers hands the frame's checks and duration to the settle", "checks: eventData.checks || null, durationMs: eventData.duration_ms" in eh)
la = (ROOT / "static/js/handlers/livingAnswer.js").read_text()
check("the module reads them through the SAME stamp reducer the reload uses (one function, two moments)", "if (o.checks) reduceStamps(st, o.checks);" in la and la.count("function reduceStamps(") == 1 and "reduceStamps(st, turn);" in la)

print(f"\n{'FAILED' if F else 'ALL PASS'} — {P + F} assertions ({P} passed, {F} failed)")
sys.exit(1 if F else 0)
