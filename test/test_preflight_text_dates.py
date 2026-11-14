"""Preflight text-date check (2026-09-05) — the lexical-date class.

Production: `INCDATE` is a DATE deployed as CHAR(11) holding `31-OCT-2023`; the agent
compared it as a string, bucketed it with SUBSTR(…,1,7) and ordered it bare — a "trend"
of arbitrary days that nothing errored on. The enrichment names the text-stored date
columns; this guard refuses, before the database sees it, a use it can PROVE is lexical
(shape sampled and not ISO) and, under enforce, an unverified one — naming the single
probe that settles the shape. Truth table + learning + recording + mode ladder +
metadata source + AST wiring. CI-pure."""
import ast, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.agent.preflight import textdates as td, guard
from trusted_data_agent.components import dateforms as df
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

SID = "s-textdates"
COLS = [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)"}, {"name": "BH018", "type": "VARCHAR(11)"}]},
        {"table": "NIBRS_Source_Domain.Arrestee", "columns": ["BH018"]}]
PROD = ("SELECT SUBSTR(i.INCDATE,1,7) AS Month_Year, COUNT(*) AS n FROM NIBRS_Source_Domain.Incident i "
        "WHERE i.INCDATE >= '2023-01-01' GROUP BY 1 ORDER BY 1")
ORDER = "SELECT TOP 30 INCDATE, COUNT(*) FROM NIBRS_Source_Domain.Incident GROUP BY INCDATE ORDER BY INCDATE DESC"
CAST_OK = ("SELECT CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') AS d, COUNT(*) FROM NIBRS_Source_Domain.Incident "
           "WHERE CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') BETWEEN DATE '2023-01-01' AND DATE '2023-12-31' GROUP BY 1 ORDER BY 1")
CAST_BAD = "SELECT CAST(INCDATE AS DATE FORMAT 'YYYY-MM-DD') FROM NIBRS_Source_Domain.Incident"
PROBE = "SELECT TOP 3 INCDATE FROM NIBRS_Source_Domain.Incident WHERE INCDATE IS NOT NULL AND TRIM(INCDATE) <> ''"
SAMPLE = {"status": "success", "results": [{"INCDATE": "31-OCT-2023   "}, {"INCDATE": "01-JAN-2023"}, {"INCDATE": None}]}

def _reset():
    td._reset_all(); td.record_text_dates(SID, COLS)

def t_shapes():
    check("31-OCT-2023 (padded) → DD-MMM-YYYY", df.date_shape("31-OCT-2023   ") == "DD-MMM-YYYY", df.date_shape("31-OCT-2023   "))
    check("ISO / slash / compact", (df.date_shape("2023-10-31"), df.date_shape("10/31/2023"), df.date_shape("13/01/2024", "DMY"), df.date_shape("20231031")) == ("YYYY-MM-DD", "MM/DD/YYYY", "DD/MM/YYYY", "YYYYMMDD"), None)
    check("a quarter, a blank, a 4-digit count have no FORMAT", df.date_shape("2023Q4") is None and df.date_shape("  ") is None and df.date_shape("12345678") is None, None)
    check("column_shape: one shape over the non-empty values", df.column_shape(["31-OCT-2023", "01-JAN-2023", None, ""]) == "DD-MMM-YYYY", None)
    check("column_shape: mixed shapes settle nothing", df.column_shape(["31-OCT-2023", "2023-01-01"]) is None and df.column_shape([]) is None, None)
    check("FORMAT → the strftime sqlglot reports", df.format_to_strftime("DD-MMM-YYYY") == "%d-%b-%Y" and df.format_to_strftime("YYYY-MM-DD") == "%Y-%m-%d", df.format_to_strftime("DD-MMM-YYYY"))
    check("only ISO is a date-ordered text", df.is_iso_shape("YYYY-MM-DD") and df.is_iso_shape("YYYY-MM-DDBHH:MI:SS") and not df.is_iso_shape("DD-MMM-YYYY") and not df.is_iso_shape(None), None)

