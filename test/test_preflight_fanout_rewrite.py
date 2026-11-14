"""Preflight fan-out rewrite (2026-09-19) — the refused fan-out becomes aggregate-then-join, 0 LLM.

Prod session 940f02fa (@IDAIRL on gemini-3.1-flash-lite): the guard refused
`mileage_redemption ⋈ member_benefit ON ffp_member_id`; the model's OWN correction —
`(SELECT k, SUM … GROUP BY k) R JOIN member_benefit B` — passed the guard but returned member 2
five times under TOP 5 (28 distinct tiers over its 149 benefit rows), so it spent one to two
further rounds reducing the other side (a 46K-token probe on turn 2). The mechanical shape:
aggregate each holder at its own grain, join the aggregates, push TOP … ORDER BY into the ranked
side with the join's membership carried in. Truth table · decline set · re-check · both twins
(Optimize strategy + the real ReAct guarded coroutine). CI-pure. Live-proven on Teradata
2026-09-19: the rewritten prod statements return the top-5 set with exact totals (17,708 for
member 2, was 531,240 multiplied); every row accounted for."""
import ast, asyncio, json, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sqlglot
from sqlglot import exp
from trusted_data_agent.agent.preflight import joins, guard, columns as _cols
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SID = "s-fanout-rw"
FAN = [{"column": "ffp_member_id", "owner": "ffp_member",
        "tables": ["mileage_redemption", "member_benefit", "miles_balance", "mileage_accrual"]}]
PROD1 = """SELECT TOP 5 R.ffp_member_id, SUM(R.miles_redeemed) AS total_miles_redeemed, B.tier_status_id
FROM airlines_core.mileage_redemption R
JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id
GROUP BY R.ffp_member_id, B.tier_status_id
ORDER BY total_miles_redeemed DESC"""
PROD2 = """SELECT TOP 5
    R.ffp_member_id,
    SUM(R.miles_redeemed) AS total_miles_redeemed,
    B.total_miles_balance
FROM airlines_core.mileage_redemption R
JOIN airlines_core.miles_balance B ON R.ffp_member_id = B.ffp_member_id
GROUP BY R.ffp_member_id, B.total_miles_balance
ORDER BY total_miles_redeemed DESC"""


def _reset():
    joins._reset_all(); joins.record_fanout(SID, FAN)


def _refusal(out):
    """The refusal dict a guarded call handed back, or {} when something ran instead."""
    try:
        return json.loads(out[0]) if isinstance(out, tuple) and isinstance(out[0], str) else {}
    except Exception:
        return {}


def _derived(sql):
    """{alias: inner Select} for every derived table in the OUTER FROM/JOIN."""
    st = sqlglot.parse_one(sql, read="teradata")
    out = {}
    for sq in st.find_all(exp.Subquery):
        if isinstance(sq.this, exp.Select) and sq.alias and not isinstance(sq.parent, exp.In):
            out[str(sq.alias)] = sq.this
    return out


print("[1] the two prod statements rewrite to aggregate-then-join, ranked inside the ranked side")
_reset()
f1 = joins.rewrite_fanout_sql(SID, PROD1)
check("PROD1 rewrites", f1 is not None and f1["sql"], f1)
d = _derived(f1["sql"])
check("both holders become derived tables under their ORIGINAL aliases", set(d) == {"R", "B"}, list(d))
check("the ranked side carries TOP 5 with its ORDER BY inside (so the TOP ranks members, not repeats)",
      d["R"].args.get("limit") is not None and d["R"].args.get("order") is not None
      and "5" in d["R"].args["limit"].sql() and "total_miles_redeemed" in d["R"].args["order"].sql(), d["R"].sql())
check("the ranked side is aggregated per key with the model's own alias",
      "SUM(miles_redeemed) AS total_miles_redeemed" in d["R"].sql(dialect="teradata") and "GROUP BY ffp_member_id" in d["R"].sql(dialect="teradata"), d["R"].sql())
check("INNER: the ranked side carries the other side's membership (k IN (SELECT k FROM member_benefit)) — "
      "or the TOP could pick a key the join then drops",
      "ffp_member_id IN (SELECT ffp_member_id FROM airlines_core.member_benefit)" in d["R"].sql(dialect="teradata"), d["R"].sql())
