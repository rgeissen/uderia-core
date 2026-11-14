#!/usr/bin/env python3
"""A decimal division Teradata truncates to a literal's scale is WARNED about, never rewritten.

`7/100.0` returns 0.1 and `1.0/3` returns 0.3 on Teradata (measured 2026-10-01) — silently. This
asserts the detector against the shapes that truncate and the ones that do not, the mode ladder,
that the note travels on the one SQL gate's note channel (so every lane carries it) and only for a
statement that RAN, that the dial's reading names the finding, and — with TDA_LIVE_TERADATA=1 —
that every flagged expression really returns a different figure than its FLOAT form.

CI-pure by default: no server, no network, no LLM.
"""
import os
import pathlib
import sys

sys.path.insert(0, "src")
sys.path.insert(0, ".")

passed = 0
failures = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


from trusted_data_agent.agent.preflight import decimals as D  # noqa: E402
from trusted_data_agent.agent.preflight import guard as G  # noqa: E402
from trusted_data_agent.agent.preflight import dialect as DI  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402

td = D.truncating_divisions

print("\n[1] what truncates is flagged")
for q, expr in (("SELECT 1.0/3", "1.0 / 3"),
                ("SELECT 7*1.0/3", "7 * 1.0 / 3"),
                ("SELECT x/100.0 FROM t", "x / 100.0"),
                ("SELECT SUM(credit)*1.0/COUNT(*) FROM t", "SUM(credit) * 1.0 / COUNT(*)"),
                ("SELECT 7.25/2", "7.25 / 2")):
    f = td(q)
    check(f"flagged: {q}", bool(f) and f[0]["expr"] == expr, f)
check("…with the places the literal gives", td("SELECT 1.0/3")[0]["places"] == 1
      and td("SELECT 7.25/2")[0]["places"] == 2)
f = td("SELECT x*1.0/y/2 FROM t")
check("a chained division is reported once, as the whole chain", len(f) == 1 and f[0]["expr"] == "x * 1.0 / y / 2", f)

print("\n[2] what does not truncate is left alone")
for q, why in (("SELECT CAST(x AS FLOAT)/3 FROM t", "a FLOAT operand"),
               ("SELECT 1.0E0*x/3 FROM t", "an exponent literal (FLOAT)"),
               ("SELECT CAST(x AS FLOAT)*1.0/3 FROM t", "a short literal beside a FLOAT operand"),
               ("SELECT x*1.0000/3 FROM t", "a literal with four places"),
               ("SELECT CAST(x AS DECIMAL(10,4))*1.0/y FROM t", "an explicit decimal cast with more places"),
               ("SELECT x/3 FROM t", "integer division (another class)"),
               ("SELECT x*100.0/total FROM t", "a percentage multiplier (deliberate idiom)"),
               ("SELECT 100.0*x/t FROM t", "a percentage multiplier on the left"),
               ("SELECT ROUND(x*1.0/y, 1) FROM t", "ROUND to the literal's places (a chosen precision)"),
               ("SELECT CAST(x*1.0/y AS DECIMAL(10,1)) FROM t", "a decimal CAST to the literal's places"),
               ("SELECT '1.0/3' FROM t", "text in quotes"),
               ("SELECT x FROM t WHERE a = 1", "no division"),
               ("SELEC broken 1.0/3", "an unparseable statement (fail open)")):
    check(f"not flagged: {why}", td(q) == [], td(q))
check("ROUND to MORE places than the literal still flags (the scale cut happened first)",
      bool(td("SELECT ROUND(x*1.0/y, 4) FROM t")))
check("non-string input → []", td(None) == [] and td("") == [])
n = D.note(td("SELECT 7*1.0/3"))
check("the note names the expression and both remedies",
      n.startswith(D.NOTE_MARKER) and "`7 * 1.0 / 3`" in n and "CAST(x AS FLOAT)" in n and "1.0000" in n, n)
check("no findings → no note", D.note([]) is None)

