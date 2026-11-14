"""
test_pdm_design_rules — PROP-008 increment A4: the design standard as rule records, data
probes before the primary-index choice, and the dictionary read back rule by rule.

  §1  rules are RECORDS: every rule carries an id, a rationale, a source from the closed set
      and either a verification statement or a readback; a rule with no executed verification
      reads unverified — never executed · changed since · another version — never a pass;
      the standard carries a version
  §2  probes: the facts a probe measures; the PI decision truth table — no key, no rows,
      synthetic (provisional, decides nothing), too few rows, a CONSTANT key demoted to the
      even foreign key or to NO PRIMARY INDEX with the reason named, a skewed key by the
      AMP bound, a key the rows show is not unique (non-unique PI + a finding), an even unique
      key kept; column refinements applied only on measured rows
  §3  readback: our own CREATE parsed back; the per-rule truth table (held · violated ·
      unverified), unverified never lowers the status, a foreign object is violated, the
      issue shape the conformance gate uses, the summary in words
  §4  the dialect base carries NO Teradata rule (vocabulary scan); the Teradata emitter
      honours a measured override and is byte-identical without one
  §5  the delta plan carries the standard and the decisions; a demoted hook drops UNIQUE and
      names it; the receipt seals the standard and the decisions
  §6  the orchestrator on the REAL GraphStore: a dry run VALIDATES (status, validated_at,
      standard), a landing stamps the readback on the node, the standard on the physical
      nodes and both into the receipt; a backend whose dictionary disagrees reads violated,
      named; an object the platform's sibling holds that the model did not build is violated
  §7  the PDM path: synthetic rows → every decision provisional, the DDL byte-identical to a
      convert without probes; shipped rows with a constant key → the key demoted, the DDL
      re-emitted without UNIQUE and the report naming it; the readback in the deploy report
  §8  live, under TDA_LIVE_TERADATA=1: every rule's verification statement executes on the
      target version; a constant-column declared key is demoted by the probes and its
      distribution read back as violated; a conformant table reads every rule held
  §9–§14  PROP-008 C3 — a second backend: the base carries neither backend's vocabulary nor
      name; PostgreSQL's OWN rule set + verification file (verified on its target, never by
      the Teradata record); one authored node planned on both backends; the PostgreSQL
      readback rule by rule; the orchestrator landing the SAME node on Teradata and on
      PostgreSQL (one physical layer, both realisations, a receipt each, both re-runs no-ops);
      the backend chosen by the connection's driver; live under TDA_LIVE_POSTGRES=1

Run: PYTHONPATH=src python3 test/test_pdm_design_rules.py
"""
import asyncio
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
def run(fn):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc()
        check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")
def _run(x): return asyncio.run(x) if asyncio.iscoroutine(x) else x

os.environ.pop("TDA_ACT", None)
from trusted_data_agent.dbdeploy import design_standard as DS
from trusted_data_agent.dbdeploy import probes as PR
from trusted_data_agent.dbdeploy import readback as RB
from trusted_data_agent.dbdeploy import delta as D
from trusted_data_agent.dbdeploy.backend import DeploymentBackend
from trusted_data_agent.dbdeploy.capabilities import DeploymentCapability, REQUIRED_CAPABILITIES
from trusted_data_agent.dbdeploy.types import DeployResult

SYS = "Teradata 20.00.24.60"

print("[§1] rules as records")
def t_rules():
    check("the standard carries a version", DS.DEFAULT_TERADATA_STANDARD.version == DS.STANDARD_VERSION and re.match(r"^teradata/\d+\.\d+$", DS.STANDARD_VERSION), DS.STANDARD_VERSION)
    check("thirteen rules, every id unique", len(DS.RULES) == len({r.id for r in DS.RULES}) == 13)
    check("every rule carries a rationale and a source from the closed set", all(r.rationale and r.source in DS.RULE_SOURCES for r in DS.RULES))
    check("every rule is provable: a verification statement, a readback, or both", all(r.verification or r.readback for r in DS.RULES))
    check("the measured rules are the ones that only the data can settle", {r.id for r in DS.RULES if r.source == DS.MEASURED} == {"comment_bounded", "pi_even_distribution", "pi_unique"})
    rep = DS.rules_report(SYS, record={})
    check("with no record, every rule with a statement reads unverified · never executed", rep["verified"] == 0 and all(x["status"] == "unverified" for x in rep["rules"]) and any("never executed" in x["note"] for x in rep["rules"]), rep["rules"][0])
    full = {"system": SYS, "on": "2026-10-06", "rules": {r.id: {"statements": [DS.statement_sha(v) for v in r.verification]} for r in DS.RULES}}
    rep2 = DS.rules_report(SYS, record=full)
    check("with a record of every statement on the same version, every rule reads verified", rep2["verified"] == 13 and rep2["unverified"] == 0, [x for x in rep2["rules"] if x["status"] != "verified"])
    changed = {**full, "rules": {**full["rules"], "multiset": {"statements": ["deadbeef"]}}}
    st, why = DS.rule_verification_status(DS.RULES_BY_ID["multiset"], changed, SYS)
    check("a statement that changed since the recorded run reads unverified, saying how many", st == "unverified" and "changed since" in why, why)
    st2, why2 = DS.rule_verification_status(DS.RULES_BY_ID["multiset"], full, "Teradata 17.20.03.19")
    check("a record from another version family reads unverified, naming both", st2 == "unverified" and "17.20" in why2 and "20.00" in why2, why2)
    st3, _ = DS.rule_verification_status(DS.RULES_BY_ID["multiset"], full, "Teradata 20.00.99.01")
    check("a patch release of the same family does not un-verify", st3 == "verified")
    check("the verification record is read from beside the module, missing → empty (unverified, never a pass)", DS.load_verification("/nonexistent/x.json") == {})
    check("a rule's dict carries no statement text, only its hash (the record's shape)", all(len(h) == 64 for h in DS.RULES_BY_ID["soft_ri"].to_dict()["verification"]))
run(t_rules)


print("\n[§2] probes and the PI decision")
def rows(n, key=None, c=None, s=None):
    return [{"k": (key(i) if key else i), "c": (c(i) if c else i % 7), "s": (s(i) if s else f"v{i}")} for i in range(n)]
COLS, TYPES = ["k", "c", "s"], {"k": "INTEGER", "c": "INTEGER", "s": "VARCHAR(40)"}
def t_probe_facts():
    # NB "ü" is 0xFC — INSIDE LATIN (ISO 8859-1); a CJK character is what needs UNICODE
    p = PR.probe_table("db.t", COLS, rows(40, s=lambda i: "" if i % 4 == 0 else ("日" if i == 1 else "v")), key=["k"], provisional=False, fks_out=["c"], types=TYPES)
    cs = p.columns["s"]
    check("a column probe measures nulls (blank is null), distinct, width, non-LATIN, the top share", cs.nulls == 10 and cs.null_ratio == 0.25 and cs.distinct == 2 and cs.max_width == 1 and cs.non_latin and abs(cs.top_share - 29 / 40) < 1e-9, cs.to_dict())
    check("the key probe: unique in 40 rows, every value once", p.key_unique is True and p.key_distinct == 40 and abs(p.key_top_share - 1 / 40) < 1e-9)
    check("the role comes from the catalog intelligence (one rule), with reasons", p.role.get("role") in ("fact", "dimension", "bridge", "lookup", "unknown") and p.role.get("reasons"), p.role)
    check("the dict is JSON-shaped and names low cardinality where it applies", p.to_dict()["columns"]["c"]["low_cardinality"] is True and p.to_dict()["columns"]["k"]["low_cardinality"] is False)
run(t_probe_facts)