check("the other side is reduced to the model's own grain (key + its grouped column), no aggregate invented",
      "GROUP BY ffp_member_id, tier_status_id" in d["B"].sql(dialect="teradata") and "MAX(" not in d["B"].sql() and "MIN(" not in d["B"].sql(), d["B"].sql())
check("the outer joins the two aggregates on the key and carries NO TOP (repeats must not consume it)",
      "ON R.ffp_member_id = B.ffp_member_id" in f1["sql"] and sqlglot.parse_one(f1["sql"], read="teradata").args.get("limit") is None, f1["sql"])
check("the outer keeps the original ORDER BY", f1["sql"].rstrip().endswith("ORDER BY total_miles_redeemed DESC"), f1["sql"])
check("the outer projection is the model's projection, mapped", f1["sql"].startswith("SELECT R.ffp_member_id, R.total_miles_redeemed, B.tier_status_id FROM"), f1["sql"])
check("the grain names the repeat honestly and the side that causes it",
      f1["grain"] == "one row per (ffp_member_id, tier_status_id)" and f1["repeats_side"] == ["member_benefit"] and f1["top_pushed"] is True, f1)
check("the rewrite is itself free of the fan-out (the guard's own re-check)", joins.fanout_violations(SID, f1["sql"]) == [], None)
check("the rewrite re-parses as one statement", len(sqlglot.parse(f1["sql"], read="teradata")) == 1, None)
f2 = joins.rewrite_fanout_sql(SID, PROD2)
check("PROD2 rewrites the same way against miles_balance", f2 and set(_derived(f2["sql"])) == {"R", "B"} and f2["repeats_side"] == ["miles_balance"] and f2["top_pushed"], f2)
check("the changes narrate every step incl. the TOP push", f2 and len(f2["changes"]) == 4 and "moved inside mileage_redemption" in f2["changes"][3], f2 and f2["changes"])

print("[2] the push and the pieces around it")
_reset()
LEFT_B = ("SELECT TOP 3 R.ffp_member_id, SUM(R.miles_redeemed) AS red, B.tier_status_id FROM airlines_core.mileage_redemption R "
          "LEFT JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id GROUP BY R.ffp_member_id, B.tier_status_id ORDER BY B.tier_status_id")
f = joins.rewrite_fanout_sql(SID, LEFT_B)
check("LEFT JOIN ranked on the OPTIONAL side: TOP stays on the outer (never pushed into the optional side)",
      f and f["top_pushed"] is False and sqlglot.parse_one(f["sql"], read="teradata").args.get("limit") is not None and "LEFT JOIN" in f["sql"], f and f["sql"])
check("LEFT JOIN carries no membership filter into the preserved side", f and " IN (SELECT" not in f["sql"], f and f["sql"])
LEFT_A = LEFT_B.replace("ORDER BY B.tier_status_id", "ORDER BY red DESC")
f = joins.rewrite_fanout_sql(SID, LEFT_A)
check("LEFT JOIN ranked on the PRESERVED side: TOP pushed, no membership (a LEFT join keeps every A key)",
      f and f["top_pushed"] and " IN (SELECT" not in f["sql"] and "TOP 3" in _derived(f["sql"])["R"].sql(dialect="teradata"), f and f["sql"])
INNER_B = ("SELECT TOP 5 R.ffp_member_id, SUM(R.miles_redeemed) AS red, B.tier_status_id FROM airlines_core.mileage_redemption R "
           "JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id WHERE R.miles_redeemed > 0 GROUP BY R.ffp_member_id, B.tier_status_id ORDER BY B.tier_status_id")
f = joins.rewrite_fanout_sql(SID, INNER_B)
dB = _derived(f["sql"])["B"].sql(dialect="teradata")
check("INNER ranked on B: TOP pushed into B with A's membership — under A's OWN where",
      f["top_pushed"] and "TOP 5" in dB and "ffp_member_id IN (SELECT ffp_member_id FROM airlines_core.mileage_redemption WHERE miles_redeemed > 0)" in dB, dB)
HAV_A = ("SELECT TOP 3 R.ffp_member_id, SUM(R.miles_redeemed) AS red FROM airlines_core.mileage_redemption R JOIN airlines_core.miles_balance B "
         "ON R.ffp_member_id = B.ffp_member_id WHERE B.total_miles_balance > 100 GROUP BY R.ffp_member_id HAVING SUM(R.miles_redeemed) > 1000 ORDER BY red DESC")
