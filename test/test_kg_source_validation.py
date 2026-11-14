"""Source validation for KG constructors (2026-09-05) — pure core, injected runner.

The ontology declares; the data holds. A constructor that validates its graph against
the source at build time stamps the sampled shape of a text-stored date, its range, a
declared key's uniqueness, a coded column's blank ratio and a real row count on the
LOGICAL nodes — the facts an agent otherwise learns at runtime, one wasted turn each.
Targets from the graph shape (ANDP and discovery), probe SQL, stamping, the fail-open
orchestration, the guard seeded from the ontology, the enrichment line. CI-pure."""
import asyncio, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.agent.kg_constructors import source_validation as sv
from trusted_data_agent.agent.preflight import textdates as td
from trusted_data_agent.components import dateforms as df

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

def andp_graph():
    E = [
        {"name": "P.Incident", "entity_type": "entity", "properties": {"business_key": "ORI,INCNUM", "deployed_table": "P_Domain.Incident"}},
        {"name": "P.Incident.INCDATE", "entity_type": "attribute", "properties": {"label": "INCDATE", "description": "INCIDENT DATE", "deployed_type": "CHAR(11)", "deployed_column": "P_Domain.Incident.INCDATE"}},
        {"name": "P.Incident.ARRDATE", "entity_type": "attribute", "properties": {"label": "ARRDATE", "deployed_type": "VARCHAR(11)", "deployed_column": "P_Domain.Incident.ARRDATE", "is_pii": True}},
        {"name": "P.Incident.V4019", "entity_type": "attribute", "properties": {"label": "V4019", "deployed_type": "VARCHAR(2)", "deployed_column": "P_Domain.Incident.V4019", "allowed_values": [{"code": "0", "label": "Female"}]}},
        {"name": "P.Incident.V1006", "entity_type": "attribute", "properties": {"label": "V1006", "description": "REPORT DATE INDICATOR", "deployed_type": "CHAR(1)", "deployed_column": "P_Domain.Incident.V1006"}},
        {"name": "P.Incident.AMT", "entity_type": "attribute", "properties": {"label": "AMT", "deployed_type": "DECIMAL(10,2)", "deployed_column": "P_Domain.Incident.AMT", "allowed_values": [{"code": "1", "label": "x"}]}},
        {"name": "P.Nowhere", "entity_type": "entity", "properties": {"business_key": "ID"}},
        {"name": "P.Bad", "entity_type": "entity", "properties": {"deployed_table": "P_Domain.Bad; DROP TABLE x"}},
    ]
    R = [{"relationship_type": "contains", "source_name": "P.Incident", "source_type": "entity", "target_name": f"P.Incident.{c}", "target_type": "attribute"} for c in ("INCDATE", "ARRDATE", "V4019", "V1006", "AMT")]
    return E, R

def discovery_graph():
    E = [
        {"name": "fitness_db.Sales", "entity_type": "entity", "properties": {"primary_key": "SaleID"}},
        {"name": "fitness_db.Sales.SaleDate", "entity_type": "attribute", "properties": {"label": "SaleDate"}},
        {"name": "fitness_db", "entity_type": "schema", "properties": {"layer": "physical"}},
        {"name": "fitness_db.Sales", "entity_type": "table", "properties": {"layer": "physical"}},
        {"name": "fitness_db.Sales.SaleDate", "entity_type": "column", "properties": {"layer": "physical", "datatype": "VARCHAR(10)"}},
    ]
    R = [
        {"relationship_type": "contains", "source_name": "fitness_db.Sales", "source_type": "entity", "target_name": "fitness_db.Sales.SaleDate", "target_type": "attribute"},
        {"relationship_type": "implements", "source_name": "fitness_db.Sales", "source_type": "table", "target_name": "fitness_db.Sales", "target_type": "entity"},
        {"relationship_type": "implements", "source_name": "fitness_db.Sales.SaleDate", "source_type": "column", "target_name": "fitness_db.Sales.SaleDate", "target_type": "attribute"},
    ]
    return E, R