def t_choose_pi():
    base = rows(60)
    even = PR.probe_table("db.t", COLS, base, key=["k"], provisional=False, fks_out=["c"], types=TYPES)
    d = PR.choose_pi("db.t", ["k"], COLS, even, amps=4, fk_cols=["c"])
    check("an even, unique declared key is kept — UNIQUE, rule declared_key, the measurement named", d.columns == ["k"] and d.unique and d.rule == "declared_key" and "unique in 60 rows" in d.reason and d.skew_lower_bound_pct is not None and d.skew_lower_bound_pct <= 10, d.to_dict())
    check("no declared key → NO PRIMARY INDEX, rule no_key", PR.choose_pi("db.t", [], COLS, even).rule == "no_key" and PR.choose_pi("db.t", ["zz"], COLS, even).nopi)
    check("no probe (an empty new table) → the declared key, said so", PR.choose_pi("db.t", ["k"], COLS, None).rule == "declared_key" and "no rows" in PR.choose_pi("db.t", ["k"], COLS, None).reason)
    const = PR.probe_table("db.t", COLS, rows(60, key=lambda i: 1), key=["k"], provisional=False, fks_out=["c"], types=TYPES)
    syn = PR.probe_table("db.t", COLS, rows(60, key=lambda i: 1), key=["k"], provisional=True, fks_out=["c"], types=TYPES)
    ds = PR.choose_pi("db.t", ["k"], COLS, syn, amps=4, fk_cols=["c"])
    check("SYNTHETIC rows decide nothing: the same constant key stays the declared key, marked provisional, the bound still recorded", ds.rule == "provisional_declared_key" and ds.provisional and ds.columns == ["k"] and ds.unique and ds.skew_lower_bound_pct and ds.skew_lower_bound_pct >= 300, ds.to_dict())
    few = PR.probe_table("db.t", COLS, rows(5, key=lambda i: 1), key=["k"], provisional=False, types=TYPES)
    check("too few measured rows decide nothing either, saying how many", PR.choose_pi("db.t", ["k"], COLS, few).rule == "declared_key" and "5 rows" in PR.choose_pi("db.t", ["k"], COLS, few).reason)
    # the constant key, measured: demoted to the even FK (7 distinct ≥ 4 amps, top share 1/7 → bound ≈ -43 → 0)
    dc = PR.choose_pi("db.t", ["k"], COLS, const, amps=4, fk_cols=["c"])
    check("a CONSTANT declared key is DEMOTED to the even foreign key, non-unique, the reason naming the value and the key", dc.rule == "demoted_skew" and dc.columns == ["c"] and not dc.unique and dc.demoted_from == ["k"] and "every row carries the same value (1)" in dc.reason and "foreign key c" in dc.reason and dc.findings, dc.to_dict())
    dn = PR.choose_pi("db.t", ["k"], COLS, const, amps=4, fk_cols=[])
    check("…with no even candidate it is NO PRIMARY INDEX, still named", dn.rule == "demoted_skew" and dn.nopi and "no even candidate" in dn.reason)
    dn2 = PR.choose_pi("db.t", ["k"], COLS, const, amps=None, fk_cols=["c"])
    check("…a constant key is total skew on ANY system — demoted even when the AMP count is unknown", dn2.rule == "demoted_skew")
    # a skewed (not constant) key: 60 rows, 50 share one value → bound (50/60·4 − 1)·100 = 233 %
    skew = PR.probe_table("db.t", COLS, rows(60, key=lambda i: 1 if i < 50 else i), key=["k"], provisional=False, fks_out=["c"], types=TYPES)
    dk = PR.choose_pi("db.t", ["k"], COLS, skew, amps=4, fk_cols=["c"])
    check("a skewed key is demoted by the AMP bound, the share named", dk.rule == "demoted_skew" and "83% of the rows carry one value" in dk.reason and dk.skew_lower_bound_pct and dk.skew_lower_bound_pct > 200, dk.to_dict())
    check("…with the AMP count unknown a non-constant skew cannot be bounded — but the same key is still measured NOT UNIQUE (a skewed key repeats)", PR.choose_pi("db.t", ["k"], COLS, skew, amps=None, fk_cols=["c"]).rule == "declared_key_not_unique")
    dup = PR.probe_table("db.t", COLS, rows(60, key=lambda i: i // 2), key=["k"], provisional=False, types=TYPES)
    dd = PR.choose_pi("db.t", ["k"], COLS, dup, amps=4)
    check("a key the rows show is NOT unique keeps the PI but drops UNIQUE, with a finding against the declaration", dd.rule == "declared_key_not_unique" and dd.columns == ["k"] and not dd.unique and "30 duplicate rows" in dd.reason and dd.findings and "not unique" in dd.findings[0], dd.to_dict())
    check("the skew bound: a constant is total skew, 1/amps is even, unknown amps bounds nothing but a constant",
          PR.skew_lower_bound_pct(1.0, 4) == 300.0 and PR.skew_lower_bound_pct(0.25, 4) == 0.0 and PR.skew_lower_bound_pct(0.5, None) is None and PR.skew_lower_bound_pct(1.0, None) > 1000)
    rec = {}
    hook = PR.as_delta_hook({"db.t": const}, amps=4, fk_cols_by_table={"db.t": ["c"]}, decisions=rec)
    check("the delta hook adapts the decision to (cols, rule) and records it", hook("db.t", ["k"], COLS) == (["c"], "demoted_skew") and rec["db.t"].rule == "demoted_skew")
run(t_choose_pi)

def t_refine():
    meas = PR.probe_table("db.t", COLS, rows(60, s=lambda i: "日" if i == 1 else "v"), key=["k"], provisional=False, types=TYPES)
    r = PR.refine_columns(meas, char_columns=["s"], key_cols=["k"])
    check("measured rows: NOT NULL where no row was null (never on the key), UNICODE where the rows need it, applied", r["s"].not_null is True and r["s"].charset == "UNICODE" and r["s"].applied and "k" not in r, {k: v.to_dict() for k, v in r.items()})
    check("…a width is recorded, never applied as a decision", r["s"].suggested_width == 1 and "suggested_width" in r["s"].to_dict())
    check("…low cardinality is a recorded compression candidate (the recommender's job) — recorded, not a charset or width decision", r["c"].compression_candidate and r["c"].charset is None and r["c"].suggested_width is None)
    syn = PR.probe_table("db.t", COLS, rows(60, s=lambda i: "日"), key=["k"], provisional=True, types=TYPES)
    rs = PR.refine_columns(syn, char_columns=["s"])
    check("synthetic rows: the same facts are recorded and NOTHING is applied", rs["s"].charset == "UNICODE" and rs["s"].applied is False)
run(t_refine)


print("\n[§3] the readback")
CREATE = ('CREATE MULTISET TABLE "db"."t", NO FALLBACK\n(\n    "k" INTEGER NOT NULL,\n    "c" INTEGER,\n'
          '    "s" VARCHAR(40) CHARACTER SET LATIN,\n    "f" BYTEINT NOT NULL DEFAULT 0\n)\nUNIQUE PRIMARY INDEX ( "k" );')
FK = 'ALTER TABLE "db"."t" ADD FOREIGN KEY ( "c" ) REFERENCES WITH NO CHECK OPTION "db"."p" ( "c" );'
STATS = ['COLLECT STATISTICS COLUMN ( "k" ) ON "db"."t";', 'COLLECT STATISTICS COLUMN ( "c" ) ON "db"."t";']
COMMENT = "COMMENT ON TABLE \"db\".\"t\" IS 'a table''s comment';"
def planned():
    return RB.planned_from_sql(CREATE, key_columns=["k"], fk_sql=[FK], statistics_sql=STATS, comment_sql=COMMENT)
def observed(**over):
    o = {"multiset": True, "fallback": False, "pi_columns": ["k"], "pi_unique": True, "nopi": False,
         "columns": {"k": {"type": "I", "nullable": False, "charset": None}, "c": {"type": "I", "nullable": True, "charset": None},
                     "s": {"type": "CV", "nullable": True, "charset": "LATIN"}, "f": {"type": "I1", "nullable": False, "charset": None}},
         "foreign_keys": [("c", "db.p", "c")], "statistics": [["k"], ["c"]], "comment": "a table's comment", "pi_unique_in_data": True}
    o.update(over); return o
def t_planned():
    p = planned()
    check("our own CREATE reads back: MULTISET, NO FALLBACK, the columns with NOT NULL + charset, the UNIQUE PI, the key",
          p and p.multiset and not p.fallback and p.pi_columns == ["k"] and p.pi_unique and not p.nopi and p.key_columns == ["k"]
          and p.columns["k"]["not_null"] and p.columns["s"]["charset"] == "LATIN" and p.columns["c"]["not_null"] is False and p.columns["k"]["type"] == "INTEGER", p and p.to_dict())
    check("…and its sibling statements: the soft RI, the statistics, the comment (quotes unescaped)", p.foreign_keys == [("c", "db.p", "c")] and p.statistics == [["k"], ["c"]] and p.comment == "a table's comment")
    nopi = RB.planned_from_sql('CREATE MULTISET TABLE "db"."e", NO FALLBACK\n(\n    "v" VARCHAR(10) CHARACTER SET LATIN\n)\nNO PRIMARY INDEX;')
    check("a NoPI table reads back as such", nopi.nopi and not nopi.pi_columns)
    check("a statement that is not our CREATE yields nothing", RB.planned_from_sql("DROP TABLE x;") is None)
run(t_planned)

def t_report():
    p = {"db.t": planned()}
    rep = RB.readback_report(p, {"db.t": observed()}, skew={"db.t": {"skew_pct": 4.0, "total_rows": 60}}, system=SYS)
    check("everything as planned → every applicable rule held, conformant, the summary in words",
          rep.status == "conformant" and rep.violated == 0 and rep.unverified == 0 and rep.held == len([r for r in DS.RULES if r.readback and r.applies != "without_key"]) and rep.summary().startswith(f"{rep.held} rule checks held") and rep.summary().endswith("conformant"), rep.to_dict())
    bad = RB.readback_report(p, {"db.t": observed(pi_columns=["c"], pi_unique=False)}, system=SYS)
    row = next(r for r in bad.rows if r.rule == "upi_from_key")
    check("a primary index the dictionary shows on another column is VIOLATED (high) and nonconformant, naming both", bad.status == "nonconformant" and row.status == "violated" and "primary index on c, planned k" in row.detail, row.to_dict())
    low = RB.readback_report(p, {"db.t": observed(statistics=[["k"]])}, skew={"db.t": {"skew_pct": 1.0, "total_rows": 60}}, system=SYS)
    check("a missing statistic is a LOW violation → partial, the columns named", low.status == "partial" and any(r.rule == "collect_statistics" and r.status == "violated" and "(c)" in r.detail for r in low.rows))
    unv = RB.readback_report(p, {"db.t": observed(multiset=None, statistics=None, comment=None)}, system=SYS)
    check("a fact the backend could not read is UNVERIFIED — counted and listed, never lowering the status", unv.status == "conformant" and unv.unverified >= 3 and {r.rule for r in unv.rows if r.status == "unverified"} >= {"multiset", "collect_statistics", "comment_bounded"} and "unverified" in unv.summary())
    none = RB.readback_report(p, {}, system=SYS)
    check("a table the dictionary did not return: every applicable rule unverified, the table named as unread, the status UNVERIFIED (never a vacuous conformant)", none.tables_unread == ["db.t"] and all(r.status == "unverified" for r in none.rows) and none.held == 0 and none.status == "unverified" and "not read back" in none.summary() and none.summary().endswith("unverified"))
    sk = RB.readback_report(p, {"db.t": observed()}, skew={"db.t": {"skew_pct": 300.0, "total_rows": 60}}, system=SYS)
    r2 = next(r for r in sk.rows if r.rule == "pi_even_distribution")
    check("a measured skew above the threshold is VIOLATED high, the fullest AMP's excess named", sk.status == "nonconformant" and r2.status == "violated" and "300% more than the average on k" in r2.detail, r2.to_dict())
    nosk = RB.readback_report(p, {"db.t": observed()}, system=SYS)
    check("…no measurement → unverified, never held", next(r for r in nosk.rows if r.rule == "pi_even_distribution").status == "unverified")
    fo = RB.readback_report(p, {"db.t": observed()}, skew={"db.t": {"skew_pct": 0, "total_rows": 60}}, foreign_objects=["db.Stranger"], system=SYS)
    check("an object in the model's database the model did not build is a VIOLATED high row → nonconformant, in the summary", fo.status == "nonconformant" and any(r.table == "db.Stranger" and r.rule == "foreign_object" for r in fo.rows) and "the model did not build" in fo.summary())
    iss = bad.issues()
    check("issues wear the conformance gate's shape — code · severity · message · count · refs — worst first", iss and set(iss[0]) >= {"code", "severity", "message", "count", "refs"} and iss[0]["severity"] == "high" and iss[0]["code"] == "upi_from_key")
    ch = RB.readback_report(p, {"db.t": observed(columns={**observed()["columns"], "s": {"type": "CV", "nullable": True, "charset": "UNICODE"}})}, system=SYS)
    check("a charset the dictionary shows differently is violated (low), both named", any(r.rule == "charset_latin" and r.status == "violated" and "s is UNICODE, planned LATIN" in r.detail for r in ch.rows) and ch.status == "partial")
    kn = RB.readback_report(p, {"db.t": observed(columns={**observed()["columns"], "k": {"type": "I", "nullable": True, "charset": None}})}, system=SYS)
    check("a nullable key column is violated (high)", any(r.rule == "key_not_null" and r.status == "violated" and "k" in r.detail for r in kn.rows) and kn.status == "nonconformant")
    nopi_p = {"db.e": RB.planned_from_sql('CREATE MULTISET TABLE "db"."e", NO FALLBACK\n(\n    "v" VARCHAR(10) CHARACTER SET LATIN\n)\nNO PRIMARY INDEX;')}
    np_rep = RB.readback_report(nopi_p, {"db.e": {"multiset": True, "fallback": False, "pi_columns": [], "nopi": True, "columns": {"v": {"nullable": True, "charset": "LATIN"}}, "foreign_keys": [], "statistics": [], "comment": None}}, system=SYS)
    check("a keyless NoPI table: the without-key rule applies (held), the with-key rules (UPI, uniqueness, distribution) do not",
          any(r.rule == "nopi_without_key" and r.status == "held" for r in np_rep.rows) and not any(r.rule in ("upi_from_key", "pi_unique", "pi_even_distribution") for r in np_rep.rows), np_rep.to_dict()["rows"])
    # a key DECLARED but demoted to NoPI (the probes' call): the with-key rules apply, and NoPI holds them by construction
    dem = RB.planned_from_sql('CREATE MULTISET TABLE "db"."d", NO FALLBACK\n(\n    "k" INTEGER NOT NULL\n)\nNO PRIMARY INDEX;', key_columns=["k"])
    dem_rep = RB.readback_report({"db.d": dem}, {"db.d": {"multiset": True, "fallback": False, "pi_columns": [], "nopi": True, "columns": {"k": {"nullable": False}}, "foreign_keys": [], "statistics": [], "comment": None}}, system=SYS)
    check("a declared key demoted to NoPI: the primary-index rule reads NO PRIMARY INDEX as planned, the distribution held by construction",
          any(r.rule == "upi_from_key" and r.status == "held" and "NO PRIMARY INDEX" in r.detail for r in dem_rep.rows) and any(r.rule == "pi_even_distribution" and r.status == "held" and "by construction" in r.detail for r in dem_rep.rows), dem_rep.to_dict()["rows"])
    nu = RB.readback_report(p, {"db.t": observed(pi_unique_in_data=False)}, skew={"db.t": {"skew_pct": 0, "total_rows": 60}}, system=SYS)
    check("a UNIQUE PI that is not unique in the data is violated (high)", any(r.rule == "pi_unique" and r.status == "violated" for r in nu.rows) and nu.status == "nonconformant")
run(t_report)


print("\n[§4] the dialect base carries no Teradata rule; the emitter honours a measured decision")
def t_dialect():
    base = open(os.path.join(ROOT, "src/trusted_data_agent/dbdeploy/dialects/base.py")).read()
    vocab = ["MULTISET", "FALLBACK", "BYTEINT", "LATIN", "PRIMARY INDEX", "COLLECT STATISTICS", "NO CHECK OPTION", "TIMESTAMP(6)", "HASHAMP", "DBC."]
    hits = [v for v in vocab if v.lower() in base.lower()]
    check("the dialect base carries no Teradata rule vocabulary", not hits, hits)
    check("…and declares the measured-decision inputs as neutral facts (pi_overrides, column_refinements)", "pi_overrides" in base and "column_refinements" in base)
    import json
    from trusted_data_agent.dbdeploy import convert
    doc = json.load(open(os.path.join(HERE, "fixtures", "industry_model_pdm_sample.json")))
    kw = dict(industry="acme", flavour="mvm", logical_db_name="acme_core", root_database="acme_core")
    a = convert(doc, **kw); b = convert(doc, **kw, pi_overrides={}, column_refinements={})
    check("the converter is byte-identical with and without empty overrides (the baseline)", a.ddl.sql_script() == b.ddl.sql_script() and a.kg_delta.entities == b.kg_delta.entities)
    c = convert(doc, **kw, pi_overrides={"acme_core.order": {"columns": ["customer_id"], "unique": False}},
                column_refinements={"acme_core.order.order_date": {"not_null": True, "charset": None}, "acme_core.customer.full_name": {"not_null": None, "charset": "UNICODE"}})
    sql = next(s.sql for s in c.ddl.by_kind("table") if s.identifier == "acme_core.order")
    check("a demoted key emits a NON-UNIQUE primary index on the foreign key", 'PRIMARY INDEX ( "customer_id" )' in sql and "UNIQUE PRIMARY INDEX" not in sql, sql)
    check("…a measured NOT NULL and UNICODE land on their columns", '"order_date" DATE NOT NULL' in sql and 'CHARACTER SET UNICODE' in next(s.sql for s in c.ddl.by_kind("table") if s.identifier == "acme_core.customer"))
    stats = [s.sql for s in c.ddl.by_kind("statistics") if s.identifier == "acme_core.order"]
    check("statistics follow the decided PI and still cover the demoted key (a filter column)", any('( "customer_id" )' in s for s in stats) and any('( "order_id" )' in s for s in stats), stats)
    check("…an untouched table is byte-identical", next(s.sql for s in c.ddl.by_kind("table") if s.identifier == "acme_core.order_line") == next(s.sql for s in a.ddl.by_kind("table") if s.identifier == "acme_core.order_line"))
run(t_dialect)


print("\n[§5] the delta plan carries the standard and the decisions")
LEAD = {"name": "fitness_db.Lead", "props": {"business_key": "lead_id", "description": "d"},
        "attributes": [{"name": "fitness_db.Lead.lead_id", "props": {"data_type": "int"}}, {"name": "fitness_db.Lead.CustomerID", "props": {"data_type": "int"}}],
        "parent": "fitness_db.Customers", "deployed": None}
FACTS = {"fitness_db.Customers": {"deployed": "fitness_db.Customers", "key": ["CustomerID"]}}
LANDING = D.landing_for("fitness_db", True)
def t_delta():
    p = D.plan_delta({"entities": [LEAD], "attributes": []}, FACTS, {"databases": {"fitness_db"}, "tables": {"fitness_db.Customers": {"columns": {"CustomerID": "I"}, "foreign_keys": []}}}, LANDING)
    check("the plan carries the standard's version and the declared-key decision per table", p.standard == DS.STANDARD_VERSION and p.pi_decisions == {"fitness_db.Lead": {"columns": ["lead_id"], "rule": "declared_key"}} and p.to_dict()["standard"] == DS.STANDARD_VERSION, p.to_dict())
    check("…UNIQUE on the declared key", 'UNIQUE PRIMARY INDEX ( "lead_id" )' in p.sql_script())
    hook = lambda t, k, c: (["CustomerID"], "demoted_skew")  # noqa: E731
    q = D.plan_delta({"entities": [LEAD], "attributes": []}, FACTS, {"databases": {"fitness_db"}, "tables": {"fitness_db.Customers": {"columns": {"CustomerID": "I"}, "foreign_keys": []}}}, LANDING, choose_pi=hook)
    check("a demoted decision drops UNIQUE and is recorded", 'PRIMARY INDEX ( "CustomerID" )' in q.sql_script() and "UNIQUE" not in q.sql_script() and q.pi_decisions["fitness_db.Lead"]["rule"] == "demoted_skew")
    nop = lambda t, k, c: ([], "demoted_skew")  # noqa: E731
    r = D.plan_delta({"entities": [LEAD], "attributes": []}, FACTS, {"databases": {"fitness_db"}, "tables": {}}, LANDING, choose_pi=nop)
    check("a demotion to NoPI is NAMED in the warnings (not read as 'no key')", "NO PRIMARY INDEX" in r.sql_script() and any("demoted (demoted_skew)" in w for w in r.warnings) and not any("no declared key" in w for w in r.warnings), r.warnings)
    rc = {"receipt_id": "x", "kind": "model_change", "standard": DS.STANDARD_VERSION, "pi_decisions": {"a": {"rule": "declared_key"}}}
    rc2 = {**rc, "pi_decisions": {"a": {"rule": "demoted_skew"}}}
    check("the receipt's sealed content includes the standard and the decisions (a different decision → a different seal)", D.receipt_content(rc) != D.receipt_content(rc2) and DS.STANDARD_VERSION in D.receipt_content(rc))
run(t_delta)


print("\n[§6] the orchestrator on the real GraphStore")
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.kgstore import factory as _factory
from components.builtin.knowledge_graph.graph_store import GraphStore
from trusted_data_agent.auth.database import _migrate_to_multi_kg
_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP); _c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read()); _migrate_to_multi_kg(_c); _c.commit(); _c.close()
APP_CONFIG.AUTH_DB_PATH = TMP; APP_CONFIG.KG_SEMANTIC_SEEDING = False
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})
from trusted_data_agent.agent.kg_constructors import authored_deploy as AD
def with_switch(value, fn):
    old = _OP._FORCED["act"]; _OP.force_act(value)
    try: return fn()
    finally: _OP.force_act(old)