def t_refs():
    _reset()
    kinds = {(f["kind"], f["snippet"]) for f in td.sql_text_date_refs(SID, PROD)}
    check("the production statement: a SUBSTR bucket and a string range comparison", kinds == {("substr", "SUBSTRING(i.INCDATE, 1, 7)"), ("range_literal", "i.INCDATE >= '2023-01-01'")}, kinds)
    check("a bare ORDER BY is an order finding; a bare GROUP BY is not a finding", [f["kind"] for f in td.sql_text_date_refs(SID, ORDER)] == ["order"], None)
    check("MIN/MAX, a DATE expression, EXTRACT, LEFT, IN", {f["kind"] for f in td.sql_text_date_refs(SID,
        "SELECT MAX(INCDATE), LEFT(INCDATE,4), EXTRACT(YEAR FROM INCDATE) FROM NIBRS_Source_Domain.Incident WHERE INCDATE > ADD_MONTHS(CURRENT_DATE,-3) AND INCDATE IN ('2023-01-01')")}
        == {"minmax", "substr", "implicit_cast", "date_expr", "equality_literal"}, None)
    check("<> '' and IS NULL are not findings (a blank check is not a date operation)", td.sql_text_date_refs(SID, "SELECT COUNT(*) FROM NIBRS_Source_Domain.Incident WHERE INCDATE <> '' AND INCDATE IS NOT NULL") == [], None)
    check("the probe itself is not a finding", td.sql_text_date_refs(SID, PROBE) == [], None)
    check("a CAST with FORMAT is classified as the right form", [f["kind"] for f in td.sql_text_date_refs(SID, CAST_OK)] == ["cast_format", "cast_format"], None)
    check("TRIM/UPPER are transparent: TRIM(INCDATE) >= '…' is still a range comparison", [f["kind"] for f in td.sql_text_date_refs(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident WHERE TRIM(INCDATE) >= '2023-01-01'")] == ["range_literal"], None)
    check("an unrecorded table's same-named column is not judged", td.sql_text_date_refs(SID, "SELECT * FROM Other o WHERE o.INCDATE >= '2023-01-01'") == [], None)
    check("an unqualified column two recorded tables carry is not judged (fail-open)",
          td.sql_text_date_refs(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident i JOIN NIBRS_Source_Domain.Arrestee a ON i.ORI=a.ORI WHERE BH018 >= '2023-01-01'") == []
          and [f["kind"] for f in td.sql_text_date_refs(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident i JOIN NIBRS_Source_Domain.Arrestee a ON i.ORI=a.ORI WHERE a.BH018 >= '2023-01-01'")] == ["range_literal"], None)
    check("an unparseable statement / no recorded columns → nothing", td.sql_text_date_refs(SID, "SELEC FROM WHERE") == [] and td.sql_text_date_refs("other", PROD) == [], None)

def t_verdicts():
    _reset()
    p, u = td.text_date_violations(SID, PROD)
    check("shape unknown: the lexical uses are UNVERIFIED, nothing is proven", p == [] and {f["kind"] for f in u} == {"substr", "range_literal"}, (p, u))
    check("shape unknown: a CAST with any FORMAT passes (the right form; the database teaches the parameter)", td.text_date_violations(SID, CAST_BAD) == ([], []), None)
    learned = td.learn_from_result(SID, PROBE, SAMPLE)
    check("the probe's rows teach the shape", learned == [("incident", "INCDATE", "DD-MMM-YYYY")], learned)
    p, u = td.text_date_violations(SID, PROD)
    check("shape known and not ISO: the same statement is PROVEN, naming shape + sample", u == [] and {f["kind"] for f in p} == {"substr", "range_literal"} and p[0]["shape"] == "DD-MMM-YYYY" and p[0]["sample"] == "31-OCT-2023", p)
    check("bare ORDER BY on the known text date is proven", td.text_date_violations(SID, ORDER)[0][0]["kind"] == "order", None)
    check("the correct CAST … FORMAT passes on every use", td.text_date_violations(SID, CAST_OK) == ([], []), None)
    check("a mismatched FORMAT is proven and names the shape", td.text_date_violations(SID, CAST_BAD)[0][0]["kind"] == "cast_format", None)
    check("CAST with no FORMAT is never a finding", td.text_date_violations(SID, "SELECT CAST(INCDATE AS DATE) FROM NIBRS_Source_Domain.Incident") == ([], []), None)
    check("equality against a literal in the column's own shape passes; in another shape it is proven",
          td.text_date_violations(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident WHERE INCDATE = '31-OCT-2023'") == ([], [])
          and td.text_date_violations(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident WHERE INCDATE = '2023-10-31'")[0][0]["kind"] == "equality_literal", None)
    td.learn_shape("incident", "BH018", ["2023-01-05", "2023-02-06"])
    check("an ISO-stored text date passes every lexical use (a string compare IS a date compare there)",
          td.text_date_violations(SID, "SELECT MAX(BH018) FROM NIBRS_Source_Domain.Incident WHERE BH018 >= '2023-01-01' ORDER BY BH018") == ([], []), None)
    check("…but a non-ISO literal against an ISO column is still wrong",
          td.text_date_violations(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident WHERE BH018 >= '01/01/2023'")[0][0]["kind"] == "range_literal", None)
    check("a shape learned for one table's column does not speak for another table's", td.known_shape("arrestee", "BH018") is None, None)

def t_learning():
    _reset()
    check("rows not carrying the column teach nothing", td.learn_from_result(SID, "SELECT COUNT(*) n FROM NIBRS_Source_Domain.Incident", {"results": [{"n": 3}]}) == [], None)
    check("rows of a table the statement does not name teach nothing", td.learn_from_result(SID, "SELECT * FROM Other", {"results": [{"INCDATE": "31-OCT-2023"}]}) == [], None)
    check("mixed shapes teach nothing (the evidence does not settle it)", td.learn_from_result(SID, PROBE, {"results": [{"INCDATE": "31-OCT-2023"}, {"INCDATE": "2023-01-01"}]}) == [] and td.known_shape("incident", "INCDATE") is None, None)
    check("a malformed result never raises", td.learn_from_result(SID, PROBE, "nope") == [] and td.learn_from_result(SID, PROBE, {"results": "x"}) == [] and td.learn_from_result(SID, None, SAMPLE) == [], None)
    check("the column key is matched case-insensitively", td.learn_from_result(SID, PROBE, {"results": [{"incdate": "31-OCT-2023"}]}) == [("incident", "INCDATE", "DD-MMM-YYYY")], None)
    from trusted_data_agent.agent import tool_enforcement as te
    blocks = [{"type": "text", "text": '{"status":"success","results":[{"INCDATE":"31-OCT-2023"}]}'}]
    check("the ReAct content-block shape is readable by the recording seam", te._coerce_to_dict((blocks, None)) == {"status": "success", "results": [{"INCDATE": "31-OCT-2023"}]} and te._coerce_to_dict(blocks)["status"] == "success", te._coerce_to_dict((blocks, None)))
    td._reset_all(); td.record_text_dates(SID, COLS)
    guard.maybe_learn_date_shapes("base_readQuery", {"sql": PROBE}, SAMPLE, SID)
    check("the guard seam learns from a result", td.known_shape("incident", "INCDATE")["format"] == "DD-MMM-YYYY", None)
    guard.maybe_learn_date_shapes("base_readQuery", {"sql": PROBE}, None, SID)
    guard.maybe_learn_date_shapes("base_tableList", {"database_name": "x"}, {"results": [{"INCDATE": "x"}]}, SID)
    check("the seam ignores non-dict results and tools without SQL", td.known_shape("incident", "INCDATE")["format"] == "DD-MMM-YYYY", None)

def t_recording():
    td._reset_all()
    n = td.record_from_event(SID, {"enrichments": [{"text_date_columns": COLS}]})
    check("columns are recorded from the enrichment event, keyed by table leaf", n == 3 and set(td.known_columns(SID)) == {"incident", "arrestee"}, (n, td.known_columns(SID)))
    check("a plain-string column entry is accepted", td.known_columns(SID)["arrestee"]["bh018"]["column"] == "BH018", None)
    n2 = td.record_from_event(SID, {"enrichments": [{"entity_count": 0}]})
    check("an enrichment without the list does not erase earlier columns", n2 == 3, n2)
    check("malformed events never raise and record nothing", td.record_from_event("s-fresh", "nope") == 0 and td.record_from_event("s-fresh", {"enrichments": [None, 3]}) == 0 and td.known_columns("s-fresh") == {}, None)
    check("a shape is remembered process-wide (a data property), the columns per session",
          (td.learn_shape("incident", "INCDATE", ["31-OCT-2023"]) == "DD-MMM-YYYY") and td.known_shape("incident", "INCDATE") is not None and td.known_columns("other-session") == {}, None)

def t_mode_ladder():
    _reset()
    old = getattr(APP_CONFIG, "PREFLIGHT_TEXT_DATE_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "observe"
        check("observe (the default) never blocks", guard.check_text_dates(PROD, SID) is None and guard.check_sql("base_readQuery", {"sql": PROD}, SID) is None, None)
        check("the shipped default IS observe (byte-identical baseline)", old in ("observe", "enforce", "off"), old)
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "enforce"
        r = guard.check_sql("base_readQuery", {"sql": PROD}, SID)
        check("enforce, shape unknown: refuses naming the probe that settles the shape", isinstance(r, dict) and r.get("preflight_text_date") and PROBE in r["data"] and "not yet known" in r["data"] and r["violations"] == [] and len(r["unverified"]) == 2, r)
        td.learn_from_result(SID, PROBE, SAMPLE)
        r = guard.check_sql("base_readQuery", {"sql": PROD}, SID)
        check("enforce, shape known: refuses with the exact FORMAT and the rewrite", isinstance(r, dict) and "FORMAT 'DD-MMM-YYYY'" in r["data"] and "CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY')" in r["data"] and "NOT executed" in r["data"] and r["violations"], r)
        check("the refusal is recovery-shaped for both engines (status/error/data)", r["status"] == "error" and r["error"] and r["preflight"], None)
        check("enforce lets the correct CAST … FORMAT through", guard.check_sql("base_readQuery", {"sql": CAST_OK}, SID) is None, None)
        check("a non-SQL tool call is untouched", guard.check_sql("base_tableList", {"database_name": "x"}, SID) is None, None)
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "off"
        check("off is a no-op", guard.check_text_dates(PROD, SID) is None, None)
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "review"
        check("an unknown-but-stronger mode clamps to enforce (never weakens silently)", isinstance(guard.check_text_dates(PROD, SID), dict), None)
    finally:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = old

def t_metadata_source():
    from components.builtin.knowledge_graph import handler as kgh
    inc = {"label": "INCDATE", "description": "INCIDENT DATE - INCDATE should be in the month, day, and year (MM/DD/YYYY) format", "deployed_type": "CHAR(11)"}
    check("the predicate: a DATE deployed as CHAR(11) is a text date", kgh._is_text_date_attr(inc), None)
    check("a one-character flag whose description mentions a date is NOT (width floor)", not kgh._is_text_date_attr({"label": "V1006", "description": "REPORT DATE INDICATOR", "deployed_type": "CHAR(1)"})
          and not kgh._is_text_date_attr({"label": "V1007", "description": "INCIDENT DATE HOUR", "deployed_type": "VARCHAR(2)"}), None)
    check("a DATE deployed as DATE is not; an unwidthed STRING is (nothing narrows it)", not kgh._is_text_date_attr({"label": "INCDATE", "deployed_type": "DATE"}) and kgh._is_text_date_attr({"label": "arrest_dt", "deployed_type": "STRING"}), None)
    parts = kgh._governance_parts(dict(inc), complete=True)
    check("the enrichment's warning line reads the SAME predicate", any("STRING order, not date order" in p for p in parts), parts)
    parts = kgh._governance_parts({"label": "V1006", "description": "REPORT DATE INDICATOR", "deployed_type": "CHAR(1)"}, complete=True)
    check("…so the flag column no longer gets the line", not any("date order" in p for p in parts), parts)
    class Cap:
        def __init__(self, d): self.deployed_as = d
    sub = {"entities": [
        {"id": 1, "entity_type": "entity", "name": "NIBRS_Source.Incident", "properties": {}},
        {"id": 2, "entity_type": "attribute", "name": "NIBRS_Source.Incident.INCDATE", "properties": inc},
        {"id": 3, "entity_type": "attribute", "name": "NIBRS_Source.Incident.V1006", "properties": {"label": "V1006", "description": "REPORT DATE INDICATOR", "deployed_type": "CHAR(1)"}},
        {"id": 4, "entity_type": "attribute", "name": "NIBRS_Source.Incident.BH018", "properties": {"label": "BH018", "title": "AGENCY INACTIVE DATE", "deployed_type": "VARCHAR(11)"}},
        {"id": 5, "entity_type": "attribute", "name": "NIBRS_Source.Incident.V4019", "properties": {"label": "V4019", "description": "SEX OF VICTIM", "deployed_type": "VARCHAR(2)"}},
    ], "relationships": [
        {"relationship_type": "contains", "source_name": "NIBRS_Source.Incident", "target_name": f"NIBRS_Source.Incident.{c}"} for c in ("INCDATE", "V1006", "BH018", "V4019")
    ]}
    tdc = kgh._text_date_columns(sub, {"NIBRS_Source.Incident": Cap("NIBRS_Source_Domain.Incident")})
    check("the metadata lists the text dates under the DEPLOYED table, with their types, sorted",
          tdc == [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "BH018", "type": "VARCHAR(11)"}, {"name": "INCDATE", "type": "CHAR(11)"}]}], tdc)
    check("no capability → the logical name (the guard keys by leaf either way)", kgh._text_date_columns(sub)[0]["table"] == "NIBRS_Source.Incident", None)
    td._reset_all(); td.record_from_event(SID, {"enrichments": [{"text_date_columns": tdc}]})
    check("the metadata shape feeds the guard directly", [f["kind"] for f in td.sql_text_date_refs(SID, ORDER)] == ["order"], None)

def t_rewrite():
    _reset()
    RANGE = "SELECT TOP 30 INCDATE, COUNT(*) n FROM NIBRS_Source_Domain.Incident i WHERE i.INCDATE >= '2023-01-01' AND TRIM(INCDATE) <> '' GROUP BY INCDATE ORDER BY INCDATE DESC"
    check("shape unknown: no rewrite (the fix needs the shape)", td.rewrite_sql(SID, RANGE) is None, None)
    td.learn_from_result(SID, PROBE, SAMPLE)
    r = td.rewrite_sql(SID, RANGE)
    check("range + bare ORDER BY: the column is wrapped in the learned CAST, the literal becomes a DATE",
          r and "CAST(i.INCDATE AS DATE FORMAT 'DD-MMM-YYYY') >= CAST('2023-01-01' AS DATE)" in r["sql"] and "ORDER BY CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') DESC" in r["sql"], r)
    check("…while the blank check, the SELECT list and the GROUP BY are left as text (wrapping a blank check would raise 2666)",
          r and "TRIM(INCDATE) <> ''" in r["sql"] and "GROUP BY INCDATE" in r["sql"] and r["sql"].startswith("SELECT TOP 30 INCDATE,"), r)
    check("the rewritten statement has nothing left to refuse", r and td.text_date_violations(SID, r["sql"]) == ([], []), None)
    check("every change is named (before → after) and the columns listed", r and len(r["changes"]) == 2 and all("→" in c for c in r["changes"]) and r["columns"] == ["NIBRS_Source_Domain.Incident.INCDATE"], r)
    r = td.rewrite_sql(SID, "SELECT MAX(INCDATE) FROM NIBRS_Source_Domain.Incident WHERE INCDATE BETWEEN '2023-01-01' AND '2023-12-31' AND INCDATE IN ('31-OCT-2023') AND INCDATE > CURRENT_DATE - 30 AND EXTRACT(YEAR FROM INCDATE) = 2023")
    check("MIN/MAX, BETWEEN, a DATE expression and EXTRACT are all mechanical",
          r and "MAX(CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY'))" in r["sql"] and "BETWEEN CAST('2023-01-01' AS DATE) AND CAST('2023-12-31' AS DATE)" in r["sql"]
          and "CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') > CURRENT_DATE - 30" in r["sql"] and "EXTRACT(YEAR FROM CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY'))" in r["sql"], r)
    check("an equality in the column's own shape is NOT touched (it passes the guard)", r and "INCDATE IN ('31-OCT-2023')" in r["sql"], r)
    r = td.rewrite_sql(SID, CAST_BAD.replace("YYYY-MM-DD", "DD-MON-YYYY"))
    check("a mismatched FORMAT is replaced by the learned one", r and "CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY')" in r["sql"] and "DD-MON" not in r["sql"], r)
    check("a SUBSTR bucket on the column is NOT rewritten (which period was meant is the model's call)", td.rewrite_sql(SID, PROD) is None, None)
    check("an ambiguous slash literal is NOT rewritten (1 Feb or 2 Jan?)", td.rewrite_sql(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident WHERE INCDATE >= '01/02/2023'") is None, None)
    r = td.rewrite_sql(SID, "SELECT SUBSTR(ORI,1,2) st, COUNT(*) FROM NIBRS_Source_Domain.Incident WHERE INCDATE >= '2023-01-01' GROUP BY 1")
    check("an unrelated SUBSTR survives as SUBSTR (sqlglot's SUBSTRING(x, a, b) is not Teradata)", r and "SUBSTR(ORI, 1, 2)" in r["sql"] and "SUBSTRING" not in r["sql"], r)
    check("a statement the guard passes is not rewritten", td.rewrite_sql(SID, CAST_OK) is None and td.rewrite_sql(SID, PROBE) is None, None)
    check("more than one statement declines; nothing recorded declines", td.rewrite_sql(SID, RANGE + "; " + RANGE) is None and td.rewrite_sql("other", RANGE) is None, None)
    check("the public findings carry no AST handles", all(not k.startswith("_") for f in td.sql_text_date_refs(SID, RANGE) for k in f), None)
    # TRYCAST fidelity: sqlglot regenerates a safe StrToDate as CAST — the rewrite must not re-arm 2666
    r = td.rewrite_sql(SID, "SELECT TRYCAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') d, COUNT(*) FROM NIBRS_Source_Domain.Incident WHERE INCDATE >= '2023-01-01' GROUP BY 1")
    check("a TRYCAST the author already wrote survives the rewrite as TRYCAST", r and "TRYCAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') AS d" in r["sql"], r)
    GUARDED = df.guarded_cast("INCDATE", "DD-MMM-YYYY")
    check("the guarded CAST (the safe form) is the right form, not a finding to refuse",
          td.text_date_violations(SID, f"SELECT {GUARDED} d, COUNT(*) FROM NIBRS_Source_Domain.Incident WHERE {GUARDED} >= DATE '2023-01-01' GROUP BY 1 ORDER BY 1") == ([], []), None)
    td._reset_all()
    td.record_text_dates(SID, [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)", "shape": "DD-MMM-YYYY", "sample": "31-OCT-2023", "unparseable": 0.003}]}])
    r = td.rewrite_sql(SID, RANGE)
    SAFE_I, SAFE = df.safe_cast("i.INCDATE", "DD-MMM-YYYY"), df.safe_cast("INCDATE", "DD-MMM-YYYY")
    check("a column the ontology says holds non-date values is rewritten with the SAFE read (the regex-free TRYCAST rebuild), never a plain CAST",
          r and f"{SAFE_I} >=" in r["sql"] and f"ORDER BY {SAFE} DESC" in r["sql"] and "REGEXP_SIMILAR" not in r["sql"], r)
    check("…and the guarded statement passes the guard", r and td.text_date_violations(SID, r["sql"]) == ([], []), None)
    r = td.rewrite_sql(SID, "SELECT CAST(INCDATE AS DATE FORMAT 'DD-MON-YYYY') FROM NIBRS_Source_Domain.Incident")
    check("…and a mismatched FORMAT becomes the safe read there too", r and r["sql"].startswith(f"SELECT {SAFE}"), r)
    td._reset_all()
    td.record_text_dates(SID, [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)", "shape": "DD-MMM-YYYY", "unparseable": 0.0, "unparseable_sampled": True}]}])
    r = td.rewrite_sql(SID, RANGE)
    check("a SAMPLED zero share is not a proof — the rewrite uses the cheap LENGTH guard (rendered verbatim, not sqlglot's LENGTH)",
          r and df.length_guarded_cast("i.INCDATE", "DD-MMM-YYYY") in r["sql"] and "LENGTH(TRIM" not in r["sql"].replace("CHAR_LENGTH", "") and "REGEXP_SIMILAR" not in r["sql"], r)
    check("…and the length-guarded statement passes the guard", r and td.text_date_violations(SID, r["sql"]) == ([], []), r)
    td._reset_all()
    td.record_text_dates(SID, [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)", "shape": "DD-MMM-YYYY", "unparseable": 0.0}]}])
    r = td.rewrite_sql(SID, RANGE)
    check("an EXACT zero share (no sampled flag) earns the plain CAST", r and "REGEXP_SIMILAR" not in r["sql"] and "TRYCAST" not in r["sql"] and "CHAR_LENGTH" not in r["sql"] and "CAST(i.INCDATE AS DATE FORMAT 'DD-MMM-YYYY') >=" in r["sql"], r)
    # the live turn of 2026-09-05: a select alias of the cast compared against bare strings (Error 2665)
    td._reset_all()
    td.record_text_dates(SID, [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)", "shape": "DD-MMM-YYYY", "unparseable": 0.0}]}])
    LIVE = (f"SELECT {GUARDED} AS incident_date, COUNT(*) AS n FROM NIBRS_Source_Domain.Incident i "
            "WHERE (i.V30061 = '7') AND incident_date BETWEEN '2023-01-01' AND '2023-12-31' GROUP BY 1 ORDER BY 1")
    p, u = td.text_date_violations(SID, LIVE)
    check("a FORMAT-bearing DATE (through its select alias) beside bare string literals is a PROVEN finding", u == [] and [f["kind"] for f in p] == ["date_expr_literal"] and "2023-01-01" in p[0]["snippet"], (p, u))
    r = td.rewrite_sql(SID, LIVE)
    check("…rewritten to typed DATE literals, the cast itself untouched", r and "BETWEEN CAST('2023-01-01' AS DATE) AND CAST('2023-12-31' AS DATE)" in r["sql"] and r["sql"].count("REGEXP_SIMILAR") == 1 and td.text_date_violations(SID, r["sql"]) == ([], []), r)
    p, u = td.text_date_violations(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident WHERE CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') >= '2023-01-01'")
    check("a direct cast beside a bare literal is the same finding", [f["kind"] for f in p] == ["date_expr_literal"], (p, u))
    check("a cast beside a typed DATE literal is not a finding", td.text_date_violations(SID, "SELECT 1 FROM NIBRS_Source_Domain.Incident WHERE CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') >= DATE '2023-01-01'") == ([], []), None)
    check("an alias not built on a recorded column is not judged", td.text_date_violations(SID, "SELECT CAST(ORI AS DATE FORMAT 'YYYY-MM-DD') AS d FROM NIBRS_Source_Domain.Incident WHERE d >= '2023-01-01'") == ([], []), None)
    td._reset_all()
    td.record_text_dates(SID, [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)", "shape": "DD-MMM-YYYY", "sample": "31-OCT-2023", "unparseable": 0.003}]}])
    old_mode = getattr(APP_CONFIG, "PREFLIGHT_TEXT_DATE_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "enforce"
        blk = guard.check_sql("base_readQuery", {"sql": PROD}, SID)
        check("the refusal names the unparseable share and shows the safe read, never the regex guard", blk and "0.3% of its non-blank values are NOT dates" in blk["data"] and df.safe_cast("INCDATE", "DD-MMM-YYYY") in blk["data"] and "REGEXP_SIMILAR" not in blk["data"], blk and blk["data"][:300])
    finally:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = old_mode

def t_optimize_strategy():
    import asyncio
    from trusted_data_agent.agent import phase_executor as pe
    _reset()
    class Ex:  # the strategy reads only the session id
        session_id = SID
    st = pe.TextDateStrategy(Ex())
    old = getattr(APP_CONFIG, "PREFLIGHT_TEXT_DATE_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "enforce"
        blk = guard.check_sql("base_readQuery", {"sql": ORDER}, SID)
        ctx = f"{blk.get('error_message', '')} {blk['data']}"      # what CorrectionHandler builds
        check("the strategy claims the guard's refusal and nothing else", st.can_handle(ctx) and not st.can_handle("[Error 3706] Syntax error"), ctx[:80])
        act = {"tool_name": "base_readQuery", "arguments": {"sql": ORDER}}
        check("shape unknown: it DECLINES (None, []) so the recovery-shaped refusal reaches the LLM", asyncio.run(st.generate_correction(act, blk)) == (None, []), None)
        td.learn_from_result(SID, PROBE, SAMPLE)
        blk = guard.check_sql("base_readQuery", {"sql": ORDER}, SID)
        corrected, events = asyncio.run(st.generate_correction(act, blk))
        check("shape known: the corrected action carries the CAST rewrite, the original is untouched",
              corrected and "ORDER BY CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') DESC" in corrected["arguments"]["sql"] and act["arguments"]["sql"] == ORDER, corrected)
        check("…and the corrected statement passes the guard", guard.check_sql("base_readQuery", corrected["arguments"], SID) is None, None)
        ev = events[0][0] if events else {}
        check("one System Self-Correction workaround event names the change and costs no LLM", ev.get("step") == "System Self-Correction" and ev.get("type") == "workaround"
              and ev["details"]["correction_type"] == "text_date_cast" and "no LLM round" in ev["details"]["summary"] and "→" in ev["details"]["summary"], ev)
        check("a SUBSTR bucket still declines to the LLM", asyncio.run(st.generate_correction({"tool_name": "base_readQuery", "arguments": {"sql": PROD}}, guard.check_sql("base_readQuery", {"sql": PROD}, SID))) == (None, []), None)
        check("non-string SQL declines, never raises", asyncio.run(st.generate_correction({"tool_name": "base_readQuery", "arguments": {"sql": None}}, blk)) == (None, []), None)
        names = [type(x).__name__ for x in pe.CorrectionHandler.__init__.__code__.co_names]
        src = pathlib.Path(pe.__file__).read_text()
        chain = src.split("self.strategies = [")[1].split("]")[0]
        pos = [chain.find(n) for n in ("TeradataLimitStrategy", "TextDateStrategy", "TableNotFoundStrategy")]
        check("the chain runs TextDateStrategy right after the limit strategy, before the LLM tiers", all(x >= 0 for x in pos) and pos[0] < pos[1] < pos[2], (pos, chain.strip()[:200]))
    finally:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = old

def t_react_rewrite():
    import asyncio, json
    from trusted_data_agent.agent import tool_enforcement as te
    _reset()
    calls = []
    async def tool(**kw):
        calls.append(kw["sql"]); return ([{"type": "text", "text": json.dumps({"status": "success", "results": [{"IncidentMonth": "2023-01", "n": 1}]})}], None)
    g = te._make_guarded_coroutine("base_readQuery", tool, SID)
    old = getattr(APP_CONFIG, "PREFLIGHT_TEXT_DATE_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "enforce"
        out = asyncio.run(g(sql=ORDER))
        check("shape unknown: the refusal is handed back as content and nothing ran", calls == [] and isinstance(out, tuple) and "not yet known" in str(out[0]) and PROBE in str(out[0]), str(out)[:200])
        td.learn_from_result(SID, PROBE, SAMPLE)
        out = asyncio.run(g(sql=ORDER))
        check("shape known: the tool ran the REWRITTEN statement, once", len(calls) == 1 and "ORDER BY CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') DESC" in calls[0], calls)
        blocks = out[0] if isinstance(out, tuple) and isinstance(out[0], list) else []
        check("the model receives the real result plus a note naming the rewrite and the executed SQL",
              len(blocks) >= 2 and blocks[0].get("text", "").startswith('{"status": "success"') and "DD-MMM-YYYY" in blocks[-1].get("text", "") and (calls[0] if calls else "\x00") in blocks[-1].get("text", ""), str(out)[:300])
        calls.clear()
        out = asyncio.run(g(sql=PROD))
        check("a SUBSTR bucket is still refused (judgement), nothing ran", calls == [] and "NOT executed" in str(out[0]), str(out)[:200])
        calls.clear()
        out = asyncio.run(g(sql=CAST_OK))
        check("a correct statement runs untouched, no note", calls == [CAST_OK] and len(out[0]) == 1, str(out)[:200])
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "observe"
        calls.clear(); out = asyncio.run(g(sql=ORDER))
        check("observe: byte-identical baseline — the original statement runs, no note", calls == [ORDER] and len(out[0]) == 1, calls)
        check("the rewrite helper ignores a non-text-date block and a missing SQL", te.text_date_rewrite("t", {"sql": ORDER}, {"preflight": True}, SID) is None and te.text_date_rewrite("t", {}, {"preflight_text_date": True}, SID) is None, None)
    finally:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = old

def t_planner_pass():
    import copy
    from trusted_data_agent.agent import planner as pl
    _reset()
    class Ex:
        session_id = SID
        meta_plan = None
    class Fake:
        executor = Ex()
    fake = Fake()
    plan = [
        {"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {"sql": ORDER}},
        {"phase": 2, "relevant_tools": ["base_readQuery"], "arguments": {"sql": PROD}},
        {"phase": 3, "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT * FROM NIBRS_Source_Domain.Incident WHERE ORI = result_of_phase_1 AND INCDATE >= '2023-01-01'"}},
        {"phase": 4, "relevant_tools": ["TDA_FinalReport"], "arguments": {}},
        "not-a-phase",
    ]
    old = getattr(APP_CONFIG, "PREFLIGHT_TEXT_DATE_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "enforce"
        fake.executor.meta_plan = copy.deepcopy(plan)
        list(pl.Planner._rewrite_plan_for_text_dates(fake))
        check("shape unknown: the plan is byte-identical", fake.executor.meta_plan == plan, None)
        td.learn_from_result(SID, PROBE, SAMPLE)
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "observe"
        fake.executor.meta_plan = copy.deepcopy(plan)
        list(pl.Planner._rewrite_plan_for_text_dates(fake))
        check("observe: shape known, the plan is STILL byte-identical (a rewrite is an act; observe only logs)", fake.executor.meta_plan == plan, fake.executor.meta_plan[0])
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "off"
        fake.executor.meta_plan = copy.deepcopy(plan)
        list(pl.Planner._rewrite_plan_for_text_dates(fake))
        check("off: byte-identical", fake.executor.meta_plan == plan, None)
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = "enforce"
        fake.executor.meta_plan = copy.deepcopy(plan)
        list(pl.Planner._rewrite_plan_for_text_dates(fake))
        got = fake.executor.meta_plan
        check("enforce, shape known: the mechanical phase is rewritten in place", "ORDER BY CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') DESC" in got[0]["arguments"]["sql"], got[0])
        check("the SUBSTR-bucket phase, the runtime-reference phase and the report phase are untouched", got[1:] == plan[1:], got[1:])
        fake.executor.meta_plan = None
        list(pl.Planner._rewrite_plan_for_text_dates(fake))
        check("a None plan never raises", True, None)
    finally:
        APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE = old
    src = pathlib.Path(pl.__file__).read_text()
    order = [src.find(f'_pass("{n}"') for n in ("sql_consolidation", "date_range_wiring", "text_date_cast", "plan_validation")]
    check("the pass runs after consolidation and date-range wiring and before validation", all(x >= 0 for x in order) and order == sorted(order), order)
    check("the three seams share ONE rewrite", src.count("_td.rewrite_sql(") == 1, None)

SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent"
def _calls_in(path, fn):
    tree = ast.parse(path.read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")) for c in ast.walk(n) if isinstance(c, ast.Call)}
    return None

def _via_gate(path, fn, gate, call):
    """``fn`` reaches ``call`` through the ONE SQL gate: it calls ``gate``, and ``gate`` (or a
    helper it calls in tool_enforcement) makes ``call``. The lanes share the gate, so the
    call lives there once (test_sql_gate_lanes pins the lanes' behaviour)."""
    te = SRC / "agent/tool_enforcement.py"
    reach = set(_calls_in(te, gate) or set())
    for helper in list(reach):
        reach |= (_calls_in(te, helper) or set())
    tree = ast.parse(te.read_text())
    for n in ast.walk(tree):           # a helper passed as a value (a loop over rewrites) counts too
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == gate:
            reach |= {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
    return gate in (_calls_in(path, fn) or set()) and call in reach

def t_wiring():
    check("check_sql runs the text-date check", "check_text_dates" in (_calls_in(SRC / "agent/preflight/guard.py", "check_sql") or set()), None)
    check("the executor records the columns where the enrichment event is stamped", "maybe_record_text_dates" in (_calls_in(SRC / "agent/executor.py", "_apply_trusted_scope") or set()), None)
    check("the ReAct chokepoint learns shapes after execution", "maybe_learn_date_shapes" in (_calls_in(SRC / "agent/tool_enforcement.py", "record_catalog") or set()), None)
    check("the ReAct guarded coroutine applies the mechanical rewrite on a refusal", _via_gate(SRC / "agent/tool_enforcement.py", "_guarded", "sql_gate_before", "text_date_rewrite"), None)
    check("the Optimize strategy and the ReAct twin share ONE rewrite", (SRC / "agent/phase_executor.py").read_text().count("_td.rewrite_sql(") == 1 and (SRC / "agent/tool_enforcement.py").read_text().count("_td.rewrite_sql(") == 1, None)
    check("the MCP-adapter chokepoint learns shapes after execution", _via_gate(SRC / "mcp_adapter/adapter.py", "_invoke_mcp_tool_impl", "sql_gate_after", "maybe_learn_date_shapes"), None)
    h = (pathlib.Path(__file__).resolve().parents[1] / "components/builtin/knowledge_graph/handler.py").read_text()
    check("the enrichment line and the metadata share ONE predicate", h.count("_is_text_date_attr(props)") >= 2, h.count("_is_text_date_attr(props)"))
    check("compose forwards the mode", "TDA_PREFLIGHT_TEXT_DATE_MODE" in (pathlib.Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(), None)

def t_slices():
    """A slice is a bucket; it is correct exactly when it picks out whole fields of the proven
    shape. Production 2026-09-05: SUBSTR(INCDATE, 4, 3) — the month of a DD-MMM-YYYY — was
    refused as a bucket, the model was steered to a per-row conversion that ran past the
    database's 110 s ceiling twice, and it then invented the monthly counts."""
    check("the fields of DD-MMM-YYYY with their positions", df.shape_fields("DD-MMM-YYYY") == [("DD", 1, 2), ("MMM", 4, 3), ("YYYY", 8, 4)], df.shape_fields("DD-MMM-YYYY"))
    check("the fields of the ISO shape and of a compact one", df.shape_fields("YYYY-MM-DD") == [("YYYY", 1, 4), ("MM", 6, 2), ("DD", 9, 2)] and df.shape_fields("YYYYMMDD") == [("YYYY", 1, 4), ("MM", 5, 2), ("DD", 7, 2)], None)
    check("SUBSTR(x, 4, 3) on DD-MMM-YYYY is the month: aligned", df.slice_aligned("DD-MMM-YYYY", 4, 3) is True, None)
    check("SUBSTR(x, 8, 4) is the year, SUBSTR(x, 8) (to the end) too", df.slice_aligned("DD-MMM-YYYY", 8, 4) is True and df.slice_aligned("DD-MMM-YYYY", 8, None) is True, None)
    check("SUBSTR(x, 4, 8) spans month AND year: aligned (a month-year)", df.slice_aligned("DD-MMM-YYYY", 4, 8) is True, None)
    check("SUBSTR(x, 1, 7) is '31-OCT-': cuts on a separator, NOT aligned", df.slice_aligned("DD-MMM-YYYY", 1, 7) is False, None)
    check("SUBSTR(x, 2, 2) cuts the day field: NOT aligned", df.slice_aligned("DD-MMM-YYYY", 2, 2) is False, None)
    check("SUBSTR(x, 1, 7) on ISO is the year-month: aligned", df.slice_aligned("YYYY-MM-DD", 1, 7) is True, None)
    check("an unknown shape or a computed slice settles nothing", df.slice_aligned(None, 1, 7) is None and df.slice_aligned("DD-MMM-YYYY", None, 3) is None and df.slice_aligned("DD-MMM-YYYY", 0, 3) is None, None)
    _reset(); td.learn_from_result(SID, PROBE, SAMPLE)
    MONTH = "SELECT SUBSTR(INCDATE, 4, 3) AS m, COUNT(*) AS n FROM NIBRS_Source_Domain.Incident WHERE INCDATE LIKE '%-2023' GROUP BY 1 ORDER BY 1"
    check("the production statement — an aligned month slice with a text year filter — PASSES", td.text_date_violations(SID, MONTH) == ([], []), td.text_date_violations(SID, MONTH))
    check("the aligned year slice passes; RIGHT(x, 4) is judged as before (a CHAR column is blank-padded — the real values read '31-OCT-2023           '); LEFT(x, 7) is proven wrong; LEFT(x, 6) (day-and-month, whole fields) passes",
          td.text_date_violations(SID, "SELECT SUBSTR(INCDATE, 8, 4) FROM NIBRS_Source_Domain.Incident") == ([], [])
          and td.text_date_violations(SID, "SELECT RIGHT(INCDATE, 4) FROM NIBRS_Source_Domain.Incident")[0][0]["kind"] == "substr"
          and td.text_date_violations(SID, "SELECT LEFT(INCDATE, 7) FROM NIBRS_Source_Domain.Incident")[0][0]["kind"] == "substr"
          and td.text_date_violations(SID, "SELECT LEFT(INCDATE, 6) FROM NIBRS_Source_Domain.Incident") == ([], []), None)
    check("the misaligned slice of the original production statement stays proven", {f["kind"] for f in td.text_date_violations(SID, PROD)[0]} == {"substr", "range_literal"}, None)
    check("a computed slice on a non-ISO shape is judged as before (violation)", td.text_date_violations(SID, "SELECT SUBSTR(INCDATE, 1, CHAR_LENGTH(INCDATE) - 4) FROM NIBRS_Source_Domain.Incident")[0][0]["kind"] == "substr", None)
    _reset()
    check("shape unknown: an aligned-looking slice is still UNVERIFIED (nothing proves the shape)", td.text_date_violations(SID, MONTH)[1][0]["kind"] == "substr", None)
    td.learn_from_result(SID, PROBE, SAMPLE)
    r = td.rewrite_sql(SID, "SELECT SUBSTR(INCDATE, 4, 3) AS m, COUNT(*) FROM NIBRS_Source_Domain.Incident WHERE INCDATE >= '2023-01-01' GROUP BY 1")
    check("the mechanical rewrite fixes the range literal and LEAVES the aligned month slice alone", r and "SUBSTR(INCDATE, 4, 3) AS m" in r["sql"] and "CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY') >= CAST('2023-01-01' AS DATE)" in r["sql"], r)

def t_cheap_forms():
    forms = df.text_period_forms("INCDATE", "DD-MMM-YYYY")
    check("the cheap forms of DD-MMM-YYYY: year, month, month number, day, month-year",
          forms["year"] == "SUBSTR(INCDATE, 8, 4)" and forms["month"] == "SUBSTR(INCDATE, 4, 3)" and forms["day"] == "SUBSTR(INCDATE, 1, 2)"
          and forms["month_year"] == "SUBSTR(INCDATE, 4, 8)" and forms["month_number"].startswith("(POSITION(UPPER(SUBSTR(INCDATE, 4, 3)) IN 'JANFEB"), forms)
    iso = df.text_period_forms("d", "YYYY-MM-DD")
    check("ISO: month is a number already (no month_number), year-month is the leading slice", "month_number" not in iso and iso["month_year"] == "SUBSTR(d, 1, 7)" and iso["year"] == "SUBSTR(d, 1, 4)", iso)
    check("no fields → nothing", df.text_period_forms("x", None) == {} and df.text_period_advice("x", None) == "", None)
    adv = df.text_period_advice("INCDATE", "DD-MMM-YYYY")
    check("the advice names the year filter, the month name→number expression and the cost of the CAST", "SUBSTR(INCDATE, 8, 4) = '2023'" in adv and "month number" in adv and "converts every scanned row" in adv, adv)
    _reset(); td.learn_from_result(SID, PROBE, SAMPLE)
    p, u = td.text_date_violations(SID, PROD)
    msg = td.block_result(p, u)["data"]
    check("the refusal of the production statement now names the cheap text forms BEFORE the CAST", "SUBSTR(INCDATE, 8, 4) = '2023'" in msg and msg.index("costs no per-row conversion") < msg.index("Rewrite every date comparison"), msg)
    # the measured chooser (12.35M-row Incident, 2026-09-05): plain 18 s · length guard 20 s · rebuilt-ISO TRYCAST 52 s · regex guard >170 s
    check("the safe read of DD-MMM-YYYY is a TRYCAST of the rebuilt ISO text, regex-free, and NULLs junk by TRYCAST semantics",
          df.safe_cast("INCDATE", "DD-MMM-YYYY").startswith("TRYCAST(SUBSTR(TRIM(INCDATE), 8, 4) || '-' || CASE UPPER(SUBSTR(TRIM(INCDATE), 4, 3)) WHEN 'JAN' THEN '01'")
          and df.safe_cast("INCDATE", "DD-MMM-YYYY").endswith("|| '-' || SUBSTR(TRIM(INCDATE), 1, 2) AS DATE)") and "REGEXP" not in df.safe_cast("INCDATE", "DD-MMM-YYYY"), df.safe_cast("INCDATE", "DD-MMM-YYYY"))
    check("a numeric-month shape rebuilds with slices only; ISO is a direct TRYCAST; a two-digit-year or time shape falls back to the regex guard",
          df.safe_cast("d", "MM/DD/YYYY") == "TRYCAST(SUBSTR(TRIM(d), 7, 4) || '-' || SUBSTR(TRIM(d), 1, 2) || '-' || SUBSTR(TRIM(d), 4, 2) AS DATE)"
          and df.safe_cast("d", "YYYY-MM-DD") == "TRYCAST(TRIM(d) AS DATE)"
          and df.safe_cast("d", "DD-MMM-YY") == df.guarded_cast("d", "DD-MMM-YY")
          and df.safe_cast("d", "YYYY-MM-DDBHH:MI:SS") == df.guarded_cast("d", "YYYY-MM-DDBHH:MI:SS"), None)
    check("the length guard compares CHAR_LENGTH(TRIM(col)) to the shape's width", df.length_guarded_cast("INCDATE", "DD-MMM-YYYY") == "CAST(CASE WHEN CHAR_LENGTH(TRIM(INCDATE)) = 11 THEN TRIM(INCDATE) END AS DATE FORMAT 'DD-MMM-YYYY')", None)
    check("the chooser: measured junk → safe read · sampled clean → length guard · exact clean → plain CAST · never the regex guard",
          df.date_read_form("c", "DD-MMM-YYYY", 0.003, True) == df.safe_cast("c", "DD-MMM-YYYY")
          and df.date_read_form("c", "DD-MMM-YYYY", 0.0, True) == df.length_guarded_cast("c", "DD-MMM-YYYY")
          and df.date_read_form("c", "DD-MMM-YYYY", 0.0, False) == "CAST(c AS DATE FORMAT 'DD-MMM-YYYY')"
          and df.date_read_form("c", "DD-MMM-YYYY", "x", True) == df.length_guarded_cast("c", "DD-MMM-YYYY")
          and "REGEXP" not in df.date_read_form("c", "DD-MMM-YYYY", 0.5, True), None)
    check("…and describes the misaligned slice as cutting across fields", "ACROSS its fields" in msg, msg)


def t_data_span():
    """A filter on a period the data does not cover is a PROVEN finding — the aligned year
    slice, the right CAST and EXTRACT(YEAR) are all correct in shape and all empty in fact.
    Found on the seeded Optimize twin: a champion's `= '2023'` rewritten by the planner to
    the CURRENT year returned no rows and read as "no robberies recorded"."""
    from components.builtin.knowledge_graph import handler as kgh
    import datetime as _dt
    Y = _dt.date.today().year
    class Cap:
        def __init__(self, d): self.deployed_as = d
    inc = {"label": "incident_date_text", "description": "INCIDENT DATE (MM/DD/YYYY)", "deployed_type": "CHAR(11)",
           "sampled_shape": "DD-MMM-YYYY", "sample_value": "29-JUN-2023", "date_unparseable_ratio": 0.0,
           "date_range": ["2023-01-01", "2023-12-31"], "date_range_sampled": True}
    sub = {"entities": [{"id": 1, "entity_type": "entity", "name": "Policing Core.Incident", "properties": {}},
                        {"id": 2, "entity_type": "attribute", "name": "Policing Core.Incident.incident_date_text", "properties": inc}],
           "relationships": [{"relationship_type": "contains", "source_name": "Policing Core.Incident", "target_name": "Policing Core.Incident.incident_date_text"}]}
    tdc = kgh._text_date_columns(sub, {"Policing Core.Incident": Cap("Policing_Core.v_Incident")})
    col = tdc[0]["columns"][0]
    check("the enrichment carries the measured span beside the shape", col.get("span") == ["2023-01-01", "2023-12-31"] and col.get("span_sampled") is True, col)
    td._reset_all(); td.record_from_event(SID, {"enrichments": [{"text_date_columns": tdc}]})
    sh = td.known_shape("v_incident", "incident_date_text")
    check("…and the guard stores it with the shape (a fact only the ontology can carry)", sh and sh.get("span") == ["2023-01-01", "2023-12-31"] and sh.get("span_sampled"), sh)

    T = "FROM Policing_Core.v_Incident i"
    def viol(sql):
        pv, uv = td.text_date_violations(SID, sql)
        return [g.get("out_of_span") for g in pv if g.get("out_of_span")], pv, uv
    # the live statement: the aligned year slice against the CURRENT year
    live = f"SELECT SUBSTR(i.incident_date_text, 4, 3) AS m, COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 8, 4) = CAST(EXTRACT(YEAR FROM CURRENT_DATE) AS VARCHAR(4)) GROUP BY 1"
    oos, pv, uv = viol(live)
    check("the aligned YEAR slice = the CURRENT year is out of span (proven, not unverified)", oos and oos[0]["selected"] == [Y, Y] and oos[0]["span"] == ["2023-01-01", "2023-12-31"] and not uv, (oos, uv))
    check("…and the finding says the span was sampled", oos and oos[0]["sampled"] is True, oos)
    check("a literal year outside the span", viol(f"SELECT COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 8, 4) = '{Y}'")[0], None)
    check("the right CAST compared against a DATE literal outside the span", viol(f"SELECT COUNT(*) {T} WHERE CAST(i.incident_date_text AS DATE FORMAT 'DD-MMM-YYYY') >= DATE '2026-01-01'")[0], None)
    check("BETWEEN two dates outside the span", viol(f"SELECT COUNT(*) {T} WHERE CAST(i.incident_date_text AS DATE FORMAT 'DD-MMM-YYYY') BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'")[0], None)
    check("EXTRACT(YEAR FROM the cast) = a year outside the span", viol(f"SELECT COUNT(*) {T} WHERE EXTRACT(YEAR FROM CAST(i.incident_date_text AS DATE FORMAT 'DD-MMM-YYYY')) = 2025")[0], None)
    check("the column on the RIGHT of the comparison is judged the same way", viol(f"SELECT COUNT(*) {T} WHERE '{Y}' = SUBSTR(i.incident_date_text, 8, 4)")[0], None)
    check("IN a list of years all outside the span", viol(f"SELECT COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 8, 4) IN ('2024', '2025')")[0], None)
    # the passes: inside, overlapping, undecidable
    check("the seed's own filter (= '2023') passes", not viol(f"SELECT COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 8, 4) = '2023'")[0] and not viol(f"SELECT COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 8, 4) = '2023'")[1], None)
    check("a range that OVERLAPS the span passes (>= 2022)", not viol(f"SELECT COUNT(*) {T} WHERE CAST(i.incident_date_text AS DATE FORMAT 'DD-MMM-YYYY') >= DATE '2022-06-01'")[0], None)
    check("an open range that reaches the span passes (<= 2023)", not viol(f"SELECT COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 8, 4) <= '2023'")[0], None)
    check("the MONTH slice carries no year → never judged", not viol(f"SELECT COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 4, 3) = 'JAN'")[0], None)
    check("ADD_MONTHS(CURRENT_DATE, …) is judgement → no verdict (fail-open)", not viol(f"SELECT COUNT(*) {T} WHERE CAST(i.incident_date_text AS DATE FORMAT 'DD-MMM-YYYY') >= ADD_MONTHS(CURRENT_DATE, -12)")[0], None)
    check("a comparison against another column → no verdict", not viol(f"SELECT COUNT(*) {T} WHERE SUBSTR(i.incident_date_text, 8, 4) = i.exceptional_clearance_date_text")[0], None)
    # no span recorded → the check is silent (the shape verdict alone stands)
    td._reset_all(); td.record_text_dates(SID, [{"table": "Policing_Core.v_Incident", "columns": [{"name": "incident_date_text", "type": "CHAR(11)", "shape": "DD-MMM-YYYY"}]}])
    check("without a measured span the same statement is NOT a finding (fail-open)", not viol(live)[0] and not viol(live)[1], viol(live))
    # the refusal + the correction chain
    td._reset_all(); td.record_from_event(SID, {"enrichments": [{"text_date_columns": tdc}]})
    pv, uv = td.text_date_violations(SID, live)
    br = td.block_result(pv, uv)
    msg = br["data"]
    check("the refusal names the span, the period and the remedy — and routes through the text-date strategy",
          "2023-01-01 to 2023-12-31" in msg and f"the year {Y}" in msg and "NOT executed" in msg
          and "SUBSTR(incident_date_text, 8, 4) = '2023'" in msg and "DATE stored as" in msg and "sampled" in msg, msg)
    check("…and says an empty result is a period the extract does not cover, not evidence of absence", "not evidence that nothing happened" in msg, msg)
    check("rewrite_sql declines it — which year the user meant is judgement", td.rewrite_sql(SID, live) is None, None)
    mixed = f"SELECT COUNT(*) {T} WHERE i.incident_date_text >= '{Y}-01-01'"
    check("a shape violation the rewrite COULD fix is still declined when the period is out of span", td.rewrite_sql(SID, mixed) is None, td.rewrite_sql(SID, mixed))
    pv, uv = td.text_date_violations(SID, f"SELECT COUNT(*) {T} WHERE i.incident_date_text >= '{Y}-01-01' AND SUBSTR(i.incident_date_text, 1, 7) = '01-JAN-'")
    check("the out-of-span finding leads the refusal over a shape finding in the same statement", td.block_result(pv, uv)["error"].startswith("Text-stored date incident_date_text filtered to a period outside"), td.block_result(pv, uv)["error"])

for fn in (t_shapes, t_refs, t_verdicts, t_learning, t_recording, t_mode_ladder, t_metadata_source, t_rewrite, t_optimize_strategy, t_react_rewrite, t_planner_pass, t_wiring, t_slices, t_cheap_forms, t_data_span):
    try: fn()
    except Exception as exc:
        import traceback; traceback.print_exc()
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
