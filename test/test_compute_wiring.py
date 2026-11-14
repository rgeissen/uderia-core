"""S3 — the planner wiring (`compute_insertion`): a synthesis phase whose goal is arithmetic
over the data phase before it gets ONE deterministic TDA_Compute phase inserted in front, with
every later reference renumbered; never when the switch is off, the component is disabled for
the profile, a phase already computes, the SQL already aggregates, or no data phase precedes
it; idempotent on replan. Drives the REAL pass with a stub executor. CI-pure."""
import asyncio, json, logging, os, pathlib, re, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.setdefault("TDA_COMPUTE_LINEAGE", "1")
from trusted_data_agent.core.config import APP_CONFIG
APP_CONFIG.COMPUTE_LINEAGE = True
from trusted_data_agent.agent import planner as PL

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Exec:
    def __init__(self, plan, compute_enabled=True):
        self.meta_plan = plan; self.events = []; self.user_uuid = "u"
        self._cfg = {"profile_type": "tool_enabled", "componentConfig": ({} if compute_enabled else {"compute": {"enabled": False}})}
    def _get_profile_config(self): return self._cfg
    def _log_system_event(self, ev, *a): self.events.append(ev)
    def _format_sse_with_depth(self, ev, *a): return json.dumps(ev)


def run(plan, compute_enabled=True):
    ex = _Exec(plan, compute_enabled)
    pl = PL.Planner.__new__(PL.Planner); pl.executor = ex
    list(pl._rewrite_plan_for_compute_insertion())
    return ex


def base_plan():
    return [
        {"phase": 1, "goal": "Get miles per customer", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT customer, miles FROM t"}},
        {"phase": 2, "goal": "Report the total miles across all customers", "relevant_tools": ["TDA_FinalReport"],
         "arguments": {"source_data": ["result_of_phase_1"]}},
    ]

print("[1] the insertion")
ex = run(base_plan())
p = ex.meta_plan
check("a compute phase is inserted before the synthesis phase", len(p) == 3 and p[1]["relevant_tools"] == ["TDA_Compute"] and p[1]["arguments"] == {"op": "sum"})
check("phases renumbered and the report's reference still points at the DATA phase (1)", [x["phase"] for x in p] == [1, 2, 3] and p[2]["arguments"]["source_data"] == ["result_of_phase_1"])
check("the inserted phase names the data phase it computes over", "phase 1" in p[1]["goal"])
check("a System Correction event names the pass", any(e.get("details", {}).get("pass") == "compute_insertion" for e in ex.events))
check("idempotent: running the pass again inserts nothing", len(run(p).meta_plan) == 3)

print("[2] the intents map to the closed op set")
for goal, op in [("Calculate the average miles per customer", "avg"), ("List the top 5 customers by miles", "rank"),
                 ("Show the percent change since last month", "pct_change"), ("Find the minimum balance", "min"),
                 ("Report the maximum order value", "max"), ("Sum of all revenue", "sum")]:
    pl_ = base_plan(); pl_[1]["goal"] = goal
    out = run(pl_).meta_plan
    check(f"'{goal}' → {op}", len(out) == 3 and out[1]["arguments"]["op"] == op, [x.get("arguments") for x in out])

print("[3] the refusals to insert")
pl_ = base_plan(); pl_[1]["goal"] = "Report the customers and their cities"
check("no arithmetic intent → untouched", len(run(pl_).meta_plan) == 2)
pl_ = base_plan(); pl_[0]["arguments"]["sql"] = "SELECT SUM(miles) AS total FROM t"
check("the database already produced the aggregate → untouched", len(run(pl_).meta_plan) == 2)
pl_ = base_plan(); pl_[1]["goal"] = "List the top 3 customers by miles"; pl_[0]["arguments"]["sql"] = "SELECT TOP 3 customer, miles FROM t ORDER BY miles DESC"
check("a TOP/ORDER BY query already ranks → untouched", len(run(pl_).meta_plan) == 2)
pl_ = base_plan(); pl_[0]["relevant_tools"] = ["TDA_CurrentDate"]
check("no DATA phase before the synthesis → untouched", len(run(pl_).meta_plan) == 2)
pl_ = [{"phase": 1, "goal": "Report the total miles", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
check("a one-phase plan → untouched", len(run(pl_).meta_plan) == 1)
pl_ = base_plan(); pl_.insert(1, {"phase": 2, "goal": "compute", "relevant_tools": ["TDA_Compute"], "arguments": {"op": "sum"}}); pl_[2]["phase"] = 3
check("a plan that already computes → untouched", len(run(pl_).meta_plan) == 3)
check("the component disabled for the profile → untouched", len(run(base_plan(), compute_enabled=False).meta_plan) == 2)
APP_CONFIG.COMPUTE_LINEAGE = False
check("the switch off → untouched (byte-identical)", run(base_plan()).meta_plan == base_plan())
APP_CONFIG.COMPUTE_LINEAGE = True
check("a None plan (conversational turn) → no error", run(None).meta_plan is None)

print("[3b] the scheduler never races a compute phase ahead of the data it computes over")
from trusted_data_agent.agent.executor import PlanExecutor as _PE
_plan = run(base_plan()).meta_plan
_groups = _PE._build_phase_groups(_plan)
check("the inserted compute phase runs in its own group AFTER the data phase (found live: it ran concurrently and computed over nothing)",
      [[p["phase"] for p in g] for g in _groups] == [[1], [2], [3]], _groups)
_two = [{"phase": 1, "goal": "a", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT a FROM t"}},
        {"phase": 2, "goal": "b", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT b FROM u"}},
        {"phase": 3, "goal": "c", "relevant_tools": ["TDA_Compute"], "arguments": {"op": "diff", "statement": 1, "statement_b": 2}}]
check("a compute over two statements waits for BOTH data phases", _PE._phase_compute_deps(_two[2], _two) == {1, 2})
check("a compute phase after a framework-only phase depends on nothing (nothing to read)", _PE._phase_compute_deps({"phase": 2, "relevant_tools": ["TDA_Compute"]}, [{"phase": 1, "relevant_tools": ["TDA_CurrentDate"]}]) == set())

print("[4] the pass sits between text_date_cast and plan_validation")
src = (ROOT / "src/trusted_data_agent/agent/planner.py").read_text()
passes = re.findall(r'_pass\("([a-z_]+)"', src)
check("registered once, after text_date_cast, before plan_validation",
      passes.count("compute_insertion") == 1 and passes.index("text_date_cast") < passes.index("compute_insertion") < passes.index("plan_validation"), passes)

print(f"\n=== S3 compute wiring: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