def graph(kg, rootdb="fitness_db"):
    S = GraphStore("p1", "u1", kg_id=kg, db_path=TMP); S.set_kg_metadata(kg, kg, "a4", is_active=False)
    _run(S.import_bulk(
        [{"name": rootdb, "entity_type": "database", "properties": {"layer": "logical"}},
         {"name": f"{rootdb}.Customers", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "CustomerID", "deployed_table": f"{rootdb}.Customers"}},
         {"name": f"{rootdb}.Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical"}},
         {"name": rootdb, "entity_type": "schema", "properties": {"layer": "physical", "infrastructure": "teradata"}},
         {"name": f"{rootdb}.Customers", "entity_type": "table", "properties": {"layer": "physical", "infrastructure": "teradata"}}],
        [{"source_name": rootdb, "source_type": "database", "target_name": f"{rootdb}.Customers", "target_type": "entity", "relationship_type": "contains"},
         {"source_name": f"{rootdb}.Customers", "source_type": "entity", "target_name": f"{rootdb}.Customers.CustomerID", "target_type": "attribute", "relationship_type": "contains"},
         {"source_name": f"{rootdb}.Customers", "source_type": "table", "target_name": f"{rootdb}.Customers", "target_type": "entity", "relationship_type": "implements"}]))
    def mk():
        lid = _run(S.add_entity(name=f"{rootdb}.Lead", entity_type="entity", properties={"layer": "authored", "status": "draft", "business_key": "lead_id", "description": "a lead"}, source="authored"))
        for a, t in (("lead_id", "int"), ("status", "VARCHAR(20)"), ("CustomerID", "int")):
            aid = _run(S.add_entity(name=f"{rootdb}.Lead.{a}", entity_type="attribute", properties={"layer": "authored", "status": "draft", "data_type": t}, source="authored"))
            _run(S.add_relationship(lid, aid, "contains", source="authored"))
        _run(S.add_relationship(lid, S.get_entity_by_name(f"{rootdb}.Customers", "entity")["id"], "relates_to", metadata={"kind": "belongs_to"}, source="authored"))
    with_switch(True, mk)
    return S

