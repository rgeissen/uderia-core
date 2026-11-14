#!/usr/bin/env python3
"""`guarded_sql_call` — one guarded SQL call for callers that are not an MCP tool.

WHY THIS GATE EXISTS
A component that runs an agent's SQL (the Database Navigator's `query` action, P1 of
docs/roadmap/DAB_P1_NAVIGATOR_QUERY.md) is the same escape hatch as an MCP SQL tool by
another name. Every protection the MCP chokepoints apply is keyed on a non-`TDA_*` tool
name or a `sql` argument, so a component call reached none of them: `deny_writes`, the
exclusive data surface, the loop guard, the turn budget, preflight, the result-set
registry, the tool-result firewall. `guarded_sql_call` runs the ReAct wrapper's sequence
around a caller-supplied executor. This gate proves each protection holds through it,
that nothing executes when a protection refuses, and that the Teradata-only rewrites
stay on Teradata.

Run: python3 test/test_guarded_sql_call.py     (no server, no network)
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
logging.disable(logging.CRITICAL)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.core import agent_authority as aa  # noqa: E402
from trusted_data_agent.agent import tool_enforcement as te  # noqa: E402
from trusted_data_agent.agent import result_sets as rs  # noqa: E402
from trusted_data_agent.agent import loop_guard as lg  # noqa: E402

NAME = "database_navigator.query"
ALIASES = frozenset({"database_navigator", "TDA_DatabaseNavigator"})
SID = "sess-guarded"
ROWS = [{"city": "Indianapolis", "n": 117}, {"city": "Tampa", "n": 90}]


class Exec:
    """A recording executor — the test asserts NOTHING ran when a protection refused."""

    def __init__(self, results=None):
        self.calls, self.results = [], list(results or [])

    async def __call__(self, args):
        self.calls.append(dict(args))
        if self.results:
            r = self.results.pop(0)
            if isinstance(r, BaseException):
                raise r
            return r
        return {"status": "success", "results": ROWS}


def run(args, ex, **kw):
    kw.setdefault("session_id", SID)
    return asyncio.run(te.guarded_sql_call(NAME, args, ex, aliases=ALIASES, **kw))


def as_principal(cfg):
    return aa.set_current_principal(aa.AgentPrincipal(owner_uuid="u", profile_id="p",
                                                      scope=aa.parse_scope(cfg)))


SELECT = {"sql": "SELECT city, COUNT(*) AS n FROM business GROUP BY city"}

# ── [1] a logical name is required — a TDA_ name would exempt everything
try:
    asyncio.run(te.guarded_sql_call("TDA_DatabaseNavigator", SELECT, Exec(), session_id=SID))
    check("[1] a TDA_ tool name is rejected", False, "no error raised")
except ValueError:
    check("[1] a TDA_ tool name is rejected", True)

# ── [2] interactive turn (no principal): executes, registers, fires the firewall
saved_cl = getattr(APP_CONFIG, "COMPUTE_LINEAGE", False)
APP_CONFIG.COMPUTE_LINEAGE = True
rs.begin_turn(SID)
seen_scan = []
orig_scan = te.scan_result
te.scan_result = lambda name, res, **k: seen_scan.append(name)
ex = Exec()
out = run(SELECT, ex)
te.scan_result = orig_scan
check("[2] success outcome", out["outcome"] == "success", out["outcome"])
check("[2] the executor ran once with the statement", len(ex.calls) == 1 and ex.calls[0]["sql"] == SELECT["sql"])
stmts = rs.statements(SID)
check("[2] the result landed in the turn's result-set registry under the logical name",
      len(stmts) == 1 and stmts[0]["tool"] == NAME and stmts[0]["row_count"] == 2, str(stmts))
check("[2] the tool-result firewall saw the result", seen_scan == [NAME], str(seen_scan))
APP_CONFIG.COMPUTE_LINEAGE = saved_cl

# ── [3] authority: each refusal leaves the database untouched
cases = [
    ("deny_writes refuses an INSERT", {"autonomous": True, "deny_writes": True},
     {"sql": "INSERT INTO t VALUES (1)"}),
    ("a component id in denied_tools refuses the call", {"autonomous": True, "denied_tools": ["database_navigator"]},
     SELECT),
    ("the TDA_ tool name in denied_tools refuses the call too", {"autonomous": True, "denied_tools": ["TDA_DatabaseNavigator"]},
     SELECT),
    ("a non-autonomous agent is refused", {"autonomous": False}, SELECT),
]
for label, cfg, args in cases:
    tok = as_principal(cfg)
    ex = Exec()
    try:
        out = run(args, ex)
    finally:
        aa.reset_current_principal(tok)
    check(f"[3] {label}", out["outcome"] == "refused" and not ex.calls, f"{out['outcome']} calls={len(ex.calls)}")

saved_obda = _OP.plane_available("read")
_OP.force("read", True)
surface = {"autonomous": True, "dataSurface": {"products": ["p1"], "exclusive": True}}
tok = as_principal(surface)
try:
    ex = Exec()
    out = run(SELECT, ex)
    check("[3] an exclusive data surface refuses raw SQL", out["outcome"] == "refused" and not ex.calls, out["outcome"])
    check("[3] the refusal names the governed route",
          "query_product" in str(out["result"]), str(out["result"])[:160])
    aa.register_governed_sql(SELECT["sql"])
    ex = Exec()
    out = run(SELECT, ex)
    check("[3] …but a statement the surface compiled this turn is allowed", out["outcome"] == "success" and len(ex.calls) == 1,
          out["outcome"])
finally:
    aa.reset_current_principal(tok)
    _OP.force("read", saved_obda)

# ── [4] shape and loop guards
ex = Exec()
out = run({"sql": {"not": "text"}}, ex)
check("[4] a non-string SQL argument is refused", out["outcome"] == "refused" and not ex.calls)

saved_th = dict(getattr(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {}) or {})
APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = dict(saved_th, max_tool_repeats=2)
ltok = lg.begin_turn()
try:
    outs = [run(SELECT, Exec())["outcome"] for _ in range(3)]
finally:
    lg.end_turn(ltok)
    APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = saved_th
check("[4] the loop guard refuses the (N+1)th identical call", outs == ["success", "success", "refused"], str(outs))

# ── [5] preflight: a refusal stops the call; Teradata-only rewrites stay on Teradata
orig_pf, orig_td = te.preflight_sql, te.text_date_rewrite
block = {"status": "error", "error": "preflight refused", "preflight_text_date": True}
tried = []
te.preflight_sql = lambda n, a, s: block
te.text_date_rewrite = lambda n, a, b, s: (tried.append(1) or
                                           {"sql": "SELECT 1", "note": "[rewrote]", "changes": ["c"], "columns": []})
try:
    ex = Exec()
    out = run(SELECT, ex, dialect="sqlite")
    check("[5] on SQLite a preflight refusal is returned, nothing runs", out["outcome"] == "refused" and not ex.calls)
    check("[5] on SQLite the Teradata rewrite is never attempted", tried == [], str(tried))
    # the rewritten statement is preflighted again; let it pass
    calls = {"n": 0}
    te.preflight_sql = lambda n, a, s: (calls.__setitem__("n", calls["n"] + 1) or (block if calls["n"] == 1 else None))
    ex = Exec()
    out = run(SELECT, ex, dialect="teradata")
    check("[5] on Teradata the mechanical rewrite runs instead", out["outcome"] == "success"
          and ex.calls and ex.calls[0]["sql"] == "SELECT 1", str(ex.calls))
    check("[5] the rewrite is reported (note + rewrite record)",
          "[rewrote]" in out["notes"] and out["rewrites"] and out["rewrites"][0]["executed_sql"] == "SELECT 1")
finally:
    te.preflight_sql, te.text_date_rewrite = orig_pf, orig_td

# ── [6] errors are results, never crashes; the timeout is bounded
ex = Exec([RuntimeError("no such column: citty")])
out = run(SELECT, ex, dialect="sqlite")
check("[6] a raising executor becomes an error outcome carrying the message",
      out["outcome"] == "error" and "citty" in str(out["result"]), str(out["result"])[:120])

orig_to = te.tool_call_timeout
te.tool_call_timeout = lambda: 0.05


async def slow(args):
    await asyncio.sleep(1)
    return {"status": "success", "results": []}


try:
    out = asyncio.run(te.guarded_sql_call(NAME, SELECT, slow, session_id=SID, aliases=ALIASES, dialect="sqlite"))
finally:
    te.tool_call_timeout = orig_to
check("[6] a slow statement is bounded and reported as timed out",
      out["outcome"] == "error" and out["result"].get("timed_out") is True, str(out["result"])[:120])

# ── [7] dialect retries: Teradata's LIMIT→TOP retry on Teradata only
err = {"status": "error", "error": "[Error 3706] Syntax error: expected something between the 'LIMIT' keyword"}
limit_sql = {"sql": "SELECT city FROM business LIMIT 5"}
ex = Exec([err, {"status": "success", "results": ROWS}])
out = run(limit_sql, ex, dialect="teradata")
check("[7] Teradata: a LIMIT tail is retried once as SELECT TOP",
      out["outcome"] == "success" and len(ex.calls) == 2 and "TOP 5" in ex.calls[1]["sql"].upper(), str(ex.calls))
check("[7] Teradata: the executed statement is the rewritten one", "TOP 5" in (out["executed_sql"] or "").upper())
ex = Exec([err])
out = run(limit_sql, ex, dialect="sqlite")
check("[7] SQLite: no Teradata retry", len(ex.calls) == 1 and out["outcome"] == "error", str(ex.calls))

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — a component's SQL gets every protection an MCP tool gets"))
sys.exit(1 if FAIL else 0)