def t_targets():
    E, R = andp_graph()
    t = sv.validation_targets(E, R)
    check("ANDP: the deployed table with its declared key is a target", [(x["table"], x["keys"]) for x in t["tables"]] == [("P_Domain.Incident", ["ORI", "INCNUM"])], t["tables"])
    check("an entity with no deployed identifier, and one whose identifier is not identifier-safe, are NOT targets", all(x["entity"] not in ("P.Nowhere", "P.Bad") for x in t["tables"]), t["tables"])
    check("text dates: the CHAR(11) date and the PII VARCHAR(11) date; not the one-character flag", sorted(x["column"] for x in t["text_dates"]) == ["ARRDATE", "INCDATE"], t["text_dates"])
    check("the PII flag travels with the target", next(x for x in t["text_dates"] if x["column"] == "ARRDATE")["is_pii"] is True, None)
    check("coded: the character-deployed coded column; not the DECIMAL one", [x["column"] for x in t["coded"]] == ["V4019"], t["coded"])
    E2, R2 = discovery_graph()
    t2 = sv.validation_targets(E2, R2)
    check("discovery: the table resolves through the physical layer's implements edge", [(x["table"], x["keys"]) for x in t2["tables"]] == [("fitness_db.Sales", ["SaleID"])], t2["tables"])
    check("discovery: the deployed type comes from the physical column node (VARCHAR date → text date)", [x["column"] for x in t2["text_dates"]] == ["SaleDate"], t2["text_dates"])
    check("key_columns: comma/space separated, list form, order kept, dupes dropped", sv.key_columns({"business_key": "ORI, INCNUM ORI"}) == ["ORI", "INCNUM"] and sv.key_columns({"primary_key": ["A", "B"]}) == ["A", "B"] and sv.key_columns({}) == [], None)

def t_probes():
    t = {"column": "INCDATE", "table": "P_Domain.Incident"}
    check("shape probe: Teradata TOP, non-blank, non-null", sv.shape_probe(t) == "SELECT TOP 5 INCDATE AS v FROM P_Domain.Incident WHERE INCDATE IS NOT NULL AND TRIM(INCDATE) <> ''", sv.shape_probe(t))
    check("shape probe: a generic dialect uses LIMIT", sv.shape_probe(t, "generic", 3).endswith(" LIMIT 3") and "TOP" not in sv.shape_probe(t, "generic", 3), None)
    check("table probe: ONE exact scan per table is a bare count — nothing evaluated per row", sv.table_probe("P_Domain.Incident") == "SELECT COUNT(*) AS n FROM P_Domain.Incident", None)
    sp = sv.sample_probe("T", ["A", "B"], [(t, "DD-MMM-YYYY")])
    G = df.guarded_cast("INCDATE", "DD-MMM-YYYY")
    check("sample probe: blank counts AND guarded-CAST date ranges AND the unparseable count over ONE bounded TOP-sampled read",
          sp == f"SELECT COUNT(*) AS n, SUM(CASE WHEN A IS NULL OR TRIM(A) = '' THEN 1 ELSE 0 END) AS b_0, SUM(CASE WHEN B IS NULL OR TRIM(B) = '' THEN 1 ELSE 0 END) AS b_1, MIN({G}) AS lo_0, MAX({G}) AS hi_0, SUM(CASE WHEN INCDATE IS NOT NULL AND TRIM(INCDATE) <> '' AND REGEXP_SIMILAR(TRIM(INCDATE), '^[0-9]{{2}}-[A-Za-z]{{3}}-[0-9]{{4}}$', 'i') = 0 THEN 1 ELSE 0 END) AS bad_0 FROM (SELECT TOP 200000 A, B, INCDATE FROM T) x", sp)
    check("never a bare CAST of the column in a probe (a dirty row must cost one value, not the read), and never TRYCAST (Teradata rejects its FORMAT phrase)", "CAST(INCDATE" not in sp and "TRYCAST" not in sp, None)
    check("the pattern a FORMAT admits", df.format_regex("DD-MMM-YYYY") == "^[0-9]{2}-[A-Za-z]{3}-[0-9]{4}$" and df.format_regex("YYYY-MM-DD") == "^[0-9]{4}-[0-9]{2}-[0-9]{2}$" and df.format_regex("MM/DD/YY") == "^[0-9]{2}/[0-9]{2}/[0-9]{2}$" and df.format_regex("DD.MM.YYYY") == "^[0-9]{2}\\.[0-9]{2}\\.[0-9]{4}$", df.format_regex("DD.MM.YYYY"))
    E9 = [{"name": "P.Rel", "entity_type": "entity", "properties": {"deployed_table": "D.Rel"}}]
    calls9 = []
    def r9(sql):
        calls9.append(sql); return [{"n": 3}] if sql.startswith("SELECT COUNT(*) AS n FROM") else []
    asyncio.run(sv.validate_with_source(E9, [], r9))
    check("a table with nothing to sample gets no sample read (the empty column list that failed live)", calls9 == ["SELECT COUNT(*) AS n FROM D.Rel"], calls9)
    check("sample probe: generic dialect samples with LIMIT", sv.sample_probe("T", ["A"], [], "generic", 10).endswith("FROM (SELECT A FROM T LIMIT 10) x"), None)
    check("key probe counts distinct groups over the whole key", sv.key_probe("T", ["ORI", "INCNUM"]) == "SELECT COUNT(*) AS groups_n FROM (SELECT ORI, INCNUM FROM T GROUP BY ORI, INCNUM) x", None)