class FakeTD(DeploymentBackend):
    """The A3 fake (a dictionary it mutates, the statements it reports) extended with the A4
    reads: the AMP count, the system version, the design facts of a table as the dictionary
    would show them (faithful to what the emitted DDL created unless ``disagree`` says the
    warehouse holds something else), the skew measured on a PI, the objects of a database."""
    def __init__(self, dbs=None, owned=None, disagree=None, skew_pct=2.0, strangers=None, read_nothing=False):
        self.dbs = {k: {t: {"columns": dict(v.get("columns") or {}), "foreign_keys": list(v.get("foreign_keys") or []), "create": v.get("create", "")} for t, v in tabs.items()} for k, tabs in (dbs or {}).items()}
        self.owned = dict(owned or {}); self.sql = []; self.disagree = dict(disagree or {}); self.skew_pct = skew_pct
        self.strangers = dict(strangers or {}); self.read_nothing = read_nothing; self.measured = []
    @property
    def backend_type(self): return "fake_td"
    @property
    def dialect(self): return "teradata"   # a faithful fake of the Teradata dictionary speaks Teradata (C3)
    def capabilities(self): return set(REQUIRED_CAPABILITIES) | {DeploymentCapability.CREATE_DATABASE}
    async def database_owned(self, name): return self.owned.get(name)
    async def database_exists(self, name): return name in self.dbs
    async def ensure_database(self, name, *, parent=None, perm_bytes=None):
        if name in self.dbs: return False
        self.dbs[name] = {}; return True
    async def amp_count(self): return 4
    async def system_version(self): return SYS
    async def describe_tables(self, database, tables=None):
        return {f"{database}.{t}": {"columns": v["columns"], "foreign_keys": v["foreign_keys"]} for t, v in self.dbs.get(database, {}).items() if not tables or t in tables}
    async def list_database_objects(self, name):
        return [("table", t) for t in self.dbs.get(name, {})] + [("table", s) for s in self.strangers.get(name, [])]
    async def execute_ddl(self, plan, *, dry_run=False, stop_on_error=True, include_statistics=True, replace_existing=False):
        r = DeployResult(dry_run=dry_run, generated_sql=plan.sql_script())
        if dry_run:
            r.planned = [f"{s.kind}:{s.identifier}" for s in plan.ordered()]; r.success = True; return r
        for st in plan.ordered():
            self.sql.append(st.sql)
            m = re.match(r'CREATE MULTISET TABLE "([^"]+)"\."([^"]+)"', st.sql)
            if m:
                cols = dict(re.findall(r'\n    "([^"]+)" ([A-Z]+)', st.sql))
                self.dbs.setdefault(m.group(1), {})[m.group(2)] = {"columns": cols, "foreign_keys": [], "create": st.sql, "stats": [], "comment": None}
            m = re.match(r'ALTER TABLE "([^"]+)"\."([^"]+)" ADD FOREIGN KEY \( "([^"]+)" \) REFERENCES (?:WITH NO CHECK OPTION )?"([^"]+)"\."([^"]+)" \( "([^"]+)" \)', st.sql)
            if m: self.dbs[m.group(1)][m.group(2)]["foreign_keys"].append((m.group(3), f"{m.group(4)}.{m.group(5)}", m.group(6)))
            m = re.match(r'COLLECT STATISTICS COLUMN \( ([^)]*) \) ON "([^"]+)"\."([^"]+)"', st.sql)
            if m: self.dbs[m.group(2)][m.group(3)].setdefault("stats", []).append(re.findall(r'"([^"]+)"', m.group(1)))
            m = re.match(r'COMMENT ON TABLE "([^"]+)"\."([^"]+)" IS \'(.*)\';', st.sql, re.S)
            if m: self.dbs[m.group(1)][m.group(2)]["comment"] = m.group(3).replace("''", "'")
            r.executed.append((st.kind, st.identifier))
        r.success = True
        return r
    async def read_design(self, identifiers):
        if self.read_nothing: return {}
        out = {}
        for ident in identifiers:
            db, _, tb = ident.rpartition(".")
            t = self.dbs.get(db, {}).get(tb)
            if not t: continue
            pt = RB.planned_from_sql(t.get("create") or "") if t.get("create") else None
            obs = {"multiset": True, "fallback": False,
                   "pi_columns": list(pt.pi_columns) if pt else [], "pi_unique": bool(pt and pt.pi_unique), "nopi": bool(pt and pt.nopi),
                   "columns": {c: {"type": ty, "nullable": not (pt and pt.columns.get(c, {}).get("not_null")), "charset": (pt.columns.get(c, {}).get("charset") if pt else None)} for c, ty in t["columns"].items()},
                   "foreign_keys": list(t["foreign_keys"]), "statistics": list(t.get("stats") or []), "comment": t.get("comment"), "pi_unique_in_data": True}
            obs.update(self.disagree.get(ident, {}))
            out[ident] = obs
        return out
    async def measure_pi_skew(self, identifier, pi_columns):
        self.measured.append((identifier, list(pi_columns)))
        return {"skew_pct": self.skew_pct, "amps": 4, "total_rows": 100, "max_rows": 26, "avg_rows": 25.0}

def t_orchestrator():
    S = graph("kg_a4")
    be = FakeTD({"fitness_db": {"Customers": {"columns": {"CustomerID": "INTEGER"}}}}, owned={"fitness_db": True})
    dry = with_switch(True, lambda: _run(AD.deploy_authored(S, be, actor="u1", dry_run=True)))
    d = dry["design"]
    check("a dry run carries the design: the standard, the rules with their verification status on THIS system, per-table conformance",
          d["standard"] == DS.STANDARD_VERSION and d["system"] == SYS and d["rules"]["rules"] and d["tables"]["fitness_db.Lead"]["conformance"] == "conformant" and d["tables"]["fitness_db.Lead"]["pi"]["rule"] == "declared_key", d)
    lead = S.get_entity_by_name("fitness_db.Lead", "entity")["properties"]
    check("…and VALIDATES the node: status validated, validated_at, the standard stamped — nothing landed", lead["status"] == "validated" and lead.get("validated_at") and lead.get("standard") == DS.STANDARD_VERSION and not be.sql and dry["validated"] == ["fitness_db.Lead"], lead)
    check("…and its attributes with it — one stage, so they stay one row (an entity validated beside draft attributes split them)",
          all(S.get_entity_by_name(f"fitness_db.Lead.{a}", "attribute")["properties"].get("status") == "validated" for a in ("lead_id", "status", "CustomerID")))
    info = with_switch(True, lambda: _run(AD.deploy_authored(S, be, actor="u1")))
    rb = info["readback"]
    check("a landing reads the dictionary back: every applicable rule held, conformant, the skew measured on the PI", info["status"] == "success" and rb["status"] == "conformant" and rb["violated"] == 0 and rb["tables_read"] == 1 and be.measured == [("fitness_db.Lead", ["lead_id"])], rb)
    lead = S.get_entity_by_name("fitness_db.Lead", "entity")["properties"]
    check("…the node carries its readback (held count, status, the standard) and stays validated", lead["status"] == "deployed" and lead["readback"]["status"] == "conformant" and lead["readback"]["held"] >= 8 and lead["readback"]["violated"] == 0 and lead["readback"]["standard"] == DS.STANDARD_VERSION and lead.get("validated_at"), lead.get("readback"))
    # Act II on production: closure_reason drafted on the landed, published Lead — the preview
    # named the published entity "validated" and never validated the attribute it was landing
    def _publish_then_draft():
        for n, t in [("fitness_db.Lead", "entity")] + [(f"fitness_db.Lead.{a}", "attribute") for a in ("lead_id", "status", "CustomerID")]:
            _run(S.update_entity(S.get_entity_by_name(n, t)["id"], properties={"status": "published"}))
        aid = _run(S.add_entity(name="fitness_db.Lead.closure_reason", entity_type="attribute", properties={"layer": "authored", "status": "draft", "data_type": "VARCHAR(40)"}, source="authored"))
        _run(S.add_relationship(S.get_entity_by_name("fitness_db.Lead", "entity")["id"], aid, "contains", source="authored"))
    with_switch(True, _publish_then_draft)
    dry2 = with_switch(True, lambda: _run(AD.deploy_authored(S, be, actor="u1", dry_run=True)))
    cr = S.get_entity_by_name("fitness_db.Lead.closure_reason", "attribute")["properties"]
    check("a draft attribute on a landed, PUBLISHED entity: the preview validates and names THAT attribute — never the published entity",
          dry2.get("validated") == ["fitness_db.Lead.closure_reason"] and cr["status"] == "validated" and cr.get("validated_at")
          and S.get_entity_by_name("fitness_db.Lead", "entity")["properties"]["status"] == "published", (dry2.get("validated"), cr.get("status")))
    # …and LANDING it: the column lands into the existing table; the published Lead is left as
    # it is — its receipt (the one that created the table), its landing time — re-stamped, its
    # content moved and the signed extension read "changed · extension edited" (Act II)
    before = dict(S.get_entity_by_name("fitness_db.Lead", "entity")["properties"])
    info4 = with_switch(True, lambda: _run(AD.deploy_authored(S, be, actor="u1")))
    after = S.get_entity_by_name("fitness_db.Lead", "entity")["properties"]
    cr = S.get_entity_by_name("fitness_db.Lead.closure_reason", "attribute")["properties"]
    check("a column landed into a published entity's table leaves the entity untouched — receipt, landing time, realisations",
          info4["status"] == "success" and after == before, {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)})
    check("…while the attribute carries the landing: deployed, its own receipt", cr["status"] == "deployed"
          and (cr.get("deploy_receipt") or {}).get("receipt_id") == info4["receipt"]["receipt_id"] != (before.get("deploy_receipt") or {}).get("receipt_id"), cr.get("deploy_receipt"))
    phys = S.get_entity_by_name("fitness_db.Lead", "table")["properties"]
    check("…the physical node carries the standard that built it", phys.get("standard") == DS.STANDARD_VERSION, phys)
    rc = info["receipt"]
    check("…the receipt seals the standard, the PI decision and the readback status", rc["standard"] == DS.STANDARD_VERSION and rc["pi_decisions"]["fitness_db.Lead"]["rule"] == "declared_key" and rc["readback"]["status"] == "conformant" and (lead["deploy_receipt"] or {}).get("readback") == "conformant" and (lead["deploy_receipt"] or {}).get("standard") == DS.STANDARD_VERSION, rc.get("readback"))
    # the warehouse disagrees: the dictionary shows a NON-unique PI on another column
    S2 = graph("kg_a4b")
    be2 = FakeTD({"fitness_db": {"Customers": {"columns": {"CustomerID": "INTEGER"}}}}, owned={"fitness_db": True},
                 disagree={"fitness_db.Lead": {"pi_columns": ["status"], "pi_unique": False}}, skew_pct=40.0)
    info2 = with_switch(True, lambda: _run(AD.deploy_authored(S2, be2, actor="u1")))
    rb2 = info2["readback"]
    lead2 = S2.get_entity_by_name("fitness_db.Lead", "entity")["properties"]
    check("a dictionary that disagrees reads VIOLATED, nonconformant, the rules named on the node", rb2["status"] == "nonconformant" and {i["code"] for i in rb2["issues"]} >= {"upi_from_key", "pi_even_distribution"} and lead2["readback"]["violated"] >= 2 and any(v.startswith("upi_from_key:") for v in lead2["readback"]["violations"]), lead2.get("readback"))
    # the platform's sibling holds a stranger
    S3 = graph("kg_a4c", "nowhere_db")
    be3 = FakeTD({}, owned={}, strangers={"UDERIA_EXT_nowhere_db": ["HandMade"]})
    info3 = with_switch(True, lambda: _run(AD.deploy_authored(S3, be3, actor="u1")))
    rb3 = info3["readback"]
    check("in the platform sibling, an object the model did not build is a violated row (foreign_object), named", info3["landing"]["rule"] == "platform_sibling" and rb3["foreign_objects"] == ["UDERIA_EXT_nowhere_db.HandMade"] and any(i["code"] == "foreign_object" for i in rb3["issues"]), rb3)
    # a backend that reads no dictionary
    S4 = graph("kg_a4d")
    be4 = FakeTD({"fitness_db": {"Customers": {"columns": {"CustomerID": "INTEGER"}}}}, owned={"fitness_db": True}, read_nothing=True)
    info4 = with_switch(True, lambda: _run(AD.deploy_authored(S4, be4, actor="u1")))
    check("a backend that reads no dictionary: every rule unverified, the table named as unread — never a pass", info4["readback"]["held"] == 0 and info4["readback"]["tables_unread"] == ["fitness_db.Lead"] and S4.get_entity_by_name("fitness_db.Lead", "entity")["properties"]["readback"]["status"] == "unverified", info4["readback"])
    ad = open(os.path.join(ROOT, "src/trusted_data_agent/agent/kg_constructors/authored_deploy.py")).read()
    check("ONE readback report serves the orchestrator (readback_report), ONE planned builder (planned_from_ddl)", ad.count("readback_report(") == 1 and re.search(r"planned_from_ddl\(plan\b", ad) is not None and "def _check(" not in ad)