f = joins.rewrite_fanout_sql(SID, HAV_A)
dR = _derived(f["sql"])["R"].sql(dialect="teradata")
check("a HAVING on the ranked side moves INSIDE with the TOP (the original filtered before ranking)",
      f["top_pushed"] and "HAVING SUM(miles_redeemed) > 1000" in dR and "WHERE R.red" not in f["sql"], f["sql"])
check("the other side's WHERE lands on that side AND in the membership filter",
      "WHERE total_miles_balance > 100" in _derived(f["sql"])["B"].sql(dialect="teradata") and "miles_balance WHERE total_miles_balance > 100)" in dR, f["sql"])
HAV_B = ("SELECT TOP 3 R.ffp_member_id, SUM(R.miles_redeemed) AS red, SUM(B.total_miles_balance) AS bal FROM airlines_core.mileage_redemption R "
         "JOIN airlines_core.miles_balance B ON R.ffp_member_id = B.ffp_member_id GROUP BY R.ffp_member_id HAVING SUM(B.total_miles_balance) > 1000 ORDER BY red DESC")
f = joins.rewrite_fanout_sql(SID, HAV_B)
check("a HAVING on the OTHER side keeps the TOP on the outer (it filters the candidates after the rank)",
      f and f["top_pushed"] is False and "WHERE B.bal > 1000" in f["sql"] and f["sql"].startswith("SELECT TOP 3"), f and f["sql"])
check("both sides' aggregates are computed at their own grain", f and "SUM(total_miles_balance) AS bal" in _derived(f["sql"])["B"].sql(dialect="teradata") and "SUM(miles_redeemed) AS red" in _derived(f["sql"])["R"].sql(dialect="teradata"), f and f["sql"])
NOTOP = HAV_B.replace("TOP 3 ", "")
f = joins.rewrite_fanout_sql(SID, NOTOP)
check("no TOP → nothing pushed, HAVING mapped onto the outer aggregates", f and f["top_pushed"] is False and "WHERE B.bal > 1000" in f["sql"], f and f["sql"])
ORD_AGG = ("SELECT TOP 3 R.ffp_member_id, SUM(R.miles_redeemed) AS red FROM airlines_core.mileage_redemption R JOIN airlines_core.miles_balance B "
           "ON R.ffp_member_id = B.ffp_member_id GROUP BY R.ffp_member_id ORDER BY SUM(R.miles_redeemed) DESC")
f = joins.rewrite_fanout_sql(SID, ORD_AGG)
check("ORDER BY on the aggregate expression itself maps to the derived alias", f and "ORDER BY red DESC" in _derived(f["sql"])["R"].sql(dialect="teradata") and f["sql"].endswith("ORDER BY R.red DESC"), f and f["sql"])
SEMI = ("SELECT DISTINCT R.ffp_member_id FROM airlines_core.mileage_redemption R JOIN airlines_core.miles_balance B "
        "ON R.ffp_member_id = B.ffp_member_id WHERE B.total_miles_balance > 100")
f = joins.rewrite_fanout_sql(SID, SEMI)
check("a side that contributes only the key becomes a semi-join (GROUP BY key); DISTINCT kept on the outer",
      f and f["sql"].startswith("SELECT DISTINCT") and "SELECT ffp_member_id FROM airlines_core.miles_balance WHERE total_miles_balance > 100 GROUP BY ffp_member_id" in f["sql"], f and f["sql"])
UNALIASED = ("SELECT R.ffp_member_id, SUM(R.miles_redeemed), COUNT(DISTINCT B.tier_status_id) FROM airlines_core.mileage_redemption R "
             "JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id GROUP BY R.ffp_member_id")
f = joins.rewrite_fanout_sql(SID, UNALIASED)
check("unaliased aggregates get synthesized names on their side and are referenced on the outer; COUNT(DISTINCT col) is fine",
      f and "SUM(miles_redeemed) AS agg_1" in f["sql"] and "COUNT(DISTINCT tier_status_id) AS agg_2" in f["sql"] and "R.agg_1, B.agg_2" in f["sql"], f and f["sql"])