def t_stamping():
    node = {"name": "a", "entity_type": "attribute", "properties": {}}
    t = {"column": "INCDATE", "table": "T", "node": node, "is_pii": False}
    check("shape from rows (padded, keyed by any case)", sv.apply_shape(t, [{"V": "31-OCT-2023   "}, {"v": "01-JAN-2023"}]) == "DD-MMM-YYYY" and node["properties"]["sampled_shape"] == "DD-MMM-YYYY" and node["properties"]["sample_value"] == "31-OCT-2023", node)
    pii = {"name": "b", "entity_type": "attribute", "properties": {}}
    sv.apply_shape({"column": "ARRDATE", "table": "T", "node": pii, "is_pii": True}, [{"v": "31-OCT-2023"}])
    check("a PII column gets the shape but never a sample value", pii["properties"].get("sampled_shape") == "DD-MMM-YYYY" and "sample_value" not in pii["properties"], pii)
    check("mixed shapes stamp nothing", sv.apply_shape({"column": "X", "table": "T", "node": {"properties": {}}, "is_pii": False}, [{"v": "31-OCT-2023"}, {"v": "2023-01-01"}]) is None, None)
    check("range stamped as ISO strings (a datetime value is cut to its date), read by index, marked sampled, with the unparseable share marked sampled", sv.apply_range(t, [{"n": 5, "lo_0": "2023-01-01 00:00:00", "hi_0": "2023-12-31", "bad_0": 1}], 0) and node["properties"]["date_range"] == ["2023-01-01", "2023-12-31"] and node["properties"]["date_range_sampled"] is True and node["properties"]["date_unparseable_ratio"] == 0.2 and node["properties"]["date_unparseable_sampled"] is True, node)
    check("an unreadable range stamps nothing", not sv.apply_range(t, [{"lo_0": None, "hi_0": "x"}]), None)
    tb = {"table": "T", "keys": ["K"], "node": {"properties": {}}}
    c1, c2 = {"node": {"properties": {}}}, {"node": {"properties": {}}}
    check("row count from the exact scan", sv.apply_row_count(tb, [{"n": 200}]) == 200 and tb["node"]["properties"]["row_count"] == 200, None)
    check("blank ratios from the sampled aggregate (b_i over the SAMPLE's n)", sv.apply_blanks([c1, c2], [{"n": 1000, "b_0": 250, "b_1": 0}]) == 2 and c1["node"]["properties"]["blank_ratio"] == 0.25 and c2["node"]["properties"]["blank_ratio"] == 0.0, None)
    check("a key with fewer groups than rows is NOT unique and names the duplicate groups", sv.apply_key(tb, [{"groups_n": 150}], 200) is False and tb["node"]["properties"]["key_duplicate_groups"] == 50, tb)
    check("a key with as many groups as rows is unique", sv.apply_key(tb, [{"groups_n": 200}], 200) is True and "key_duplicate_groups" not in tb["node"]["properties"], tb)