run(t_orchestrator)


print("\n[§7] the PDM path")
def t_pdm():
    import json
    try:
        import test_pdm_constructor_deploy as T  # the fakes + make_ctx of the constructor gate (guarded by __main__)
        from trusted_data_agent.agent.kg_constructors.declarative_model import DeclarativeModelStrategy
    except ImportError:  # distribution-aware: the Industry Data Model constructor ships with neither distribution
        print("  §7 not run here: the Industry Data Model constructor is not in this distribution")
        return
    from trusted_data_agent.dbdeploy import convert
    doc = T.load_model()
    # synthetic rows: provisional, nothing applied, the DDL byte-identical to a convert with no probes
    store, be = T.FakeStore(), T.FakeDeployBackend()
    ctx, _ = T.make_ctx(store, be, deploy_pdm=True, load_demo_data=True, rows_per_table=5, data_profile="basic")
    res = _run(DeclarativeModelStrategy().construct(ctx))
    dep = res.extra["deploy"]
    plain = convert(doc, industry="acme", flavour="mvm", logical_db_name="acme_core", root_database="acme_core")
    check("synthetic rows: every decision provisional, nothing applied, the probes recorded per table", dep["status"] == "success" and dep["probes"]["provisional"] is True and dep["probes"]["applied"] is False and set(dep["probes"]["decisions"]) == {"acme_core.order", "acme_core.order_line", "acme_core.event_log", "acme_core.customer"} and dep["probes"]["decisions"]["acme_core.order"]["rule"] == "provisional_declared_key", dep["probes"]["decisions"])
    check("…the DDL the fake executed is byte-identical to a convert without probes (the baseline holds)", be.executed and be.executed[0]["n"] == len(plain.ddl.ordered()))
    check("…the deploy report carries the standard and the readback (unverified on a fake that reads nothing, never a pass)", dep["standard"] == DS.STANDARD_VERSION and dep["readback"]["held"] == 0 and dep["readback"]["tables_unread"] and dep["readback"]["rules"]["standard"] == DS.STANDARD_VERSION, dep["readback"].get("summary"))
    check("…the physical nodes carry the standard", all(e["properties"].get("standard") == DS.STANDARD_VERSION for ents in store.import_entities[1:] for e in ents if e.get("entity_type") in ("table", "column")) and any(e.get("entity_type") == "table" for ents in store.import_entities[1:] for e in ents))
    check("…the load still runs after the DDL (the probes moved the rows, not the load)", be.phase_order == ["ddl", "load", "stats"])
    # shipped rows with a constant declared key on `order`: measured → demoted to the even FK, the DDL re-emitted
    orders = "order_id,customer_id,order_ts,order_date,total_amount,is_paid\n" + "\n".join(f"7,{i % 9},2026-01-01 00:00:00,2026-01-01,1.0,true" for i in range(40))
    custs = "customer_id,full_name\n" + "\n".join(f"{i},name {i}" for i in range(9))
    lines = "order_id,line_no,qty\n" + "\n".join(f"7,{i},1" for i in range(40))
    events = "event_ts,payload\n" + "\n".join(f"2026-01-01 00:00:00,p{i}" for i in range(40))
    shipped = {("acme_core", "order"): orders, ("acme_core", "customer"): custs, ("acme_core", "order_line"): lines, ("acme_core", "event_log"): events}
    store2, be2 = T.FakeStore(), T.FakeDeployBackend()
    ctx2, _ = T.make_ctx(store2, be2, deploy_pdm=True, load_demo_data=True, shipped_csv_by_table=shipped)
    dep2 = _run(DeclarativeModelStrategy().construct(ctx2)).extra["deploy"]
    dec = dep2["probes"]["decisions"]["acme_core.order"]
    check("shipped rows: the constant declared key on order is DEMOTED to the even foreign key customer_id, named, and applied",
          dep2["status"] == "success" and dep2["probes"]["provisional"] is False and dep2["probes"]["applied"] is True and dec["rule"] == "demoted_skew" and dec["columns"] == ["customer_id"] and "every row carries the same value" in dec["reason"], dec)
    check("…the DDL re-emitted carries the demoted PI (no UNIQUE), and the untouched table is as before",
          be2.executed and be2.executed[0]["n"] == len(plain.ddl.ordered()) and dep2["probes"]["decisions"]["acme_core.customer"]["rule"] == "declared_key")
    check("…order_line keeps its declared composite key (unique in the rows)", dep2["probes"]["decisions"]["acme_core.order_line"]["rule"] == "declared_key")
run(t_pdm)


print("\n[§8] live Teradata (TDA_LIVE_TERADATA=1)")
if os.environ.get("TDA_LIVE_TERADATA", "").strip().lower() not in ("1", "true", "yes", "on"):
    print("  skipped — set TDA_LIVE_TERADATA=1 to execute every rule's verification statement and read a real table back")
else:
    def t_live():
        import uuid
        sys.path.insert(0, os.path.join(ROOT, "agent_packs/policing-data-products/scripts")); sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import tdrun  # noqa: E402
        import design_rules_verify as V  # noqa: E402
        from trusted_data_agent.dbdeploy import TeradataDeploymentBackend
        c = tdrun.creds()
        be = TeradataDeploymentBackend({"host": c["host"], "username": c["user"], "password": c["password"], "database_parent": "dbc", "database_perm_bytes": 20_000_000})
        _run(be.initialize())
        system = _run(be.system_version())
        check("the target's version is read (a rule is verified AGAINST a version)", bool(system and system.startswith("Teradata ")), system)
        tag = uuid.uuid4().hex[:6]; db = f"A4LIVE_{tag}"
        _run(be.ensure_database(db))
        try:
            rec = _run(V.verify_rules(be._execute_sql, db, system=system))
            check("every rule's verification statement executes on the target version", not rec["failed"] and len(rec["rules"]) == len(DS.RULES), rec["failed"])
            rep = DS.rules_report(system, record=rec)
            check("…so the rules report reads every rule verified on this system", rep["verified"] == len(DS.RULES), [r for r in rep["rules"] if r["status"] != "verified"])
            # a constant-column declared key (the NIBRS shape), loaded, measured, read back
            q = lambda t: f'"{db}"."{t}"'  # noqa: E731
            _run(be._execute_sql(f'CREATE MULTISET TABLE {q("Skewed")}, NO FALLBACK ("k" INTEGER NOT NULL, "c" INTEGER, "s" VARCHAR(10) CHARACTER SET LATIN) UNIQUE PRIMARY INDEX ("k");'))
            # a UNIQUE PI cannot hold a constant — load the constant shape into a NON-unique PI table, as the real NIBRS tables were
            _run(be._execute_sql(f'CREATE MULTISET TABLE {q("Const")}, NO FALLBACK ("k" INTEGER NOT NULL, "c" INTEGER, "s" VARCHAR(10) CHARACTER SET LATIN) PRIMARY INDEX ("k");'))
            for i in range(60):
                _run(be._execute_sql(f'INSERT INTO {q("Const")} VALUES (1, {i % 7}, \'v{i}\');'))
                _run(be._execute_sql(f'INSERT INTO {q("Skewed")} VALUES ({i}, {i % 7}, \'v{i}\');'))
            _run(be._execute_sql(f'COLLECT STATISTICS COLUMN ("k") ON {q("Skewed")};'))
            amps = _run(be.amp_count())
            check("the AMP count is read", isinstance(amps, int) and amps >= 1, amps)
            # the probes on the rows as the loader would see them → the constant key is demoted
            rows_ = [{"k": 1, "c": i % 7, "s": f"v{i}"} for i in range(60)]
            pr = PR.probe_table(f"{db}.Const", ["k", "c", "s"], rows_, key=["k"], provisional=False, fks_out=["c"], types={"k": "INTEGER", "c": "INTEGER", "s": "VARCHAR(10)"})
            dec = PR.choose_pi(f"{db}.Const", ["k"], ["k", "c", "s"], pr, amps=amps, fk_cols=["c"])
            check("the probes demote the constant-column declared key before any CREATE would run", dec.rule == "demoted_skew", dec.to_dict())
            # the readback measures the distribution the warehouse actually has
            sk = _run(be.measure_pi_skew(f"{db}.Const", ["k"]))
            check("the distribution read back on the constant key: every row on one AMP (skew far above the threshold)", sk and sk["total_rows"] == 60 and sk["amps_with_rows"] == 1 and sk["skew_pct"] > 100, sk)
            obs = _run(be.read_design([f"{db}.Const", f"{db}.Skewed"]))
            pc = RB.planned_from_sql(f'CREATE MULTISET TABLE "{db}"."Const", NO FALLBACK\n(\n    "k" INTEGER NOT NULL,\n    "c" INTEGER,\n    "s" VARCHAR(10) CHARACTER SET LATIN\n)\nPRIMARY INDEX ( "k" );', key_columns=["k"])
            ps = RB.planned_from_sql(f'CREATE MULTISET TABLE "{db}"."Skewed", NO FALLBACK\n(\n    "k" INTEGER NOT NULL,\n    "c" INTEGER,\n    "s" VARCHAR(10) CHARACTER SET LATIN\n)\nUNIQUE PRIMARY INDEX ( "k" );', key_columns=["k"], statistics_sql=[f'COLLECT STATISTICS COLUMN ( "k" ) ON "{db}"."Skewed";'])
            rep_c = RB.readback_report({f"{db}.Const": pc}, obs, skew={f"{db}.Const": sk}, system=system)
            rep_s = RB.readback_report({f"{db}.Skewed": ps}, obs, skew={f"{db}.Skewed": _run(be.measure_pi_skew(f"{db}.Skewed", ["k"]))}, system=system)
            check("readback on the constant-key table: pi_even_distribution VIOLATED, the table nonconformant", rep_c.status == "nonconformant" and any(r.rule == "pi_even_distribution" and r.status == "violated" for r in rep_c.rows), rep_c.to_dict()["rows"])
            # MEASURED on the local Teradata 20.00.22.31 (2026-10-06): a table created ", NO FALLBACK" reads
            # back ",FALLBACK ," in SHOW TABLE — the target FORCES fallback protection.  The readback must
            # say so (a violated no_fallback naming FALLBACK), never paper over it; every other rule holds.
            others = [r for r in rep_s.rows if r.rule != "no_fallback"]
            fb = next((r for r in rep_s.rows if r.rule == "no_fallback"), None)
            check("readback on the conformant table: every rule HELD (multiset, UPI, key NOT NULL, LATIN, statistics, even, unique) — and no_fallback either held or violated NAMING the target's forced FALLBACK",
                  all(r.status == "held" for r in others) and rep_s.unverified == 0 and fb is not None
                  and (fb.status == "held" or (fb.status == "violated" and "has FALLBACK" in fb.detail)), [r.to_dict() for r in rep_s.rows if r.status != "held"])
            if fb is not None and fb.status == "violated":
                print(f"  · recorded: this target forces FALLBACK — the readback reads {rep_s.status} for that reason alone")
        finally:
            r = _run(be.drop_database(db)); print("  cleanup:", db, "dropped" if r.success else r.errors)
    run(t_live)