print("\n[3] the mode ladder and the guard")
OK = {"status": "success", "results": [{"x": 1}]}
ARGS = {"sql": "SELECT 7*1.0/3 FROM t"}
_saved = getattr(APP_CONFIG, "PREFLIGHT_DECIMAL_MODE", "observe")
try:
    APP_CONFIG.PREFLIGHT_DECIMAL_MODE = "observe"
    check("observe → no note (logged and tallied only)", G.decimal_division_note("base_readQuery", ARGS, OK) is None)
    APP_CONFIG.PREFLIGHT_DECIMAL_MODE = "off"
    check("off → no note", G.decimal_division_note("base_readQuery", ARGS, OK) is None)
    APP_CONFIG.PREFLIGHT_DECIMAL_MODE = "enforce"
    check("enforce → the note", (G.decimal_division_note("base_readQuery", ARGS, OK) or "").startswith(D.NOTE_MARKER))
    check("a statement that failed carries no figure → no note",
          G.decimal_division_note("base_readQuery", ARGS, {"status": "error"}) is None
          and G.decimal_division_note("base_readQuery", ARGS, {"success": False}) is None)
    with DI.using("postgres"):
        check("another dialect → no note (PostgreSQL numeric division does not truncate so)",
              G.decimal_division_note("base_readQuery", ARGS, OK) is None)
    check("no division → no note", G.decimal_division_note("base_readQuery", {"sql": "SELECT 1"}, OK) is None)

    from trusted_data_agent.agent import assurance_tally as T
    T.begin_turn()
    try:
        G.decimal_division_note("base_readQuery", ARGS, OK)
        pf = (T.snapshot() or {}).get("preflight") or {}
    finally:
        T.end_turn()
    check("tallied as a preflight finding naming the expression",
          pf.get("flagged_count") == 1 and pf.get("decimal_divisions") == ["7 * 1.0 / 3"], pf)

    print("\n[4] it travels on the one gate's note channel — every lane")
    from trusted_data_agent.agent.tool_enforcement import sql_gate_after
    vn, _ = sql_gate_after("base_readQuery", ARGS, OK, "s-dec")
    check("a successful statement → the gate returns the note", vn and vn["note"].startswith(D.NOTE_MARKER), vn)
    check("…with no unverified literals (the value check owns those)", vn and vn["unverified"] == [], vn)
    vn, _ = sql_gate_after("base_readQuery", ARGS, {"status": "error", "data": "x"}, "s-dec")
    check("a failed statement → nothing", vn is None, vn)
    vn, _ = sql_gate_after("TDA_Compute", ARGS, OK, "s-dec")
    check("a framework tool → nothing", vn is None, vn)
finally:
    APP_CONFIG.PREFLIGHT_DECIMAL_MODE = _saved

src = pathlib.Path("src/trusted_data_agent/mcp_adapter/adapter.py").read_text()
check("Optimize lane: an unverified list is stamped only when the value check named one",
      'if _vn.get("unverified"):' in src)
te = pathlib.Path("src/trusted_data_agent/agent/tool_enforcement.py").read_text()
check("the ReAct and Navigator lanes read the same gate's note", te.count('vn["note"] if vn else None') >= 2)

print("\n[5] the dial names what it found")
from trusted_data_agent.agent import assurance_points as AP  # noqa: E402
r = AP._read_preflight({"preflight": {"ran": True, "mode": "enforce", "flagged_count": 1,
                                      "decimal_divisions": ["7 * 1.0 / 3"]}}, None)
check("a decimal finding is named as one", "decimal division truncated" in r.detail and "7 * 1.0 / 3" in r.detail, r.detail)
check("…never as an unknown database or column", "unknown database or column" not in r.detail, r.detail)
check("…and says it was noted, not blocked, under enforce", "noted on the result" in r.detail, r.detail)
r = AP._read_preflight({"preflight": {"ran": True, "mode": "enforce", "flagged_count": 1,
                                      "unverified_values": ["agency.city='Chicago'"]}}, None)
check("a value finding is named as one too (it used to read 'unknown database or column')",
      "agency.city='Chicago'" in r.detail, r.detail)
r = AP._read_preflight({"preflight": {"ran": True, "mode": "observe", "flagged_count": 1,
                                      "unknown_dbs": ["nope"]}}, None)
check("an observed database finding keeps its wording", "unknown database(s) nope" in r.detail
      and "observe mode" in r.detail, r.detail)

print("\n[6] configuration")
check("APP_CONFIG default observe", getattr(type(APP_CONFIG), "PREFLIGHT_DECIMAL_MODE", None) in ("observe", _saved))
check("compose forwards the switch",
      "TDA_PREFLIGHT_DECIMAL_MODE=${TDA_PREFLIGHT_DECIMAL_MODE:-observe}" in pathlib.Path("docker-compose.yml").read_text())

if os.environ.get("TDA_LIVE_TERADATA") == "1" and os.environ.get("TDP_HOST"):
    print("\n[L] live: every flagged expression returns a different figure than its FLOAT form")
    import teradatasql
    cur = teradatasql.connect(host=os.environ["TDP_HOST"], user=os.environ["TDP_USER"],
                              password=os.environ["TDP_PASSWORD"]).cursor()
    for dec, flt in (("7/100.0", "CAST(7 AS FLOAT)/100"), ("1.0/3", "CAST(1 AS FLOAT)/3"),
                     ("7*1.0/3", "CAST(7 AS FLOAT)/3"), ("7.25/2", "CAST(7.25 AS FLOAT)/2")):
        check(f"live: {dec} is flagged", bool(td(f"SELECT {dec}")))
        cur.execute(f"SELECT {dec}, {flt}")
        a, b = cur.fetchall()[0]
        check(f"live: {dec} = {a} ≠ {b} (FLOAT)", abs(float(a) - float(b)) > 1e-6, (a, b))
    cur.execute("SELECT x*1.0000/3 FROM (SELECT 7 AS x) d")
    check("live: the unflagged four-place form keeps the precision",
          abs(float(cur.fetchall()[0][0]) - 7 / 3) < 1e-3)
else:
    print("\n[L] live check skipped — set TDA_LIVE_TERADATA=1 and TDP_HOST/TDP_USER/TDP_PASSWORD")

print("\n" + "=" * 68)
print(f"RESULT: {'FAILURES' if failures else 'ALL PASS'}  ({passed} passed, {len(failures)} failed)")
print("=" * 68)
sys.exit(1 if failures else 0)