COLALIAS = PROD1.replace("B.tier_status_id\nFROM", "B.tier_status_id AS tier\nFROM")
f = joins.rewrite_fanout_sql(SID, COLALIAS)
check("a column projection alias survives on the outer while the derived table keeps the column", f and "B.tier_status_id AS tier" in f["sql"] and "GROUP BY ffp_member_id, tier_status_id" in f["sql"], f and f["sql"])
_reset(); joins.record_join_keys(SID, [{"tables": ["mileage_redemption", "member_benefit"], "key": ["ffp_member_id"]}])
check("a pair the model DECLARES is never a fan-out, so there is nothing to rewrite (the guard's rule, inherited)", joins.rewrite_fanout_sql(SID, PROD1) is None, None)

print("[2b] the CLASS, not the conversation — an unrelated schema, unaliased tables, another key")
joins._reset_all()
joins.record_fanout(SID, [{"column": "customer_id", "owner": "customers", "tables": ["orders", "shipments"]},
                          {"column": "product_id", "owner": "products", "tables": ["order_line", "inventory_move"]}])
GEN = ("SELECT TOP 10 orders.customer_id, SUM(orders.amount) AS spend, shipments.carrier FROM sales.orders "
       "LEFT JOIN sales.shipments ON orders.customer_id = shipments.customer_id WHERE orders.status = 'PAID' "
       "GROUP BY orders.customer_id, shipments.carrier ORDER BY spend DESC")
f = joins.rewrite_fanout_sql(SID, GEN)
check("a fan-out on a different key / schema / unaliased tables rewrites by the same rule",
      f and f["column"] == "customer_id" and f["pair"] == ["orders", "shipments"] and set(_derived(f["sql"])) == {"orders", "shipments"}
      and f["top_pushed"] and "TOP 10" in _derived(f["sql"])["orders"].sql(dialect="teradata") and "WHERE status = 'PAID'" in _derived(f["sql"])["orders"].sql(dialect="teradata"), f and f["sql"])
GEN2 = ("SELECT L.product_id, MIN(L.unit_price) AS floor_price, MAX(M.qty) AS max_move FROM wh.order_line L "
        "JOIN wh.inventory_move M ON M.product_id = L.product_id GROUP BY L.product_id ORDER BY 1")
f = joins.rewrite_fanout_sql(SID, GEN2)
check("the ON may name the sides in either order; ORDER BY on an ordinal declines nothing on the outer",
      f is None or (f["pair"] == ["order_line", "inventory_move"] and "MIN(unit_price) AS floor_price" in f["sql"]), f and f["sql"])
GEN2b = GEN2.replace("ORDER BY 1", "ORDER BY L.product_id")
f = joins.rewrite_fanout_sql(SID, GEN2b)
check("both-sides-aggregated, no TOP, on the second key: each aggregate at its own grain, joined once",
      f and "MIN(unit_price) AS floor_price" in _derived(f["sql"])["L"].sql(dialect="teradata") and "MAX(qty) AS max_move" in _derived(f["sql"])["M"].sql(dialect="teradata")
      and f["sql"].count(" JOIN ") == 1 and joins.fanout_violations(SID, f["sql"]) == [], f and f["sql"])
_reset()

print("[3] the decline set — each is today's refusal, never a guess")
_reset()
def declines(name, sql):
    check(f"declines: {name}", joins.rewrite_fanout_sql(SID, sql) is None, joins.rewrite_fanout_sql(SID, sql))