# ═══════════════════════════════════════════════════════════════════════════════════════
# PROP-008 C3 — a second backend: PostgreSQL, its own rule set and verification file
# ═══════════════════════════════════════════════════════════════════════════════════════
from trusted_data_agent.dbdeploy import pg_standard as PGS
from trusted_data_agent.dbdeploy.dialects.postgresql import PostgresDeltaDialect, bounded_name
from trusted_data_agent.dbdeploy.dialects.teradata import TeradataDeltaDialect
PG_SYS = "PostgreSQL 17.11"

print("\n[§9] C3: the base carries NEITHER backend; both rule sets are records, verified on their own targets")
def t_c3_rule_sets():
    base = open(os.path.join(ROOT, "src/trusted_data_agent/dbdeploy/dialects/base.py")).read()
    td_vocab = ["MULTISET", "FALLBACK", "BYTEINT", "LATIN", "PRIMARY INDEX", "COLLECT STATISTICS", "NO CHECK OPTION", "TIMESTAMP(6)", "HASHAMP", "DBC."]
    pg_vocab = ["PRIMARY KEY", "ANALYZE", "BOOLEAN", "JSONB", "BYTEA", "TEXT", "WITH TIME ZONE", "pg_catalog", "information_schema", "CREATE INDEX", "ADD CONSTRAINT", "ADD COLUMN", "CREATE SCHEMA"]
    names = ["teradata", "postgres"]
    code = re.sub(r'"""[\s\S]*?"""', "", base)    # the docstrings may explain the seam in words; the code may not
    check("the dialect base carries no Teradata vocabulary", not [v for v in td_vocab if v.lower() in base.lower()])
    # SQL keywords are matched as keywords (case-sensitive, whole words): a parameter named ``text`` is not TEXT
    pg_hits = [v for v in pg_vocab if re.search(r"(?<![A-Za-z_])" + re.escape(v) + r"(?![A-Za-z_])", code)]
    check("…and no PostgreSQL vocabulary", not pg_hits, pg_hits)
    check("…and names no vendor at all", not [n for n in names if n in base.lower()], [n for n in names if n in base.lower()])
    td, pg = DS.rule_set_for("teradata"), DS.rule_set_for("postgresql")
    check("each backend has its OWN rule set and its own verification file", td.backend == "teradata" and pg.backend == "postgresql" and td.verification_file != pg.verification_file and pg.verification_file.name == "design_standard_verification_postgresql.json")
    check("…its own version, never the other's", td.version == "teradata/1.0" and pg.version == "postgresql/1.0")
    check("the PostgreSQL rules are records: unique ids, a source from the closed set, a verification statement each",
          len(pg.rules) == len({r.id for r in pg.rules}) == 11 and all(r.source in DS.RULE_SOURCES and r.verification for r in pg.rules))
    check("…and they are PostgreSQL's own rules, not a translation (a primary key, an index per reference, ANALYZE, an unbounded comment)",
          {"primary_key_from_key", "reference_indexed", "analyze_after_create", "comment_full", "identifiers_quoted"} <= set(pg.by_id) and not ({"multiset", "upi_from_key", "soft_ri"} & set(pg.by_id)))
    try:
        DS.rule_set_for("snowflake"); unknown = False
    except ValueError:
        unknown = True
    check("an unknown backend has NO rule set (never reported against another backend's standard)", unknown)
    td_rep = DS.rules_report("Teradata 20.00.22.31", rule_set=td)
    pg_rep = DS.rules_report(PG_SYS, rule_set=pg)
    check("BOTH rule sets read verified on their own recorded target", td_rep["verified"] == 13 and td_rep["unverified"] == 0 and pg_rep["verified"] == 11 and pg_rep["unverified"] == 0, (td_rep["verified"], pg_rep["verified"]))
    rec = pg.load_verification()
    check("…the PostgreSQL record names its backend, standard and system", rec.get("backend") == "postgresql" and rec.get("standard") == "postgresql/1.0" and str(rec.get("system", "")).startswith("PostgreSQL 17"), {k: rec.get(k) for k in ("backend", "standard", "system")})
    check("…a patch release of the same major still reads verified (PostgreSQL's family is the major)", DS.rules_report("PostgreSQL 17.12", rule_set=pg)["verified"] == 11)
    check("…another major reads unverified, every rule, never a pass", DS.rules_report("PostgreSQL 16.4", rule_set=pg)["verified"] == 0)
    check("…and one backend's record never vouches for the other's rules", DS.rules_report(PG_SYS, rule_set=pg, record=td.load_verification())["verified"] == 0)
    dl = PostgresDeltaDialect()
    check("every PostgreSQL rule with a readback key has a check (never 'no readback for')",
          all("no readback for" not in dl.readback_check(r, RB.PlannedTable(identifier="s.t"), {}, None, 50.0)[1] for r in pg.rules if r.readback))
run(t_c3_rule_sets)

LEAD_E = {"name": "fitness_db.Lead", "props": {"business_key": "lead_id", "description": "an open line of enquiry " * 30},
          "attributes": [{"name": "fitness_db.Lead.lead_id", "props": {"data_type": "INTEGER"}},
                         {"name": "fitness_db.Lead.CustomerID", "props": {"data_type": "INTEGER"}},
                         {"name": "fitness_db.Lead.opened_at", "props": {"data_type": "TIMESTAMP"}},
                         {"name": "fitness_db.Lead.hot", "props": {"data_type": "BOOLEAN"}},
                         {"name": "fitness_db.Lead.note", "props": {}}], "parent": "fitness_db.Customers"}
TIER_A = {"name": "fitness_db.Customers.loyalty_tier", "props": {"data_type": "string"}, "entity": "fitness_db.Customers"}
FACTS_C = {"fitness_db.Customers": {"deployed": "fitness_db.Customers", "key": ["CustomerID"]}}
DICT_C = {"databases": {"fitness_db"}, "tables": {"fitness_db.Customers": {"columns": {"CustomerID": "integer"}, "foreign_keys": []}}}

print("\n[§10] C3: ONE authored node, planned on both backends")
def t_c3_plan():
    land = D.landing_for("fitness_db", True)
    td = D.plan_delta({"entities": [LEAD_E], "attributes": [TIER_A]}, FACTS_C, DICT_C, land)
    pg = D.plan_delta({"entities": [LEAD_E], "attributes": [TIER_A]}, FACTS_C, DICT_C, land, dialect="postgresql")
    check("the same decisions on both: one table, one base column, one reference, the same realisations",
          [(c.kind, c.identifier) for c in td.pending() if c.kind in ("table", "column", "foreign_key")]
          == [(c.kind, c.identifier) for c in pg.pending() if c.kind in ("table", "column", "foreign_key")] and td.realises == pg.realises)
    tsql, psql = td.sql_script(), pg.sql_script()
    check("Teradata says it in Teradata (MULTISET, UNIQUE PRIMARY INDEX, soft RI, COLLECT STATISTICS)", all(x in tsql for x in ("MULTISET", "UNIQUE PRIMARY INDEX", "WITH NO CHECK OPTION", "COLLECT STATISTICS")))
    check("PostgreSQL says it in PostgreSQL and NONE of Teradata's words",
          all(x in psql for x in ('PRIMARY KEY ( "lead_id" )', "ADD COLUMN", "ADD CONSTRAINT", "CREATE INDEX", "ANALYZE")) and
          not [x for x in ("MULTISET", "FALLBACK", "PRIMARY INDEX", "NO CHECK OPTION", "COLLECT STATISTICS", "BYTEINT", "CHARACTER SET") if x in psql])
    check("the plan names its backend and the standard that built it", (td.backend, td.standard, pg.backend, pg.standard) == ("teradata", "teradata/1.0", "postgresql", "postgresql/1.0"))
    check("the key column is NOT NULL on both", '"lead_id" INTEGER NOT NULL' in tsql and '"lead_id" INTEGER NOT NULL' in psql)
    check("types follow each standard: BOOLEAN vs BYTEINT, TIMESTAMP WITH TIME ZONE vs TIMESTAMP(6) WITH TIME ZONE, TEXT for an undeclared type",
          '"hot" BOOLEAN' in psql and '"hot" BYTEINT' in tsql and '"opened_at" TIMESTAMP WITH TIME ZONE' in psql and "TIMESTAMP(6) WITH TIME ZONE" in tsql
          and '"note" TEXT' in psql and any("no data type declared — TEXT" in w for w in pg.warnings))
    tc = next(c.sql for c in td.pending() if c.kind == "comment"); pc = next(c.sql for c in pg.pending() if c.kind == "comment")
    check("the comment: bounded to 255 on Teradata, the FULL description on PostgreSQL", len(tc) < 320 and LEAD_E["props"]["description"].strip()[:400] in pc)
    check("PostgreSQL indexes the referencing column after the reference (the index is its own change)",
          [c.kind for c in pg.pending()].index("reference_index") > [c.kind for c in pg.pending()].index("foreign_key") and not any(c.kind == "reference_index" for c in td.pending()))
    check("statistics: one ANALYZE per table on PostgreSQL, a COLLECT per column set on Teradata",
          len([c for c in pg.pending() if c.kind == "statistics"]) == 1 and len([c for c in td.pending() if c.kind == "statistics"]) == 2)
    check("the preview reads in each backend's words", "1 foreign key" in pg.summary() and "1 soft-RI reference" in td.summary(), (pg.summary(), td.summary()))
    check("deterministic: the same inputs plan the same PostgreSQL statements", pg.sql_script() == D.plan_delta({"entities": [LEAD_E], "attributes": [TIER_A]}, FACTS_C, DICT_C, land, dialect="postgresql").sql_script())
    keyless = {**LEAD_E, "props": {"description": "x"}}
    pk = D.plan_delta({"entities": [keyless], "attributes": []}, FACTS_C, DICT_C, land, dialect="postgresql")
    check("no declared key ⇒ no PRIMARY KEY (a heap), named — never a guessed one", "PRIMARY KEY" not in pk.sql_script() and any("no primary key (a heap)" in w for w in pk.warnings))
    held = {"databases": {"fitness_db"}, "tables": {**DICT_C["tables"], "fitness_db.Customers": {"columns": {"CustomerID": "integer", "loyalty_tier": "text"}, "foreign_keys": []},
                                                     "fitness_db.Lead": {"columns": {a: "x" for a in ("lead_id", "CustomerID", "opened_at", "hot", "note")}, "foreign_keys": [("CustomerID", "fitness_db.Customers", "CustomerID")]}}}
    again = D.plan_delta({"entities": [{**LEAD_E, "deployed": "fitness_db.Lead"}], "attributes": [TIER_A]}, FACTS_C, held, land, dialect="postgresql")
    check("idempotent on PostgreSQL: a landed delta re-plans to nothing", again.is_noop, again.summary())
    dr = D.plan_delta({"entities": [], "attributes": []}, FACTS_C, DICT_C, land, dialect="postgresql", force_drop=True,
                      removed=[{"kind": "table", "identifier": "fitness_db.Old", "node": "x"}, {"kind": "column", "identifier": "fitness_db.Customers.gone", "node": "y"}])
    check("drops in PostgreSQL's words, still only with force", dr.sql_script().splitlines() == ['DROP TABLE "fitness_db"."Old";', 'ALTER TABLE "fitness_db"."Customers" DROP COLUMN "gone";'])
    long = bounded_name("fk", "a" * 70, "CustomerID")
    check("an invented name is bounded to 63 bytes and stable (a re-run names the same object)", len(long.encode()) <= 63 and long == bounded_name("fk", "a" * 70, "CustomerID") and bounded_name("fk", "Lead", "c") == "fk_Lead_c")
    check("a quoted identifier keeps its case and escapes a quote", 'CREATE TABLE "fitness_db"."Lead"' in psql and PostgresDeltaDialect().add_column("s", 'a"b', "c", "TEXT") == 'ALTER TABLE "s"."a""b" ADD COLUMN "c" TEXT;')
    check("the PostgreSQL CREATE reads back as planned: the primary key, the key NOT NULL, the reference + its index, ANALYZE, the full comment",
          (lambda pt: pt.primary_key == ["lead_id"] and pt.key_columns == ["lead_id"] and pt.foreign_keys == [("CustomerID", "fitness_db.Customers", "CustomerID")]
           and pt.reference_indexes == [["CustomerID"]] and pt.analyzed and pt.comment == LEAD_E["props"]["description"].strip())(RB.planned_from_ddl(pg)["fitness_db.Lead"]))