def t_orchestration():
    E, R = andp_graph()
    log = []
    def runner(sql):
        log.append(sql)
        if "groups_n" in sql:
            return [{"groups_n": 990}]
        if "AS s_0_a" in sql:          # the one sampled shape read per table: INCDATE witnesses, ARRDATE blank
            return [{"s_0_a": "01-JAN-2023", "s_0_b": "31-OCT-2023", "s_1_a": None, "s_1_b": None}]
        if "ARRDATE AS v" in sql:      # the per-column fallback for the column the sample left blank
            raise RuntimeError("[Error 3807] no such column")
        if "FROM (SELECT TOP 200000" in sql:
            return [{"n": 500, "b_0": 200, "lo_0": "2023-01-01", "hi_0": "2023-12-31"}]
        if sql.startswith("SELECT COUNT(*) AS n FROM"):
            return [{"n": 1000}]
        return []
    stats = asyncio.run(sv.validate_with_source(E, R, runner))
    by = {e["name"]: e["properties"] for e in E}
    check("row count + key verdict on the entity", by["P.Incident"]["row_count"] == 1000 and by["P.Incident"]["key_unique"] is False and by["P.Incident"]["key_duplicate_groups"] == 10, by["P.Incident"])
    check("shape + sample (the first witness) + range on the text date", by["P.Incident.INCDATE"]["sampled_shape"] == "DD-MMM-YYYY" and by["P.Incident.INCDATE"]["sample_value"] == "01-JAN-2023" and by["P.Incident.INCDATE"]["date_range"] == ["2023-01-01", "2023-12-31"], by["P.Incident.INCDATE"])
    check("blank ratio on the coded column", by["P.Incident.V4019"]["blank_ratio"] == 0.4, by["P.Incident.V4019"])
    check("a failing probe is counted and skipped, the build goes on", stats["probe_errors"] == 1 and "sampled_shape" not in by["P.Incident.ARRDATE"], stats)
    check("the stats add up", (stats["tables_counted"], stats["keys_checked"], stats["keys_not_unique"], stats["shapes_sampled"], stats["date_ranges"], stats["blank_ratios"]) == (1, 1, 1, 1, 1, 1), stats)
    check("one sampled shape read per table (+ a per-column fallback only for a blank witness), one exact count, ONE sampled read carrying blanks + ranges, one key scan",
          len(log) == 5 and sum(l == "SELECT COUNT(*) AS n FROM P_Domain.Incident" for l in log) == 1 and sum("SUM(CASE" in l and "MIN(CAST(CASE WHEN REGEXP_SIMILAR" in l for l in log) == 1
          and sum("groups_n" in l for l in log) == 1 and sum("AS s_0_a" in l for l in log) == 1 and sum(l.startswith("SELECT TOP 5 ARRDATE") for l in log) == 1, [l[:40] for l in log])
    shp = sv.shapes_probe("T", [{"column": "INCDATE"}, {"column": "ARRDATE"}])
    check("the shape read: two non-blank witnesses per date column from ONE bounded sample",
          shp == "SELECT MIN(CASE WHEN INCDATE IS NOT NULL AND TRIM(INCDATE) <> '' THEN INCDATE END) AS s_0_a, MAX(CASE WHEN INCDATE IS NOT NULL AND TRIM(INCDATE) <> '' THEN INCDATE END) AS s_0_b, MIN(CASE WHEN ARRDATE IS NOT NULL AND TRIM(ARRDATE) <> '' THEN ARRDATE END) AS s_1_a, MAX(CASE WHEN ARRDATE IS NOT NULL AND TRIM(ARRDATE) <> '' THEN ARRDATE END) AS s_1_b FROM (SELECT TOP 200000 INCDATE, ARRDATE FROM T) x", shp)
    check("every round reports its seconds", set(stats["seconds"]) == {"shapes", "counts", "samples", "keys"}, stats["seconds"])
    E5, R5 = andp_graph()
    def dirty(sql):
        if "MIN(CAST(CASE WHEN REGEXP_SIMILAR" in sql:
            raise RuntimeError("[Error 2666] Invalid date supplied")
        return runner(sql)
    s5 = asyncio.run(sv.validate_with_source(E5, R5, dirty))
    b5 = {e["name"]: e["properties"] for e in E5}
    check("a dirty date row costs the range, not the blank ratios or the row count", b5["P.Incident"].get("row_count") == 1000 and "date_range" not in b5["P.Incident.INCDATE"] and b5["P.Incident.INCDATE"].get("sampled_shape") == "DD-MMM-YYYY" and b5["P.Incident.V4019"].get("blank_ratio") == 0.4 and s5["probe_errors"] >= 1, (s5, b5["P.Incident"]))
    check("no timestamp is stamped anywhere (a no-change build hashes identically)", not any(k for e in E for k in (e.get("properties") or {}) if "time" in k or "_at" in k), None)
    async def arunner(sql):
        return runner(sql)
    E2, R2 = andp_graph()
    check("an async runner works the same", asyncio.run(sv.validate_with_source(E2, R2, arunner))["shapes_sampled"] == 1, None)
    def boom(sql):
        raise RuntimeError("connection lost")
    E3, R3 = andp_graph()
    s3 = asyncio.run(sv.validate_with_source(E3, R3, boom))
    check("a runner that always fails never raises and stamps nothing", s3["probe_errors"] == s3["probes"] > 0 and all("row_count" not in (e.get("properties") or {}) for e in E3), s3)
    check("an empty graph is a no-op", asyncio.run(sv.validate_with_source([], [], runner))["probes"] == 0, None)
    def chunky(sql):
        return [{"n": 10, **{f"b_{i}": 1 for i in range(200)}}] if "SUM(CASE" in sql else ([{"n": 10}] if sql.startswith("SELECT COUNT") else [])
    E4 = [{"name": "P.T", "entity_type": "entity", "properties": {"deployed_table": "D.T"}}] + [
        {"name": f"P.T.C{i}", "entity_type": "attribute", "properties": {"label": f"C{i}", "deployed_type": "CHAR(1)", "deployed_column": f"D.T.C{i}", "allowed_values": [{"code": "1"}]}} for i in range(150)]
    R4 = [{"relationship_type": "contains", "source_name": "P.T", "source_type": "entity", "target_name": f"P.T.C{i}", "target_type": "attribute"} for i in range(150)]
    calls = []
    def crun(sql):
        calls.append(sql); return chunky(sql)
    asyncio.run(sv.validate_with_source(E4, R4, crun, blank_chunk=120))
    check("blank counts are chunked per statement (150 coded columns → 2 sampled reads, 1 exact count)", len([c for c in calls if "SUM(CASE" in c]) == 2 and len([c for c in calls if c == "SELECT COUNT(*) AS n FROM D.T"]) == 1, [c[:40] for c in calls])