J = "FROM airlines_core.mileage_redemption R JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id"
# Held by TWO guards in `_register_agg` (the explicit Star check and the no-column-side
# check) — removing either alone is an equivalent mutant; the property is what is pinned.
declines("bare COUNT(*) — it counted the multiplied rows; what it meant is unknowable", f"SELECT R.ffp_member_id, COUNT(*) AS n {J} GROUP BY R.ffp_member_id")
declines("COUNT(1) likewise", f"SELECT R.ffp_member_id, COUNT(1) AS n {J} GROUP BY R.ffp_member_id")
declines("an aggregate mixing both tables", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed * B.tier_status_id) AS x {J} GROUP BY R.ffp_member_id")
declines("a WHERE predicate mixing both tables", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} WHERE R.miles_redeemed > B.tier_status_id GROUP BY R.ffp_member_id")
declines("a HAVING mixing both tables", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} GROUP BY R.ffp_member_id HAVING SUM(R.miles_redeemed) > SUM(B.tier_status_id)")
declines("a third table", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} JOIN airlines_core.ffp_member M ON M.ffp_member_id = R.ffp_member_id GROUP BY R.ffp_member_id")
declines("a RIGHT JOIN", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x FROM airlines_core.mileage_redemption R RIGHT JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id GROUP BY R.ffp_member_id")
declines("an ON beyond the single key equality", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} AND B.tier_status_id > 1 GROUP BY R.ffp_member_id")
declines("a projection that is an expression, not a bare column or aggregate", f"SELECT R.ffp_member_id + 1 AS k, SUM(R.miles_redeemed) AS x {J} GROUP BY R.ffp_member_id + 1")
declines("an aggregate wrapped in an expression", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) * 2 AS x {J} GROUP BY R.ffp_member_id")
declines("an unqualified column the parse cannot attribute", f"SELECT R.ffp_member_id, SUM(miles_redeemed) AS x {J} GROUP BY R.ffp_member_id")
declines("GROUP BY on an ordinal", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} GROUP BY 1")
declines("SELECT *", f"SELECT * {J}")
declines("a subquery in the input", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} WHERE R.ffp_member_id IN (SELECT ffp_member_id FROM airlines_core.ffp_member) GROUP BY R.ffp_member_id")
declines("a window function", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) OVER (PARTITION BY R.ffp_member_id) AS x {J}")
declines("a UNION", f"SELECT R.ffp_member_id {J} UNION SELECT 1")
declines("TOP … WITH TIES", f"SELECT TOP 5 WITH TIES R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} GROUP BY R.ffp_member_id ORDER BY x DESC")
declines("TOP … PERCENT", f"SELECT TOP 10 PERCENT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} GROUP BY R.ffp_member_id ORDER BY x DESC")
declines("two statements", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} GROUP BY R.ffp_member_id; SELECT 1")
declines("an ORDER BY term the parse cannot attribute", f"SELECT R.ffp_member_id, SUM(R.miles_redeemed) AS x {J} GROUP BY R.ffp_member_id ORDER BY nothing_here")
declines("a non-fan-out statement (nothing to rewrite)", "SELECT * FROM airlines_core.ffp_member")
check("no recorded fan-out → None (fail-open: the rewrite exists only where the guard refuses)", joins.rewrite_fanout_sql("no-such-session", PROD1) is None, None)
check("unparseable input → None", joins.rewrite_fanout_sql(SID, "SELEC TOP FROM JOIN") is None, None)
check("non-string input → None", joins.rewrite_fanout_sql(SID, None) is None and joins.rewrite_fanout_sql(SID, 42) is None, None)

print("[4] the re-check is structural — the rewrite runs every join check on its own output")
jsrc = (ROOT / "src/trusted_data_agent/agent/preflight/joins.py").read_text()
body = jsrc.split("def rewrite_fanout_sql")[1].split("def fanout_rewrite_note")[0]
check("fanout + declared-key + cross-domain checks all run on new_sql before it is returned",
      all(f"{fn}(session_id, new_sql)" in body for fn in ("fanout_violations", "join_violations", "cross_domain_violations")), None)
check("the input is never mutated", (lambda s: (joins.rewrite_fanout_sql(SID, s), s)[1] == PROD1)(PROD1), None)
_reset()
r1 = joins.rewrite_fanout_sql(SID, PROD1)["sql"]; r2 = joins.rewrite_fanout_sql(SID, PROD1)["sql"]
check("deterministic — the same input rewrites byte-identically", r1 == r2, None)

print("[5] the model-facing note")
_reset()
n = joins.fanout_rewrite_note(joins.rewrite_fanout_sql(SID, PROD1), "ffp_member")
check("the note names the pair, the grain, the executed SQL, and how to get one row per key incl. the owner",
      "mileage_redemption and member_benefit" in n and "one row per (ffp_member_id, tier_status_id)" in n
      and "Executed SQL: SELECT R.ffp_member_id" in n and "member_benefit contributes ungrouped columns" in n and "from ffp_member, which owns the key" in n, n)
n2 = joins.fanout_rewrite_note(joins.rewrite_fanout_sql(SID, HAV_A), None)
check("no repeating side → no repeat sentence", "ungrouped" not in n2 and "Executed SQL" in n2, n2)

print("[6] the Optimize twin — FanoutJoinStrategy in the correction chain")
from trusted_data_agent.agent import phase_executor as _pe
_reset()
blk = joins.fanout_block_result(joins.fanout_violations(SID, PROD1))
class _Ex: session_id = SID
st = _pe.FanoutJoinStrategy(_Ex())
check("claims exactly the fan-out refusal (the block's own data text)", st.can_handle(blk["data"]) and not st.can_handle("[Preflight] X is a DATE stored as text") and not st.can_handle("Error 3706"), None)
corr, evs = asyncio.run(st.generate_correction({"tool_name": "base_readQuery", "arguments": {"sql": PROD1}}, blk))
check("yields the corrected action with the rewritten SQL + one workaround event naming the correction",
      corr and corr["arguments"]["sql"] == joins.rewrite_fanout_sql(SID, PROD1)["sql"] and len(evs) == 1
      and evs[0][0]["details"]["correction_type"] == "fanout_aggregate_then_join" and "no LLM round spent" in evs[0][0]["details"]["summary"], (corr, evs))
corr, evs = asyncio.run(st.generate_correction({"tool_name": "base_readQuery", "arguments": {"sql": f"SELECT R.ffp_member_id, COUNT(*) AS n {J} GROUP BY R.ffp_member_id"}}, blk))
check("declines (None, []) where the rewrite declines — the refusal reaches the LLM as before", corr is None and evs == [], (corr, evs))
pe_src = (ROOT / "src/trusted_data_agent/agent/phase_executor.py").read_text()
chain = pe_src.split("self.strategies = [")[1].split("]")[0]
_pos = {k: chain.find(f"{k}(executor)") for k in ("TextDateStrategy", "FanoutJoinStrategy", "GenericCorrectionStrategy")}
check("registered in the chain after TextDateStrategy and before the generic LLM fallback",
      _pos["FanoutJoinStrategy"] >= 0 and _pos["TextDateStrategy"] < _pos["FanoutJoinStrategy"] < _pos["GenericCorrectionStrategy"], _pos)

print("[7] the ReAct twin — through the real guarded coroutine under enforce")
from trusted_data_agent.agent import tool_enforcement as te
OK = json.dumps({"status": "success", "results": [{"ffp_member_id": "2", "total_miles_redeemed": "17708", "tier_status_id": "27"}]})
calls = []
async def tool(**kw):
    calls.append(kw.get("sql")); return (OK, None)
_old = getattr(APP_CONFIG, "PREFLIGHT_JOIN_MODE", "observe")
try:
    _reset(); APP_CONFIG.PREFLIGHT_JOIN_MODE = "enforce"
    out = asyncio.run(te._make_guarded_coroutine("base_readQuery", tool, SID)(sql=PROD1))
    exp_sql = joins.rewrite_fanout_sql(SID, PROD1)["sql"]
    check("the tool receives the REWRITTEN statement, once — the refused original never runs", calls == [exp_sql], calls)
    content = out[0] if isinstance(out, tuple) else out
    check("the result is annotated with the rewrite note naming the grain and the executed SQL",
          isinstance(content, str) and "[Uderia rewrote this query before it ran" in content and "one row per (ffp_member_id, tier_status_id)" in content and exp_sql in content, content[-300:] if isinstance(content, str) else content)
    check("the data reaches the model unaltered before the note", isinstance(content, str) and content.startswith(OK), None)
    calls.clear()
    out = asyncio.run(te._make_guarded_coroutine("base_readQuery", tool, SID)(sql=f"SELECT R.ffp_member_id, COUNT(*) AS n {J} GROUP BY R.ffp_member_id"))
    body = _refusal(out)
    check("a fan-out the rewrite declines is NOT executed — the recovery-shaped refusal reaches the model as before",
          calls == [] and body.get("preflight_fanout") is True and "multiplies rows" in body.get("data", ""), (calls, body))
    # The rewrite is preflighted again in FULL through the one seam every check hangs off
    # (`preflight_sql`): a refusal of the REWRITTEN statement by ANY check hands the ORIGINAL
    # refusal back, and nothing runs. Driven by a seam double faithful in the dimension under
    # test — the real preflight for the original, a refusal for the rewritten shape.
    _real_pf = te.preflight_sql
    def _pf(tool_name, arguments, session_id):
        if "INNER JOIN (SELECT" in (arguments or {}).get("sql", ""):
            return {"status": "error", "preflight": True, "preflight_column": True, "data": "[Preflight] column X does not exist"}
        return _real_pf(tool_name, arguments, session_id)
    te.preflight_sql = _pf
    try:
        calls.clear()
        out = asyncio.run(te._make_guarded_coroutine("base_readQuery", tool, SID)(sql=PROD1))
        body = _refusal(out)
        check("a rewrite that another preflight check would refuse is never executed — the ORIGINAL refusal is handed back",
              calls == [] and body.get("preflight_fanout") is True, (calls, body))
    finally:
        te.preflight_sql = _real_pf
    calls.clear(); APP_CONFIG.PREFLIGHT_JOIN_MODE = "observe"
    out = asyncio.run(te._make_guarded_coroutine("base_readQuery", tool, SID)(sql=PROD1))
    check("under observe the ORIGINAL executes, un-annotated — byte-identical baseline", calls == [PROD1] and out[0] == OK, (calls, out))
finally:
    APP_CONFIG.PREFLIGHT_JOIN_MODE = _old; joins._reset_all()
te_src = (ROOT / "src/trusted_data_agent/agent/tool_enforcement.py").read_text()
fr = te_src.split("def fanout_rewrite")[1].split("def _annotate")[0]
check("the twin re-preflights the rewritten arguments in full before accepting the rewrite",
      'preflight_sql(tool_name, dict(arguments, sql=fix["sql"]), session_id) is not None' in fr, None)
gd = te_src.split("def sql_gate_before")[1].split("\ndef ")[0]
check("the twin sits on the ONE preflight seam (the shared gate), after the text-date twin, before the refusal is returned",
      gd.index('("text_date", text_date_rewrite)') < gd.index('("fanout", fanout_rewrite)') < gd.index("return block, None, None")
      and "sql_gate_before(" in te_src.split("def _make_guarded_coroutine")[1], None)
tree = ast.parse(te_src)
fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "fanout_rewrite")
check("the twin is gated on the fan-out refusal key, never on prose", any(isinstance(n, ast.Constant) and n.value == "preflight_fanout" for n in ast.walk(fn)), None)