run(t_c3_plan)

def pg_obs(**over):
    o = {"name_exact": True, "encoding": "UTF8", "analyzed": True, "comment": LEAD_E["props"]["description"].strip(),
         "columns": {"lead_id": {"nullable": False}, "CustomerID": {"nullable": True}, "opened_at": {"nullable": True}, "hot": {"nullable": True}, "note": {"nullable": True}},
         "exact_columns": ["lead_id", "CustomerID", "opened_at", "hot", "note"], "primary_key": ["lead_id"],
         "foreign_keys": [("CustomerID", "fitness_db.Customers", "CustomerID")], "indexes": [["lead_id"], ["CustomerID"]]}
    o.update(over); return o

print("\n[§11] C3: the PostgreSQL readback, rule by rule")
def t_c3_readback():
    land = D.landing_for("fitness_db", True)
    plan = D.plan_delta({"entities": [LEAD_E], "attributes": []}, FACTS_C, DICT_C, land, dialect="postgresql")
    planned = RB.planned_from_ddl(plan)
    rep = lambda obs: RB.readback_report(planned, {"fitness_db.Lead": obs}, system=PG_SYS, dialect="postgresql")
    ok = rep(pg_obs())
    check("a conformant table: every applicable PostgreSQL rule held, under the PostgreSQL standard", ok.status == "conformant" and ok.violated == 0 and ok.unverified == 0 and ok.held == 8 and ok.standard == "postgresql/1.0" and ok.backend == "postgresql", ok.to_dict()["rows"])
    check("…and no Teradata rule is ever asked of it", not ({"multiset", "no_fallback", "upi_from_key", "pi_even_distribution"} & {r.rule for r in ok.rows}))
    cases = [("no index on the referencing column", dict(indexes=[["lead_id"]]), "reference_indexed", "medium"),
             ("the primary key missing", dict(primary_key=[]), "primary_key_from_key", "high"),
             ("a nullable key column", dict(columns={**pg_obs()["columns"], "lead_id": {"nullable": True}}), "key_not_null", "high"),
             ("never analyzed", dict(analyzed=False), "analyze_after_create", "low"),
             ("a truncated comment", dict(comment="an open line"), "comment_full", "low"),
             ("a folded column name", dict(exact_columns=["lead_id", "customerid", "opened_at", "hot", "note"]), "identifiers_quoted", "medium"),
             ("the reference missing", dict(foreign_keys=[]), "foreign_key_enforced", "medium"),
             ("a non-UTF8 database", dict(encoding="LATIN1"), "encoding_utf8", "low")]
    for label, over, rule, sev in cases:
        r = rep(pg_obs(**over))
        row = next((x for x in r.rows if x.rule == rule), None)
        check(f"{label} reads VIOLATED ({rule}, {sev})", row is not None and row.status == "violated" and row.severity == sev and r.status == ("nonconformant" if sev == "high" else "partial"), row.to_dict() if row else None)
    nothing = rep({})
    check("a table the catalogue says nothing about: every rule unverified, the status unverified — never conformant", nothing.held == 0 and nothing.violated == 0 and nothing.status == "unverified", nothing.to_dict()["rows"])
    keyless = D.plan_delta({"entities": [{**LEAD_E, "props": {}, "parent": None}], "attributes": []}, FACTS_C, DICT_C, land, dialect="postgresql")
    kr = RB.readback_report(RB.planned_from_ddl(keyless), {"fitness_db.Lead": pg_obs(primary_key=[], foreign_keys=[], comment=None)}, system=PG_SYS, dialect="postgresql")
    check("a keyless heap: the without-key rule holds (no primary key), the with-key rules are not asked", any(r.rule == "heap_without_key" and r.status == "held" for r in kr.rows) and not any(r.rule in ("primary_key_from_key", "key_not_null") for r in kr.rows), kr.to_dict()["rows"])
run(t_c3_readback)


class FakePG(DeploymentBackend):
    """A faithful PostgreSQL fake: a catalogue it mutates from the PostgreSQL DDL it accepts, and
    REFUSES Teradata syntax the way the real server does (a syntax error) — so a delta said in the
    wrong dialect cannot pass for a landing."""
    TD_WORDS = ("MULTISET", "PRIMARY INDEX", "NO CHECK OPTION", "COLLECT STATISTICS", "FALLBACK", "CHARACTER SET")
    def __init__(self, schemas=None, owned=None):
        self.s = {k: {t: {"columns": dict(v.get("columns") or {}), "pk": list(v.get("pk") or []), "fks": list(v.get("fks") or []), "ix": [list(x) for x in v.get("ix") or []], "analyzed": False, "comment": None, "notnull": set(v.get("pk") or [])} for t, v in tabs.items()} for k, tabs in (schemas or {}).items()}
        self.owned = dict(owned or {}); self.sql = []
    @property
    def backend_type(self): return "postgresql"
    def capabilities(self): return set(REQUIRED_CAPABILITIES) | {DeploymentCapability.CREATE_DATABASE}
    async def database_owned(self, name): return self.owned.get(name, False if name not in self.s else None)
    async def database_exists(self, name): return name in self.s
    async def ensure_database(self, name, *, parent=None, perm_bytes=None):
        if name in self.s: return False
        self.s[name] = {}; return True
    async def system_version(self): return PG_SYS
    async def describe_tables(self, database, tables=None):
        return {f"{database}.{t}": {"columns": {c: "x" for c in v["columns"]}, "foreign_keys": list(v["fks"])} for t, v in self.s.get(database, {}).items()}
    async def list_database_objects(self, name): return [("table", t) for t in self.s.get(name, {})]
    async def execute_ddl(self, plan, *, dry_run=False, stop_on_error=True, include_statistics=True, replace_existing=False):
        r = DeployResult(dry_run=dry_run, generated_sql=plan.sql_script())
        if dry_run:
            r.planned = [f"{s.kind}:{s.identifier}" for s in plan.ordered()]; r.success = True; return r
        for st in plan.ordered():
            self.sql.append(st.sql)
            if any(w in st.sql for w in self.TD_WORDS):
                r.errors.append((st.identifier, "syntax error at or near a Teradata keyword")); continue
            m = re.match(r'CREATE TABLE "([^"]+)"\."([^"]+)"', st.sql)
            if m:
                cols = dict(re.findall(r'\n    "([^"]+)" ([A-Z]+)', st.sql))
                pk = re.findall(r'"([^"]+)"', (re.search(r'PRIMARY KEY \(([^)]*)\)', st.sql) or [None, ""])[1] or "")
                nn = set(re.findall(r'\n    "([^"]+)" [^\n]* NOT NULL', st.sql)) | set(pk)
                self.s.setdefault(m.group(1), {})[m.group(2)] = {"columns": cols, "pk": pk, "fks": [], "ix": [pk] if pk else [], "analyzed": False, "comment": None, "notnull": nn}
            m = re.match(r'ALTER TABLE "([^"]+)"\."([^"]+)" ADD COLUMN "([^"]+)" (\S+)', st.sql)
            if m: self.s[m.group(1)][m.group(2)]["columns"][m.group(3)] = m.group(4)
            m = re.match(r'ALTER TABLE "([^"]+)"\."([^"]+)" ADD CONSTRAINT "[^"]+" FOREIGN KEY \( "([^"]+)" \) REFERENCES "([^"]+)"\."([^"]+)" \( "([^"]+)" \)', st.sql)
            if m:
                if not self.s.get(m.group(4), {}).get(m.group(5), {}).get("pk"):
                    r.errors.append((st.identifier, "there is no unique constraint matching given keys for referenced table")); continue
                self.s[m.group(1)][m.group(2)]["fks"].append((m.group(3), f"{m.group(4)}.{m.group(5)}", m.group(6)))
            m = re.match(r'CREATE INDEX "[^"]+" ON "([^"]+)"\."([^"]+)" \( "([^"]+)" \)', st.sql)
            if m: self.s[m.group(1)][m.group(2)]["ix"].append([m.group(3)])
            m = re.match(r'ANALYZE "([^"]+)"\."([^"]+)"', st.sql)
            if m: self.s[m.group(1)][m.group(2)]["analyzed"] = True
            m = re.match(r'COMMENT ON TABLE "([^"]+)"\."([^"]+)" IS \'(.*)\';', st.sql, re.S)
            if m: self.s[m.group(1)][m.group(2)]["comment"] = m.group(3).replace("''", "'")
            r.executed.append((st.kind, st.identifier))
        r.success = not r.errors
        return r
    async def read_design(self, identifiers):
        out = {}
        for ident in identifiers:
            sch, _, tb = ident.partition(".")
            t = self.s.get(sch, {}).get(tb)
            if not t: continue
            out[ident] = {"name_exact": True, "encoding": "UTF8", "analyzed": t["analyzed"], "comment": t["comment"],
                          "columns": {c: {"nullable": c not in t["notnull"]} for c in t["columns"]}, "exact_columns": list(t["columns"]),
                          "primary_key": list(t["pk"]), "foreign_keys": list(t["fks"]), "indexes": [list(x) for x in t["ix"]]}
        return out