def t_guard_and_enrichment():
    td._reset_all()
    n = td.record_text_dates("s-val", [{"table": "P_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)", "shape": "DD-MMM-YYYY", "sample": "31-OCT-2023"}]}])
    sh = td.known_shape("incident", "INCDATE")
    check("the ontology's sampled shape seeds the guard — no probe round", n == 1 and sh and sh["format"] == "DD-MMM-YYYY" and sh.get("source") == "ontology", sh)
    p, u = td.text_date_violations("s-val", "SELECT TOP 30 INCDATE FROM P_Domain.Incident ORDER BY INCDATE DESC")
    check("…so the first lexical statement is PROVEN, not unverified", u == [] and p and p[0]["shape"] == "DD-MMM-YYYY", (p, u))
    td.learn_shape("incident", "INCDATE", ["2023-10-31"])
    check("result rows disagreeing with the ontology WIN (data over declaration)", td.known_shape("incident", "INCDATE")["format"] == "YYYY-MM-DD", None)
    td.record_text_dates("s-val", [{"table": "P_Domain.Incident", "columns": [{"name": "INCDATE", "shape": "DD-MMM-YYYY"}]}])
    check("re-recording the ontology's shape never overrides a learned one", td.known_shape("incident", "INCDATE")["format"] == "YYYY-MM-DD", None)
    from components.builtin.knowledge_graph import handler as kgh
    props = {"label": "INCDATE", "description": "INCIDENT DATE", "deployed_type": "CHAR(11)", "sampled_shape": "DD-MMM-YYYY", "sample_value": "31-OCT-2023", "date_range": ["2023-01-01", "2023-12-31"]}
    parts = kgh._governance_parts(props, complete=True)
    line = next((x for x in parts if "DATE stored as" in x), "")
    check("the enrichment states the exact CAST and the data span, not the sampling instruction", "CAST(INCDATE AS DATE FORMAT 'DD-MMM-YYYY')" in line and "spans 2023-01-01 to 2023-12-31" in line and "SELECT one value first" not in line and "TRYCAST" not in line, line)
    line = next((x for x in kgh._governance_parts({**props, "date_range_sampled": True, "date_unparseable_ratio": 0.012}, complete=True) if "DATE stored as" in x), "")
    check("with non-date values present the enrichment shows the guarded cast and the share; a sampled span says 'about'", df.safe_cast("INCDATE", "DD-MMM-YYYY") in line and "1.2% of its non-blank values are not dates" in line and "spans about 2023-01-01" in line and "REGEXP_SIMILAR" not in line, line)
    line = next((x for x in kgh._governance_parts({**props, "date_unparseable_ratio": 0.0, "date_unparseable_sampled": True}, complete=True) if "DATE stored as" in x), "")
    check("a SAMPLED zero share still gets a guarded form — the cheap LENGTH guard — and says why", df.length_guarded_cast("INCDATE", "DD-MMM-YYYY") in line and "none seen in the sample" in line and "REGEXP_SIMILAR" not in line, line)
    check("the enrichment names the cheap text forms for period buckets BEFORE the conversion", line.index("period buckets and year/month filters use the TEXT") < line.index("a date comparison"), line)
    parts = kgh._governance_parts({"label": "V4019", "deployed_type": "VARCHAR(2)", "allowed_values": [{"code": "0", "label": "Female"}], "blank_ratio": 0.34}, complete=True)
    check("a coded column's blank ratio is stated", any("blank in 34% of rows" in x for x in parts), parts)
    parts = kgh._governance_parts({"label": "X", "deployed_type": "VARCHAR(2)", "allowed_values": [{"code": "0", "label": "y"}], "blank_ratio": 0.01}, complete=True)
    check("a negligible blank ratio is not noise", not any("blank in" in x for x in parts), parts)
    class Cap:
        def __init__(self, d): self.deployed_as = d
    sub = {"entities": [{"id": 1, "entity_type": "entity", "name": "P.Incident", "properties": {}},
                        {"id": 2, "entity_type": "attribute", "name": "P.Incident.INCDATE", "properties": props}],
           "relationships": [{"relationship_type": "contains", "source_name": "P.Incident", "target_name": "P.Incident.INCDATE"}]}
    tdc = kgh._text_date_columns(sub, {"P.Incident": Cap("P_Domain.Incident")})
    check("the metadata carries the shape, sample AND the measured span to the guard", tdc == [{"table": "P_Domain.Incident", "columns": [{"name": "INCDATE", "type": "CHAR(11)", "shape": "DD-MMM-YYYY", "sample": "31-OCT-2023", "span": ["2023-01-01", "2023-12-31"]}]}], tdc)
    sub["entities"][1]["properties"]["date_unparseable_ratio"] = 0.003
    sub["entities"][1]["properties"]["date_unparseable_sampled"] = True
    c0 = kgh._text_date_columns(sub, {"P.Incident": Cap("P_Domain.Incident")})[0]["columns"][0]
    check("…and the unparseable share (and that it was sampled) when the source showed one", c0["unparseable"] == 0.003 and c0["unparseable_sampled"] is True, c0)
    check("ONE predicate: the handler's text-date test IS the shared module's", kgh._is_text_date_attr is df.is_text_date_attr and kgh._looks_like_date_attr is df.looks_like_date_attr, None)

SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent"
ROOT = pathlib.Path(__file__).resolve().parents[1]
def _calls_in(path, fn):
    import ast
    tree = ast.parse(path.read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")) for c in ast.walk(n) if isinstance(c, ast.Call)}
    return None

def t_every_constructor():
    from trusted_data_agent.agent.kg_constructors import database_context as dbc
    # ANDP: build + resync both validate; the descriptor carries the choice
    andp = SRC / "agent/kg_constructors/andp_semantic.py"
    check("ANDP: construct validates after the walk", "_validate" in (_calls_in(andp, "construct") or set()), None)
    check("ANDP: rebuild (resync) validates too", "_validate" in (_calls_in(andp, "rebuild") or set()), None)
    from trusted_data_agent.agent.kg_constructors import andp_semantic as am
    flat = {"product": "P", "validate_with_source": False, "include_columns": True}
    check("ANDP: the update dialog's override reaches a FLAT descriptor's entry (the REST merge nests it under options)",
          am.entry_options({**flat, "options": {"validate_with_source": True}}, flat)["validate_with_source"] is True
          and am.entry_options(flat, flat)["validate_with_source"] is False
          and "entry_options" in (_calls_in(andp, "rebuild") or set()), None)
    check("ANDP: the step runs the ONE shared core", "validate_with_source" in (_calls_in(andp, "_run") or set()) and '"validate_with_source": bool(opts.get("validate_with_source", False))' in andp.read_text(), None)
    # industry model: after the load, through the deploy backend, re-importing the stamped logical nodes
    im = SRC / "agent/kg_constructors/declarative_model.py"
    if im.exists():  # distribution-aware (the Industry Data Model constructor ships with neither distribution)
        dep = _calls_in(im, "_deploy_pdm") or set()
        check("industry model: the deploy validates against the loaded data through the same core", "validate_with_source" in dep and "import_bulk" in dep, dep)
        check("industry model: construct hands the logical nodes to the deploy so stamps can land", "entities=entities, relationships=relationships" in im.read_text(), None)
    # discovery: an optional run_sql role + the tool-based runner
    check("discovery: run_sql is a resolvable tool role and the prompt asks for it", "run_sql" in dbc.ROLE_KEYS and '"run_sql"' in dbc.tool_role_derivation_messages([{"name": "t"}], "db") and dbc.parse_tool_roles('{"list_tables": {"tool": "a"}, "run_sql": {"tool": "q", "sql_arg": "sql"}}')["run_sql"] == {"tool": "q", "sql_arg": "sql"}, None)
    E, R = discovery_graph()
    seen = []
    async def invoke(tool, args):
        seen.append((tool, args))
        sql = args["sql"]
        if sql.startswith("SELECT COUNT(*) AS n"):
            return {"status": "success", "results": [{"n": 5}]}
        if "groups_n" in sql:
            return {"results": [{"groups_n": 5}]}
        if "SaleDate AS v" in sql:
            return {"results": [{"v": "2024-01-05"}, {"v": "2024-02-06"}]}
        return {"results": []}
    stats = asyncio.run(dbc.validate_graph_via_tool(invoke, {"tool": "base_readQuery", "sql_arg": "sql"}, {"entities": E, "relationships": R}, "Teradata"))
    by = {e["name"]: e["properties"] for e in E if e["entity_type"] in ("entity", "attribute")}
    check("discovery: the runner is the resolved tool, rows read through the constructor's own result parser",
          all(t == "base_readQuery" for t, _ in seen) and stats["tables_counted"] == 1 and by["fitness_db.Sales"]["row_count"] == 5 and by["fitness_db.Sales"]["key_unique"] is True and by["fitness_db.Sales.SaleDate"]["sampled_shape"] == "YYYY-MM-DD", (stats, by))
    check("discovery: no run_sql tool → skipped, and it says so", asyncio.run(dbc.validate_graph_via_tool(invoke, None, {"entities": E, "relationships": R})).get("skipped"), None)
    rr = (SRC / "api/rest_routes.py").read_text()
    check("REST: the discovery path threads the flag to the tool-based validation", "validate_graph_via_tool" in (_calls_in(SRC / "api/rest_routes.py", "_run_deterministic_structural_discovery") or set()) and 'validate_with_source = bool(data.get("validate_with_source", False))' in rr, None)
    check("REST: the ANDP options carry the flag; the update dialog may override it", '"validate_with_source": bool(data.get("validate_with_source", False))' in rr and '"validate_with_source"):' in rr, None)
    # dialogs
    for path, needle in (("static/js/handlers/rag/kgAndpSemanticModal.js", "validate_with_source: _overlay.querySelector('#kg-andp-validate')"),
                         ("static/js/handlers/rag/kgIndustryModelModal.js", "payload.validate_with_source = _overlay.querySelector('#kg-im-validate')"),
                         ("static/js/handlers/rag/kgConstructorModal.js", "_collectDiscoveryTools(), validateWithSource"),
                         ("static/js/api.js", "validate_with_source: true"),
                         ("static/js/handlers/rag/kgUpdateVersions.js", "row('validate_with_source'")):
        if not (ROOT / path).exists():
            continue  # a dialog this distribution does not carry
        check(f"dialog offers the option: {path.split('/')[-1]}", needle in (ROOT / path).read_text(), needle)

def t_stamp_and_card():
    import tempfile, json
    E, R = andp_graph()
    check("has_source_validation: derived from content, false on a bare graph", not sv.has_source_validation(E) and sv.summarize(E)["tables"] == 0, None)
    E[0]["properties"]["row_count"] = 5; E[0]["properties"]["key_unique"] = False
    E[1]["properties"]["sampled_shape"] = "DD-MMM-YYYY"; E[1]["properties"]["date_range"] = ["2023-01-01", "2023-12-31"]; E[3]["properties"]["blank_ratio"] = 0.4
    check("summarize counts what the card states", sv.summarize(E) == {"tables": 1, "keys_checked": 1, "keys_not_unique": 1, "shapes": 1, "date_ranges": 1, "blank_ratios": 1} and sv.has_source_validation(E), sv.summarize(E))
    from components.builtin.knowledge_graph.graph_store import GraphStore
    from trusted_data_agent.auth.database import _migrate_to_multi_kg
    import sqlite3
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.executescript((ROOT / "schema" / "21_knowledge_graph.sql").read_text()); conn.commit()
    _migrate_to_multi_kg(conn); conn.commit(); conn.close()       # the real schema + the real migration
    store = GraphStore(profile_id="p1", user_uuid="u1", kg_id="kg-val", db_path=tmp.name)
    store.set_kg_metadata(name="Val", database_name="d")
    check("no stamp before validation", store.get_source_validation() is None, None)
    check("the stamp is set with validated_at + the counts (real metadata row)", store.set_source_validation(sv.summarize(E)) and store.get_source_validation()["tables"] == 1 and store.get_source_validation()["validated_at"], store.get_source_validation())
    lst = [g for g in GraphStore.list_all_graphs("u1", db_path=tmp.name) if g["kg_id"] == "kg-val"]
    check("the graph list carries the stamp for the card", lst and (lst[0].get("source_validation") or {}).get("tables") == 1, lst and lst[0].get("source_validation"))
    check("clearing removes it (a resync without validation must not leave a stale badge)", store.set_source_validation(None) and store.get_source_validation() is None, None)
    store.set_source_descriptor({"strategy": "andp_semantic", "product": "NIBRS_Source", "validate_with_source": True})
    lbl = [g for g in GraphStore.list_all_graphs("u1", db_path=tmp.name) if g["kg_id"] == "kg-val"][0]["source_label"]
    check("an ANDP graph's source label names its product (not None/None@main)", lbl == "ANDP: NIBRS_Source", lbl)
    check("a multi-product descriptor names every product; an industry model keeps its shape",
          GraphStore.source_label({"strategy": "andp_semantic", "products": [{"product": "A"}, {"product": "B"}]}) == "ANDP: A, B"
          and GraphStore.source_label({"strategy": "industry_model", "industry": "airlines", "model_size": "mvm", "repo_ref": "main"}) == "airlines/mvm@main" and GraphStore.source_label(None) is None, None)
    js = (ROOT / "static/js/handlers/knowledgeGraphPanelHandler.js").read_text()
    check("both card renderers show the Source-validated badge beside the backend badge", js.count("${backendBadge}${_sourceValidationBadge(kg)}") == 2, js.count("${backendBadge}${_sourceValidationBadge(kg)}"))
    check("the badge states WHEN and what it covered, and is absent when never validated", "if (!v || !v.validated_at) return '';" in js and "Validated against the source data on" in js and "took" in js, None)
    rr = (SRC / "api/rest_routes.py").read_text()
    check("check-update reports a FLAT (ANDP) descriptor's validate flag as the dialog's baseline", '_flat = {k: descriptor[k] for k in ("include_columns", "validate_with_source") if k in descriptor}' in rr and '"options": {**_flat, **(descriptor.get("options") or {})}' in rr, None)
    check("apply-update stamps from the candidate's CONTENT (set or cleared) and never rebuilds twice after a check",
          "store.set_source_validation(_summary)" in rr and "_take_candidate(_candidate_key(user_uuid, kg_id, options_override))" in rr and "_keep_candidate(_candidate_key(user_uuid, kg_id, options_override)" in rr, None)
    from trusted_data_agent.api import rest_routes as rrm
    rrm._keep_candidate("k", ("d", [1], [2]))
    check("a checked candidate is taken once, then gone", rrm._take_candidate("k") == ("d", [1], [2]) and rrm._take_candidate("k") is None, None)
    check("the discovery import and the ANDP build stamp too", "set_source_validation(_sv.summarize(structural[\"entities\"]))" in rr and "_stamp_validation" in (_calls_in(SRC / "agent/kg_constructors/andp_semantic.py", "construct") or set()), None)

for fn in (t_targets, t_probes, t_stamping, t_orchestration, t_guard_and_enrichment, t_every_constructor, t_stamp_and_card):
    try: fn()
    except Exception as exc:
        import traceback; traceback.print_exc()
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