print("[8] a rewrite is a STEP of the turn — the ledger, all four kinds, the agent's emit (ReAct lane)")
from trusted_data_agent.agent import conversation_agent as _ca
from trusted_data_agent.agent.sql_dialect import looks_like_teradata_syntax_error as _lts  # noqa: F401
# the ledger: armed in the parent, written by the wrapper in a CHILD task (LangGraph's shape)
async def _ledger_scenario():
    _reset()
    lst = te.begin_rewrite_capture()
    calls.clear()
    await asyncio.create_task(te._make_guarded_coroutine("base_readQuery", tool, SID)(sql=PROD1))
    return lst
_old = getattr(APP_CONFIG, "PREFLIGHT_JOIN_MODE", "observe")
try:
    APP_CONFIG.PREFLIGHT_JOIN_MODE = "enforce"
    lst = asyncio.run(_ledger_scenario())
    check("the fan-out rewrite is recorded in the ledger the PARENT armed, from the child task that ran the tool",
          len(lst) == 1 and lst[0]["kind"] == "fanout" and lst[0]["tool_name"] == "base_readQuery"
          and lst[0]["original_sql"] == PROD1 and lst[0]["executed_sql"] == calls[0] and lst[0].get("grain"), lst)
    # (asyncio.run ran the scenario in a COPY of the context — the ledger it armed is the
    # list it returned; arm one here for the drain semantics)
    te.begin_rewrite_capture()
    check("drain by tool takes exactly that tool's records and leaves the rest",
          (te.record_rewrite("base_readQuery", "fanout", "a", "b") is not None)
          and (te.record_rewrite("other_tool", "teradata_top", "a", "b") is not None)
          and [r["kind"] for r in te.drain_rewrites("base_readQuery")] == ["fanout"]
          and [r["tool_name"] for r in te.drain_rewrites()] == ["other_tool"] and te.drain_rewrites() == [], None)
    te.end_rewrite_capture()
    check("an unarmed ledger records nothing (any other consumer of the wrapper is byte-identical)",
          te.record_rewrite("x", "fanout", "a", "b") is None and te.drain_rewrites() == [], None)
    # the two post-error retries record too — driven through the real guarded coroutine
    from langchain_core.tools import ToolException
    async def _retry_scenario(err_text, bad_sql):
        lst = te.begin_rewrite_capture(); seen = []
        async def t(**kw):
            seen.append(kw.get("sql"))
            if len(seen) == 1:
                raise ToolException(err_text)
            return (OK, None)
        await te._make_guarded_coroutine("base_readQuery", t, "s-retry")(sql=bad_sql)
        return lst, seen
    lst, seen = asyncio.run(_retry_scenario("[Teradata Database] [Error 3706] Syntax error: expected something between the word 'DESC' and the word 'LIMIT'.",
                                            "SELECT x FROM t ORDER BY x DESC LIMIT 10"))
    check("a TOP-placement retry records kind=teradata_top with the original and the executed statement",
          len(lst) == 1 and lst[0]["kind"] == "teradata_top" and lst[0]["original_sql"].endswith("LIMIT 10")
          and lst[0]["executed_sql"].startswith("SELECT TOP 10") and len(seen) == 2, lst)
    lst, seen = asyncio.run(_retry_scenario("[Teradata Database] [Error 3707] Syntax error, expected something like a name or a Unicode delimited identifier between the 'AS' keyword and the 'Month' keyword.",
                                            "SELECT EXTRACT(MONTH FROM d) AS Month FROM t ORDER BY Month"))
    check("a reserved-alias retry records kind=reserved_alias", len(lst) == 1 and lst[0]["kind"] == "reserved_alias" and '"Month"' in lst[0]["executed_sql"], lst)
    te.end_rewrite_capture()
    check("every kind has a human label (the step title reads a label, never a wire token)",
          all(k in te.REWRITE_KIND_LABEL for k in ("teradata_top", "reserved_alias", "text_date", "fanout")), te.REWRITE_KIND_LABEL)