print("\n[§12] C3: one authored node lands on BOTH backends (the orchestrator on the real GraphStore)")
def t_c3_both():
    S = graph("kg_c3")
    n_phys = lambda: sum(1 for e in _run(S.list_entities(limit=999999)) if (e.get("properties") or {}).get("layer") == "physical")
    td = FakeTD({"fitness_db": {"Customers": {"columns": {"CustomerID": "INTEGER"}}}}, owned={"fitness_db": True})
    i1 = with_switch(True, lambda: _run(AD.deploy_authored(S, td, actor="u1")))
    lead = S.get_entity_by_name("fitness_db.Lead", "entity")["properties"]
    phys_after_td = n_phys()
    check("on the graph's own infrastructure (Teradata) it lands as before: physical nodes, deployed_table, the TD standard",
          i1["status"] == "success" and lead["deployed_table"] == "fitness_db.Lead" and i1["infrastructure"] == {"graph": "teradata", "this_deploy": "teradata", "primary": True}
          and lead["realisations"]["teradata"]["table"] == "fitness_db.Lead" and i1["design"]["backend"] == "teradata", i1.get("infrastructure"))
    # the PostgreSQL login does NOT own the model's schema: there the node lands in the platform sibling, so its
    # PostgreSQL realisation differs from its Teradata one — resuming from the wrong one cannot hide
    pg = FakePG({"fitness_db": {"Customers": {"columns": {"CustomerID": "integer"}, "pk": ["CustomerID"]}}}, owned={"fitness_db": False})
    dry = with_switch(True, lambda: _run(AD.deploy_authored(S, pg, actor="u1", dry_run=True)))
    check("the SAME authored node previews on PostgreSQL under the PostgreSQL standard, its rules verified on this target",
          dry["status"] == "dry_run" and dry["design"]["backend"] == "postgresql" and dry["design"]["standard"] == "postgresql/1.0"
          and dry["design"]["rules"]["verified"] == 11 and "PRIMARY KEY" in dry["generated_sql"] and "PRIMARY INDEX" not in dry["generated_sql"], dry.get("design", {}).get("rules", {}).get("verified"))
    check("…and says it lands on a second infrastructure (the graph's physical layer is Teradata)", dry["infrastructure"] == {"graph": "teradata", "this_deploy": "postgresql", "primary": False})
    i2 = with_switch(True, lambda: _run(AD.deploy_authored(S, pg, actor="u1")))
    lead = S.get_entity_by_name("fitness_db.Lead", "entity")["properties"]
    check("it LANDS on PostgreSQL: the table, the enforced reference and its index, ANALYZE",
          i2["status"] == "success" and i2["landing"]["rule"] == "platform_sibling" and "Lead" not in pg.s["fitness_db"]
          and (lambda t: t.get("fks") == [("CustomerID", "fitness_db.Customers", "CustomerID")] and ["CustomerID"] in t.get("ix", []) and t.get("analyzed"))(pg.s.get("UDERIA_EXT_fitness_db", {}).get("Lead", {})),
          {"result": i2.get("result"), "schemas": {k: list(v) for k, v in pg.s.items()}})
    check("…no Teradata statement ever reached PostgreSQL", not [s for s in pg.sql if any(w in s for w in FakePG.TD_WORDS)])
    check("…read back conformant under the PostgreSQL rules", i2["readback"]["status"] == "conformant" and i2["readback"]["backend"] == "postgresql" and i2["readback"]["standard"] == "postgresql/1.0", i2["readback"]["summary"])
    check("the node records BOTH realisations; the graph keeps ONE physical layer (Teradata), deployed_table unchanged",
          lead["realisations"]["postgresql"]["table"] == "UDERIA_EXT_fitness_db.Lead" and lead["realisations"]["teradata"]["table"] == "fitness_db.Lead"
          and lead["deployed_table"] == "fitness_db.Lead" and n_phys() == phys_after_td and lead["realisations"]["postgresql"]["readback"]["status"] == "conformant")
    rc = i2["receipt"]
    check("the PostgreSQL landing has its own sealed receipt naming the backend, filed under that realisation",
          rc["backend"] == "postgresql" and rc["standard"] == "postgresql/1.0" and lead["realisations"]["postgresql"]["receipt"]["receipt_id"] == rc["receipt_id"]
          and lead["deploy_receipt"]["receipt_id"] == i1["receipt"]["receipt_id"] and '"backend":"postgresql"' in D.receipt_content(rc))
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler
    listing = _run(KnowledgeGraphHandler.authored_nodes(S))
    row = next(n for n in listing["nodes"] if n["name"] == "fitness_db.Lead")
    check("the authored listing (what the Govern dialog reads) says where the node landed: both warehouses, the graph's own named",
          row["landed_on"] == ["postgresql", "teradata"] and listing["graph_infrastructure"] == "teradata"
          and row["realisations"]["postgresql"]["table"] == "UDERIA_EXT_fitness_db.Lead", (row.get("landed_on"), listing.get("graph_infrastructure")))
    i3 = with_switch(True, lambda: _run(AD.deploy_authored(S, pg, actor="u1")))
    check("a re-run on PostgreSQL resumes from ITS realisation: a no-op", i3["status"] == "noop" and i3["plan"]["is_noop"], i3.get("summary"))
    i4 = with_switch(True, lambda: _run(AD.deploy_authored(S, td, actor="u1")))
    check("…and a re-run on Teradata is still a no-op (neither realisation disturbs the other)", i4["status"] == "noop", i4.get("summary"))
    # a graph with no physical layer yet: the first backend IS the graph's infrastructure
    S2 = graph("kg_c3b", "nowhere_db")
    for e in _run(S2.list_entities(limit=999999)):
        if (e.get("properties") or {}).get("layer") == "physical":
            _run(S2.delete_entity(e["id"]))
    pg2 = FakePG({}, owned={})
    j = with_switch(True, lambda: _run(AD.deploy_authored(S2, pg2, actor="u1")))
    ph = S2.get_entity_by_name("UDERIA_EXT_nowhere_db.Lead", "table")
    check("a graph with no physical layer: PostgreSQL lands as its own infrastructure — physical nodes tagged postgresql, the sibling schema provisioned",
          j["infrastructure"]["primary"] is True and ph and ph["properties"]["infrastructure"] == "postgresql" and "UDERIA_EXT_nowhere_db" in pg2.s, j.get("infrastructure"))
run(t_c3_both)

print("\n[§13] C3: the faces — the backend follows the connection's driver")
def t_c3_faces():
    from trusted_data_agent.dbdeploy.connection import DEPLOYABLE_DRIVERS, backend_type_for_driver, canvas_creds_to_config
    from trusted_data_agent.dbdeploy import factory
    # distribution-aware: Teradata is checked only where this distribution carries its backend
    carried = [d for d in DEPLOYABLE_DRIVERS if d != "teradata" or os.path.exists(os.path.join(ROOT, "src/trusted_data_agent/dbdeploy/teradata_backend.py"))]
    check("the deployable drivers are declared once: Teradata and PostgreSQL, each carried one a registered backend",
          DEPLOYABLE_DRIVERS == ("teradata", "postgresql") and all(backend_type_for_driver(d) in factory.list_backends() for d in carried))
    cfg = canvas_creds_to_config({"driver": "postgresql", "host": "h", "port": 5433, "user": "u", "password": "p", "database": "d", "ssl": True})
    check("the resolved config keeps the driver, the port and TLS (they were dropped before C3)", cfg.get("driver") == "postgresql" and cfg.get("port") == 5433 and cfg.get("ssl") is True and cfg.get("username") == "u")
    check("the factory makes a PostgreSQL backend that speaks PostgreSQL", factory.make_backend("postgresql", cfg).dialect == "postgresql")
    rr = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
    fn = rr[rr.index("async def kg_authored_deploy"):rr.index("async def kg_redeploy_metric_views")]
    check("the deploy route chooses the backend from the connection's driver, never a request field",
          "backend_type_for_driver(driver)" in fn and "deployment_backend_type" not in fn.split('"""', 2)[2])
    check("…and refuses a driver no backend can land on, naming the deployable ones", "driver_not_deployable" in fn and "DEPLOYABLE_DRIVERS" in fn)
    js = open(os.path.join(ROOT, "static/js/handlers/knowledgeGraphPanelHandler.js")).read()
    m = re.search(r"const DEPLOY_DRIVERS = \[([^\]]*)\]", js)
    check("the Govern dialog's picker mirrors DEPLOYABLE_DRIVERS exactly", m and tuple(re.findall(r"'([^']+)'", m.group(1))) == DEPLOYABLE_DRIVERS)
    check("…and filters the connections by it (no Teradata-only filter left)", "DEPLOY_DRIVERS.includes(" in js and "=== 'teradata')" not in js[js.index("function _wireGovDeploy"):js.index("function _wireGovDeploy") + 6000])
run(t_c3_faces)

if os.environ.get("TDA_LIVE_POSTGRES") != "1":
    print("\n[§14] live PostgreSQL (TDA_LIVE_POSTGRES=1 + PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE)")
    print("  skipped — set TDA_LIVE_POSTGRES=1 to execute every PostgreSQL rule and land + read back a real table")
else:
    print("\n[§14] live PostgreSQL")
    def t_live_pg():
        import uuid
        from trusted_data_agent.dbdeploy.postgresql_backend import PostgresDeploymentBackend
        import importlib.util
        spec = importlib.util.spec_from_file_location("drv", os.path.join(ROOT, "scripts/design_rules_verify.py"))
        V = importlib.util.module_from_spec(spec); spec.loader.exec_module(V)
        be = PostgresDeploymentBackend({"host": os.environ.get("PGHOST", "localhost"), "port": os.environ.get("PGPORT", "5432"),
                                        "username": os.environ.get("PGUSER"), "password": os.environ.get("PGPASSWORD"),
                                        "database": os.environ.get("PGDATABASE", "postgres")})
        system = _run(be.system_version())
        check("the target's version is read", system and system.startswith("PostgreSQL "), system)
        db = f"C3_{uuid.uuid4().hex[:6]}"
        _run(be.ensure_database(db))
        try:
            rec = _run(V.verify_rules(be._execute_sql, db, system=system, rule_set=DS.rule_set_for("postgresql")))
            check("every PostgreSQL rule's verification statement executes on the target", not rec["failed"] and len(rec["rules"]) == 11, rec["failed"])
            _run(be._execute_sql(f'CREATE TABLE "{db}"."Customers" ("CustomerID" INTEGER PRIMARY KEY)'))
            land = D.landing_for(db, _run(be.database_owned(db)))
            facts = {f"{db}.Customers": {"deployed": f"{db}.Customers", "key": ["CustomerID"]}}
            ent = {**LEAD_E, "name": f"{db}.Lead", "parent": f"{db}.Customers",
                   "attributes": [{**a, "name": a["name"].replace("fitness_db.", f"{db}.")} for a in LEAD_E["attributes"]]}
            tier = {**TIER_A, "name": f"{db}.Customers.loyalty_tier", "entity": f"{db}.Customers"}
            dic = {"databases": {db}, "tables": _run(be.describe_tables(db))}
            plan = D.plan_delta({"entities": [ent], "attributes": [tier]}, facts, dic, land, dialect="postgresql")
            res = _run(be.execute_delta(plan))
            check("the delta lands on a real PostgreSQL: every statement executed, no error", res.success and len(res.landed) == len(plan.pending()), res.errors)
            planned = RB.planned_from_ddl(plan)
            rep = RB.readback_report(planned, _run(be.read_design(sorted(planned))), system=system, dialect="postgresql")
            check("the real catalogue reads back conformant: every applicable PostgreSQL rule held", rep.status == "conformant" and rep.violated == 0 and rep.unverified == 0, rep.to_dict()["rows"])
            again = D.plan_delta({"entities": [{**ent, "deployed": f"{db}.Lead"}], "attributes": [tier]}, facts,
                                 {"databases": {db}, "tables": _run(be.describe_tables(db))}, land, dialect="postgresql")
            check("re-planned against the real catalogue: a no-op", again.is_noop, again.summary())
        finally:
            r = _run(be.drop_database(db)); print("  cleanup:", db, "dropped" if r.success else r.errors)
    run(t_live_pg)

try: os.remove(TMP)
except Exception: pass
print(f"\n=== PDM design rules (A4 + C3) results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