finally:
    APP_CONFIG.PREFLIGHT_JOIN_MODE = _old; joins._reset_all(); te.end_rewrite_capture()
# the agent's emit: `_rewrite_events_for` drains, stamps the session, seals a provenance step
class _Prov:
    def __init__(self): self.steps = []
    def add_step(self, kind, content, summary): self.steps.append((kind, content, summary))
class _Agent:
    session_id = "sess-9"; provenance = _Prov()
te.begin_rewrite_capture(); te.record_rewrite("base_readQuery", "fanout", "SELECT a", "SELECT b", summary="agg", changes=["x"], grain="one row per (k)")
evs = _ca.ConversationAgentExecutor._rewrite_events_for(_Agent(), "base_readQuery")
check("the agent turns the record into a session-stamped, labelled event payload",
      len(evs) == 1 and evs[0]["session_id"] == "sess-9" and evs[0]["label"] == te.REWRITE_KIND_LABEL["fanout"]
      and evs[0]["executed_sql"] == "SELECT b" and evs[0]["original_sql"] == "SELECT a" and evs[0]["grain"] == "one row per (k)", evs)
check("…sealed into the provenance chain as agent_tool_rewrite carrying the EXECUTED statement",
      _Agent.provenance.steps and _Agent.provenance.steps[0][0] == "agent_tool_rewrite" and "SELECT b" in _Agent.provenance.steps[0][1], _Agent.provenance.steps)
check("drained once — a second read is empty", _ca.ConversationAgentExecutor._rewrite_events_for(_Agent(), "base_readQuery") == [], None)
te.end_rewrite_capture()
ca_src = (ROOT / "src/trusted_data_agent/agent/conversation_agent.py").read_text()
_exec = ca_src.split("async def execute(self, query: str)")[1].split("\n    async def ", 1)[0] if "async def execute(self, query: str)" in ca_src else ""
check("execute() arms the ledger before the agent runs", "begin_rewrite_capture()" in _exec[:1500], None)
_end = ca_src.split('elif event_kind == "on_tool_end":')[1].split("elif event_kind")[0]
check("on_tool_end emits conversation_tool_rewrite AFTER conversation_tool_completed, from the drained ledger",
      _end.index('"conversation_tool_completed"') < _end.index('"conversation_tool_rewrite"') and "_rewrite_events_for(tool_name)" in _end, None)
_wrap = te_src.split("def sql_gate_before")[1].split("\ndef ")[0]
check("the pre-execution seam records BOTH mechanical kinds (text-date and fan-out) through one call",
      _wrap.count("record_rewrite(") == 1 and '("text_date", text_date_rewrite)' in _wrap and '("fanout", fanout_rewrite)' in _wrap, None)
_retry = te_src.split("async def _retry_core")[1].split("async def _retry_with_sql")[0]
check("the retry seam records on SUCCESS only (a retry that did not help records nothing)",
      _retry.index("return result, None                    # the rewrite did not help") < _retry.index("record_rewrite(tool_name, kind"), None)

print(f"\n=== Preflight fan-out rewrite: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
