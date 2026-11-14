"""Preflight JOIN-key check (2026-09-04) — the runaway-join class.

Production: an agent joined Incident to Arrestee on BH010/BH012 (batch-header fields both
tables carry) instead of the declared (ORI, INCNUM); the near-cartesian product ran three
hours and queued every other request on the endpoint. The enrichment now names a declared
pair's join columns; this guard refuses, before the database sees it, a JOIN it can PROVE
is off-key. Truth table + fail-open cases + mode ladder + AST wiring. CI-pure."""
import ast, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.agent.preflight import joins, guard
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

SID = "s-joins"
KEYS = [{"tables": ["incident", "arrestee"], "key": ["ORI", "INCNUM"]},
        {"tables": ["incident", "victim"], "key": ["ORI", "INCNUM"]}]
RUNAWAY = ("SELECT COUNT(DISTINCT I.BH010 || I.BH012) AS n FROM NIBRS_Source_Domain.Incident I "
           "JOIN NIBRS_Source_Domain.Arrestee A ON I.BH010 = A.BH010 AND I.BH012 = A.BH012 WHERE I.V20081='2'")
GOOD = ("SELECT COUNT(DISTINCT I.ORI || I.INCNUM) AS n FROM NIBRS_Source_Domain.Incident I "
        "INNER JOIN NIBRS_Source_Domain.Arrestee A ON I.ORI = A.ORI AND I.INCNUM = A.INCNUM WHERE I.V20081='2'")
SUBSET = "SELECT COUNT(*) FROM NIBRS_Source_Domain.Incident I JOIN NIBRS_Source_Domain.Arrestee A ON I.INCNUM = A.INCNUM"
EXTRA = ("SELECT COUNT(*) FROM NIBRS_Source_Domain.Incident I JOIN NIBRS_Source_Domain.Arrestee A "
         "ON I.ORI = A.ORI AND I.INCNUM = A.INCNUM AND A.V6006 = '1'")
UNDECLARED = "SELECT * FROM NIBRS_Source_Domain.Incident I JOIN NIBRS_Source_Domain.Batch_Header B ON I.BH041 = B.BH041"
LEFTJOIN = ("SELECT COUNT(*) FROM NIBRS_Source_Domain.Incident LEFT JOIN NIBRS_Source_Domain.Victim "
            "ON Incident.BH010 = Victim.BH010")

def t_sql_joins():
    js = joins.sql_joins(RUNAWAY)
    check("the joined tables are resolved through their aliases", js and sorted(js[0]["tables"]) == ["arrestee", "incident"], js)
    check("every ON column is collected", js and sorted(set(js[0]["on_columns"])) == ["BH010", "BH012"], js)
    js = joins.sql_joins(LEFTJOIN)
    check("unaliased tables resolve by leaf name", js and sorted(js[0]["tables"]) == ["incident", "victim"], js)
    check("an unparseable statement yields no joins", joins.sql_joins("SELEC FROM JOIN ON") == [], None)
    check("non-string input yields no joins", joins.sql_joins(None) == [] and joins.sql_joins(42) == [], None)

def t_violations():
    joins._reset_all(); joins.record_join_keys(SID, KEYS)
    v = joins.join_violations(SID, RUNAWAY)
    check("the runaway join is a violation naming the declared key", v and v[0]["key"] == ["ORI", "INCNUM"] and v[0]["missing"] == ["ORI", "INCNUM"], v)
    check("the columns actually used are named", v and v[0]["used"] == ["bh010", "bh012"], v)
    check("a join on the declared key passes", joins.join_violations(SID, GOOD) == [], None)
    check("a join on a SUBSET of the key is a violation naming the missing column", [x["missing"] for x in joins.join_violations(SID, SUBSET)] == [["ORI"]], None)
    check("extra predicates beside the whole key are fine", joins.join_violations(SID, EXTRA) == [], None)
    check("a join between tables with no declared pair is not judged", joins.join_violations(SID, UNDECLARED) == [], None)
    check("an off-key LEFT JOIN on unaliased tables is caught too", bool(joins.join_violations(SID, LEFTJOIN)), None)
    check("key matching is case-insensitive", joins.join_violations(SID, GOOD.replace("I.ORI = A.ORI", "i.ori = a.ori")) == [], None)
    check("no recorded keys -> no verdict (fail-open)", joins.join_violations("other-session", RUNAWAY) == [], None)
    check("the block result is recovery-shaped and names the rewrite", "ON incident.ORI = arrestee.ORI AND incident.INCNUM = arrestee.INCNUM" in joins.block_result(v)["data"] and joins.block_result(v)["preflight_join"], joins.block_result(v)["data"])

def t_recording():
    joins._reset_all()
    n = joins.record_from_event(SID, {"enrichments": [{"join_keys": KEYS}]})
    check("keys are recorded from the enrichment event", n == 2, n)
    n2 = joins.record_from_event(SID, {"enrichments": [{"entity_count": 0}]})
    check("an enrichment without keys does not erase earlier keys", n2 == 2 and len(joins.known_pairs(SID)) == 2, (n2, joins.known_pairs(SID)))
    check("a pair with an empty key is never recorded", joins.record_join_keys("s2", [{"tables": ["a", "b"], "key": []}]) == 0 and joins.known_pairs("s2") == {}, None)
    check("malformed events never raise and record nothing", joins.record_from_event("s-fresh", "nope") == 0 and joins.record_from_event("s-fresh", {"enrichments": [None, 3]}) == 0 and joins.known_pairs("s-fresh") == {}, None)
    check("a self-pair is ignored", joins.record_join_keys("s3", [{"tables": ["a", "a"], "key": ["k"]}]) == 0, None)

def t_mode_ladder():
    joins._reset_all(); joins.record_join_keys(SID, KEYS)
    old = getattr(APP_CONFIG, "PREFLIGHT_JOIN_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "observe"
        check("observe (the default) never blocks", guard.check_join_keys(RUNAWAY, SID) is None, None)
        check("the default mode IS observe (byte-identical baseline)", old in ("observe", "enforce", "off") and type(APP_CONFIG).PREFLIGHT_JOIN_MODE.__class__ is str, old)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "enforce"
        r = guard.check_join_keys(RUNAWAY, SID)
        check("enforce blocks the runaway join with the recovery-shaped error", isinstance(r, dict) and r.get("preflight_join") and "declared key" in r["error"], r)
        check("enforce lets the declared-key join through", guard.check_join_keys(GOOD, SID) is None, None)
        r2 = guard.check_sql("base_readQuery", {"sql": RUNAWAY}, SID)
        check("the guard's one entry point (check_sql) applies the join check", isinstance(r2, dict) and r2.get("preflight_join"), r2)
        check("a non-SQL tool call is untouched", guard.check_sql("base_tableList", {"database_name": "x"}, SID) is None, None)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "off"
        check("off is a no-op", guard.check_join_keys(RUNAWAY, SID) is None, None)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "review"
        check("an unknown-but-stronger mode clamps to enforce (never weakens silently)", isinstance(guard.check_join_keys(RUNAWAY, SID), dict), None)
    finally:
        APP_CONFIG.PREFLIGHT_JOIN_MODE = old

def t_metadata_source():
    from components.builtin.knowledge_graph import handler as kgh
    sub = {"entities": [
        {"id": 1, "entity_type": "entity", "name": "P.Incident", "properties": {"business_key": "ORI,INCNUM"}},
        {"id": 2, "entity_type": "entity", "name": "P.Arrestee", "properties": {"business_key": "ORI, INCNUM, V6006"}},
        {"id": 3, "entity_type": "entity", "name": "P.Other", "properties": {}},
    ], "relationships": [
        {"relationship_type": "foreign_key", "source_id": 2, "target_id": 1, "source_name": "P.Arrestee", "source_type": "entity", "target_name": "P.Incident", "target_type": "entity"},
        {"relationship_type": "foreign_key", "source_id": 3, "target_id": 1, "source_name": "P.Other", "source_type": "entity", "target_name": "P.Incident", "target_type": "entity"},
    ]}
    jp = kgh._declared_join_pairs(sub)
    check("the declared pair carries the shared key by leaf", [(j["leaves"], j["key"]) for j in jp] == [(["arrestee", "incident"], ["ORI", "INCNUM"]), (["incident", "other"], [])], jp)
    check("a pair whose tables declare no shared key has an empty key (declared, but not checkable)", jp[1]["key"] == [], None)
    joins._reset_all(); n = joins.record_join_keys(SID, [{"tables": j["leaves"], "key": j["key"]} for j in jp if j["key"]])
    check("the metadata shape feeds the guard directly", n == 1 and joins.join_violations(SID, RUNAWAY)[0]["missing"] == ["ORI", "INCNUM"], None)


# ── the cross-domain join: an instance link a class-level correspondence forbids ──────
# Live on the policing core model (2026-09-15, @PXPROD, session 28fcb86a): asked "how many
# exhibits belong to each robbery?" the agent EXECUTED
#   v_Offence ⋈ v_Incident ⋈ v_ChainOfCustody ON I.incident_number = C.evidence_object_id
# before refusing — the crime and evidence areas share no per-record key, so the join can
# only return coincidence or nothing, and the model's honesty rested on the agent's
# judgement alone. The model DECLARES the partition (subject_area) and the one class-level
# bridge; the guard now reads both. Graphs declaring no subject areas stay silent.
XJOIN = ("SELECT O.incident_number, COUNT(C.exhibit_number) AS exhibit_count "
         "FROM Policing_Core.v_Offence O JOIN Policing_Core.v_Incident I "
         "ON O.ori = I.ori AND O.incident_number = I.incident_number "
         "JOIN Policing_Core.v_ChainOfCustody C ON I.incident_number = C.evidence_object_id "
         "GROUP BY O.incident_number")
WITHIN = ("SELECT COUNT(*) FROM Policing_Core.v_Offence O JOIN Policing_Core.v_Incident I "
          "ON O.ori = I.ori AND O.incident_number = I.incident_number WHERE O.offence_code = '120'")
EVIDENCE = ("SELECT COUNT(*) FROM Policing_Core.v_CaseObject c JOIN Policing_Core.v_ChainOfCustody x "
            "ON c.object_id = x.evidence_object_id")
AREAS = {"v_offence": "crime", "v_incident": "crime", "v_caseobject": "evidence", "v_chainofcustody": "evidence"}
BRIDGES = [{"name": "Offence__CaseObject", "tables": ["v_caseobject", "v_offence"],
            "areas": ["crime", "evidence"], "basis": "the Incident Profile"}]
CORE_KEYS = [{"tables": ["v_offence", "v_incident"], "key": ["ori", "incident_number"]},
             {"tables": ["v_caseobject", "v_chainofcustody"], "key": ["object_id"]}]

def t_cross_domain():
    joins._reset_all(); joins.record_join_keys(SID, CORE_KEYS); joins.record_domains(SID, AREAS, BRIDGES)
    v = joins.cross_domain_violations(SID, XJOIN)
    check("the live cross-area join is a violation (every crime table it relates to the evidence one)",
          v and all(sorted(x["areas"]) == ["crime", "evidence"] and "v_chainofcustody" in x["tables"] for x in v)
          and {t for x in v for t in x["tables"]} == {"v_chainofcustody", "v_incident", "v_offence"}, v)
    check("...naming both areas", v and sorted(v[0]["areas"]) == ["crime", "evidence"], v)
    check("...and the class-level bridge the model DOES declare between those areas", v and all(x["bridge"] == "Offence__CaseObject" for x in v), v)
    check("a within-area join is not a violation", joins.cross_domain_violations(SID, WITHIN) == [], None)
    check("a within-area join on the OTHER side is not either", joins.cross_domain_violations(SID, EVIDENCE) == [], None)
    # a DECLARED cross-area key permits the join — the partition never overrides a declaration
    joins.record_join_keys(SID, [{"tables": ["v_incident", "v_chainofcustody"], "key": ["incident_number"]}])
    check("a declared cross-area key makes the same join legitimate — and relates the third "
          "table THROUGH it (a chain of declared keys is a declared relation)",
          joins.cross_domain_violations(SID, XJOIN) == [], joins.cross_domain_violations(SID, XJOIN))
    joins._reset_all(); joins.record_domains(SID, {"v_offence": "crime"}, BRIDGES)
    check("one side's area unknown → no verdict (fail-open)", joins.cross_domain_violations(SID, XJOIN) == [], None)
    joins._reset_all(); joins.record_join_keys(SID, CORE_KEYS)
    check("no areas recorded (an ANDP / industry / discovery graph) → silent", joins.cross_domain_violations(SID, XJOIN) == [], None)
    joins._reset_all(); joins.record_domains(SID, AREAS, [])
    v = joins.cross_domain_violations(SID, XJOIN)
    check("with no bridge declared the violation still fires, naming no bridge", v and v[0]["bridge"] is None, v)
    blk = joins.cross_domain_block_result(joins.cross_domain_violations(SID, XJOIN))
    check("the block is recovery-shaped: names both areas, says the query did NOT run",
          blk["preflight_cross_domain"] and "crime" in blk["data"] and "evidence" in blk["data"] and "NOT executed" in blk["data"], blk)
    joins.record_domains(SID, AREAS, BRIDGES)
    blk = joins.cross_domain_block_result(joins.cross_domain_violations(SID, XJOIN))
    check("...and with a bridge declared, tells the agent to READ it (kinds, never rows)",
          "Offence__CaseObject" in blk["data"] and "KINDS" in blk["data"], blk["data"])

    # ── "related" is any co-presence in one SELECT scope, not only an ON clause ──────
    # Live 2026-09-15: refused on the keyed join, the agent's self-correction wrote a
    # CROSS JOIN — no ON, nothing for the join scan to see — and reported "44 exhibits
    # per robbery" (every robbery × the 44 evidence objects). Every way of relating two
    # areas in a scope is the same forbidden link; every way of merely READING both is not.
    joins._reset_all(); joins.record_join_keys(SID, CORE_KEYS); joins.record_domains(SID, AREAS, BRIDGES)
    RELATES = {
        "CROSS JOIN": "SELECT O.incident_number, CO.object_id FROM Policing_Core.v_Offence O CROSS JOIN Policing_Core.v_CaseObject CO WHERE O.offence_code = '120'",
        "comma list": "SELECT COUNT(*) FROM Policing_Core.v_Offence O, Policing_Core.v_CaseObject CO",
        "ON 1=1": "SELECT COUNT(*) FROM Policing_Core.v_Offence O JOIN Policing_Core.v_CaseObject CO ON 1=1",
        "correlated EXISTS": "SELECT COUNT(*) FROM Policing_Core.v_Offence O WHERE EXISTS (SELECT 1 FROM Policing_Core.v_CaseObject CO WHERE CO.source_bundle_id = O.incident_number)",
        "keyed join on an undeclared key": "SELECT COUNT(*) FROM Policing_Core.v_Offence O JOIN Policing_Core.v_CaseObject CO ON CO.source_bundle_id = O.incident_number",
    }
    for name, sql in RELATES.items():
        v = joins.cross_domain_violations(SID, sql)
        check(f"a cross-area {name} is refused, naming the bridge to read",
              len(v) == 1 and v[0]["bridge"] == "Offence__CaseObject", (name, v))
    READS_BOTH = {
        "UNION ALL of two single-area populations": "SELECT 'a' AS p, COUNT(*) FROM Policing_Core.v_Offence UNION ALL SELECT 'b', COUNT(*) FROM Policing_Core.v_CaseObject",
        "two scalar subqueries": "SELECT (SELECT COUNT(*) FROM Policing_Core.v_Offence) AS a, (SELECT COUNT(*) FROM Policing_Core.v_CaseObject) AS b",
        "a within-area correlated EXISTS": "SELECT COUNT(*) FROM Policing_Core.v_Incident I WHERE EXISTS (SELECT 1 FROM Policing_Core.v_Offence O WHERE O.ori = I.ori AND O.incident_number = I.incident_number)",
    }
    for name, sql in READS_BOTH.items():
        check(f"{name} relates nothing and passes", joins.cross_domain_violations(SID, sql) == [], (name, joins.cross_domain_violations(SID, sql)))
    check("a scope lists its own tables plus its enclosing scopes' (the correlation)",
          any(sorted(sc) == ["v_caseobject", "v_offence"] for sc in joins.sql_scope_tables(RELATES["correlated EXISTS"])),
          joins.sql_scope_tables(RELATES["correlated EXISTS"]))
    check("a UNION branch is its own scope", all(len(sc) == 1 for sc in joins.sql_scope_tables(READS_BOTH["UNION ALL of two single-area populations"])),
          joins.sql_scope_tables(READS_BOTH["UNION ALL of two single-area populations"]))

def t_cross_domain_ladder():
    joins._reset_all(); joins.record_join_keys(SID, CORE_KEYS); joins.record_domains(SID, AREAS, BRIDGES)
    old = getattr(APP_CONFIG, "PREFLIGHT_JOIN_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "observe"
        check("observe: the cross-domain join runs (logged, never blocked)", guard.check_join_keys(XJOIN, SID) is None, None)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "enforce"
        r = guard.check_join_keys(XJOIN, SID)
        check("enforce: the cross-domain join is refused through the SAME ladder as the key check",
              isinstance(r, dict) and r.get("preflight_cross_domain") is True, r)
        check("enforce: a within-area join still passes", guard.check_join_keys(WITHIN, SID) is None, None)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "off"
        check("off: nothing is judged", guard.check_join_keys(XJOIN, SID) is None, None)
    finally:
        APP_CONFIG.PREFLIGHT_JOIN_MODE = old

def t_cross_domain_metadata_source():
    """The partition and the bridge are derived ONCE by the handler from the graph, and
    the enrichment event carries them to the recorder (the join_keys precedent)."""
    from components.builtin.knowledge_graph import handler as kgh
    sub = {"entities": [
        {"id": 1, "entity_type": "entity", "name": "Policing Core.Offence",
         "properties": {"subject_area": "crime", "deployed_table": "Policing_Core.v_Offence"}},
        {"id": 2, "entity_type": "entity", "name": "Policing Core.CaseObject",
         "properties": {"subject_area": "evidence", "deployed_table": "Policing_Core.v_CaseObject"}},
        {"id": 3, "entity_type": "entity", "name": "P.NoArea", "properties": {"deployed_table": "P.t"}},
        {"id": 4, "entity_type": "product_bridge", "name": "Policing Core.bridge.Offence__CaseObject",
         "properties": {"label": "Offence__CaseObject", "linkage_mechanism": "type_correspondence", "basis": "the Incident Profile"}},
        {"id": 5, "entity_type": "product_bridge", "name": "X.bridge.k", "properties": {"label": "k", "linkage_mechanism": "shared_key"}},
    ], "relationships": [
        {"relationship_type": "relates_to", "source_name": "Policing Core.bridge.Offence__CaseObject", "source_type": "product_bridge", "target_name": "Policing Core.Offence", "target_type": "entity"},
        {"relationship_type": "relates_to", "source_name": "Policing Core.bridge.Offence__CaseObject", "source_type": "product_bridge", "target_name": "Policing Core.CaseObject", "target_type": "entity"},
    ]}
    areas = kgh._subject_area_map(sub)
    check("the partition is keyed by DEPLOYED leaf, lower-cased, area lower-cased", areas == {"v_offence": "crime", "v_caseobject": "evidence"}, areas)
    check("an entity without a subject area is not in the partition", "t" not in areas, None)
    br = kgh._class_bridges(sub)
    check("only type_correspondence bridges are class bridges, with their leaves and areas",
          br == [{"name": "Offence__CaseObject", "tables": ["v_caseobject", "v_offence"], "areas": ["crime", "evidence"], "basis": "the Incident Profile"}], br)
    # the ends resolve from the bridge's OWN declaration when its edges are not in the
    # relevance-scoped subgraph (live: class_bridges shipped with tables=[] areas=[])
    sub_noedges = {"entities": sub["entities"], "relationships": []}
    for e in sub_noedges["entities"]:
        if e["id"] == 4:
            e["properties"].update({"from_object_class": "Offence", "to_object_class": "CaseObject"})
    br2 = kgh._class_bridges(sub_noedges)
    check("a bridge resolves its ends from from/to_object_class when its edges are absent",
          br2 and br2[0]["tables"] == ["v_caseobject", "v_offence"] and br2[0]["areas"] == ["crime", "evidence"], br2)
    # join_keys are keyed by the DEPLOYED leaf the SQL names, not the logical entity's
    jsub = {"entities": [
        {"id": 1, "entity_type": "entity", "name": "Policing Core.Incident",
         "properties": {"business_key": "ori, incident_number", "deployed_table": "Policing_Core.v_Incident"}},
        {"id": 2, "entity_type": "entity", "name": "Policing Core.Offence",
         "properties": {"business_key": "ori, incident_number, offence_seq", "deployed_table": "Policing_Core.v_Offence"}},
    ], "relationships": [
        {"relationship_type": "foreign_key", "source_name": "Policing Core.Incident", "source_type": "entity",
         "target_name": "Policing Core.Offence", "target_type": "entity"}]}
    jp = kgh._declared_join_pairs(jsub)
    check("declared join pairs are keyed by the deployed leaf (v_incident), so the guard matches the SQL",
          jp and sorted(jp[0]["leaves"]) == ["v_incident", "v_offence"] and jp[0]["key"] == ["ori", "incident_number"], jp)
    joins._reset_all()
    joins.record_from_event(SID, {"enrichments": [{"join_keys": [], "subject_areas": areas, "class_bridges": br}]})
    check("the event shape feeds the recorder directly", joins.known_areas(SID) == areas and joins.known_bridges(SID) == br, None)
    h = (pathlib.Path(__file__).resolve().parents[1] / "components/builtin/knowledge_graph/handler.py").read_text()
    check("the enrichment stamps both beside join_keys", 'metadata["subject_areas"] = _subject_area_map(subgraph)' in h and 'metadata["class_bridges"] = _class_bridges(subgraph)' in h, None)

SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent"
def _calls_in(path, fn):
    tree = ast.parse(path.read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")) for c in ast.walk(n) if isinstance(c, ast.Call)}
    return None

def t_wiring():
    check("check_sql runs the join check", "check_join_keys" in (_calls_in(SRC / "agent/preflight/guard.py", "check_sql") or set()), None)
    check("the join check consults the cross-domain rule", "cross_domain_violations" in (_calls_in(SRC / "agent/preflight/guard.py", "check_join_keys") or set()), None)
    check("the executor records the keys where the enrichment event is stamped", "maybe_record_join_keys" in (_calls_in(SRC / "agent/executor.py", "_apply_trusted_scope") or set()), None)
    h = (pathlib.Path(__file__).resolve().parents[1] / "components/builtin/knowledge_graph/handler.py").read_text()
    check("the renderer and the metadata share ONE pair computation", h.count("_declared_join_pairs(subgraph)") >= 2, h.count("_declared_join_pairs(subgraph)"))

# ── the FK-holder fan-out (2026-09-19): two tables joined to EACH OTHER on a key both hold as
# a FOREIGN key to a third — prod: mileage_redemption ⋈ member_benefit ON ffp_member_id read
# 2,638,492 miles redeemed for a member whose true figure is 17,708.
FANOUT = [{"column": "ffp_member_id", "owner": "ffp_member",
           "tables": ["member_benefit", "mileage_accrual", "mileage_redemption", "miles_balance"]}]
FAN_SQL = ("SELECT TOP 5 R.ffp_member_id, SUM(R.miles_redeemed) AS total_redeemed, B.tier_status_id "
           "FROM airlines_core.mileage_redemption R JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id "
           "GROUP BY R.ffp_member_id, B.tier_status_id")
FAN_OWNER = ("SELECT R.ffp_member_id, SUM(R.miles_redeemed) FROM airlines_core.mileage_redemption R "
             "JOIN airlines_core.ffp_member M ON R.ffp_member_id = M.ffp_member_id GROUP BY 1")
FAN_AGG = ("SELECT a.ffp_member_id, a.redeemed, b.benefits FROM (SELECT ffp_member_id, SUM(miles_redeemed) AS redeemed "
           "FROM airlines_core.mileage_redemption GROUP BY 1) a JOIN (SELECT ffp_member_id, COUNT(*) AS benefits "
           "FROM airlines_core.member_benefit GROUP BY 1) b ON a.ffp_member_id = b.ffp_member_id")
FAN_OTHER_COL = ("SELECT * FROM airlines_core.mileage_redemption R JOIN airlines_core.member_benefit B "
                 "ON R.general_ledger_id = B.general_ledger_id")

def t_fanout():
    joins._reset_all(); joins.record_fanout(SID, FANOUT)
    v = joins.fanout_violations(SID, FAN_SQL)
    check("the prod statement is a fan-out violation naming both holders, the column and the owner",
          v and sorted(v[0]["tables"]) == ["member_benefit", "mileage_redemption"] and v[0]["column"] == "ffp_member_id"
          and v[0]["owner"] == "ffp_member", v)
    check("joining a holder to the OWNER is never a violation", joins.fanout_violations(SID, FAN_OWNER) == [], None)
    check("aggregating each per key and joining the aggregates is never a violation (derived tables, not the holders)",
          joins.fanout_violations(SID, FAN_AGG) == [], joins.fanout_violations(SID, FAN_AGG))
    check("the same two holders on a column NOT recorded as a fan-out key → no verdict", joins.fanout_violations(SID, FAN_OTHER_COL) == [], None)
    # Derived-table aliases that REUSE the base names — `(…) AS mileage_redemption JOIN (…) AS
    # member_benefit` — are a join between two aggregates, not between the holders. A qualifier
    # resolves against the join's own scope, and a derived alias proves nothing (2026-09-19: the
    # guard read the aliases as the base tables and refused the very rewrite it had asked for).
    FAN_AGG_SAMENAME = ("SELECT mileage_redemption.ffp_member_id, mileage_redemption.redeemed, member_benefit.benefits "
                        "FROM (SELECT ffp_member_id, SUM(miles_redeemed) AS redeemed FROM airlines_core.mileage_redemption GROUP BY 1) AS mileage_redemption "
                        "JOIN (SELECT ffp_member_id, COUNT(*) AS benefits FROM airlines_core.member_benefit GROUP BY 1) AS member_benefit "
                        "ON mileage_redemption.ffp_member_id = member_benefit.ffp_member_id")
    check("derived tables aliased with the holders' own names are still derived tables — no verdict",
          joins.fanout_violations(SID, FAN_AGG_SAMENAME) == [] and joins.sql_joins(FAN_AGG_SAMENAME) == [], joins.sql_joins(FAN_AGG_SAMENAME))
    FAN_MIXED = ("SELECT * FROM airlines_core.mileage_redemption R JOIN (SELECT ffp_member_id FROM airlines_core.member_benefit GROUP BY 1) B "
                 "ON R.ffp_member_id = B.ffp_member_id")
    check("a holder joined to a DERIVED table of the other holder is not a fan-out (one side is pre-aggregated)",
          joins.fanout_violations(SID, FAN_MIXED) == [], joins.fanout_violations(SID, FAN_MIXED))
    check("…while the same statement over the BASE tables still is", bool(joins.fanout_violations(SID, FAN_SQL)), None)
    NESTED = ("SELECT * FROM airlines_core.mileage_redemption R JOIN airlines_core.member_benefit B ON R.ffp_member_id = B.ffp_member_id "
              "WHERE R.ffp_member_id IN (SELECT M.ffp_member_id FROM airlines_core.ffp_member M JOIN airlines_core.miles_balance X ON M.ffp_member_id = X.ffp_member_id)")
    check("each JOIN is judged in its own scope: the outer fan-out is caught, the inner holder→owner join is not a second one",
          [sorted(v["tables"]) for v in joins.fanout_violations(SID, NESTED)] == [["member_benefit", "mileage_redemption"]], joins.fanout_violations(SID, NESTED))
    joins.record_join_keys(SID, [{"tables": ["mileage_redemption", "member_benefit"], "key": ["ffp_member_id"]}])
    check("a DECLARED pair between the two holders makes the join legitimate — a declaration always wins",
          joins.fanout_violations(SID, FAN_SQL) == [], None)
    joins._reset_all()
    check("nothing recorded → silent (fail-open)", joins.fanout_violations(SID, FAN_SQL) == [], None)
    joins.record_fanout(SID, [{"column": "x", "tables": ["only_one"]}, "junk", {"column": "", "tables": ["a", "b"]}])
    check("a one-holder or malformed entry is never recorded", joins.known_fanout(SID) == {}, joins.known_fanout(SID))
    # read from the enrichment event, beside the join keys (one seam)
    joins._reset_all()
    joins.record_from_event(SID, {"enrichments": [{"join_keys": KEYS, "fanout_keys": FANOUT}]})
    check("record_from_event takes the fan-out keys from the enrichment metadata", "ffp_member_id" in joins.known_fanout(SID), joins.known_fanout(SID))
    blk = joins.fanout_block_result(joins.fanout_violations(SID, FAN_SQL))
    check("the block is recovery-shaped: names the owner, the aggregate-then-join remedy, and that nothing ran",
          blk.get("preflight_fanout") and "ffp_member" in blk["data"] and "aggregate" in blk["data"].lower()
          and "NOT executed" in blk["data"] and "member_benefit" in blk["error"], blk)

def t_fanout_ladder():
    old = APP_CONFIG.PREFLIGHT_JOIN_MODE
    try:
        joins._reset_all(); joins.record_fanout(SID, FANOUT)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "enforce"
        r = guard.check_sql("base_readQuery", {"sql": FAN_SQL}, SID)
        check("enforce refuses the fan-out join at the chokepoint", isinstance(r, dict) and r.get("preflight_fanout"), r)
        check("enforce lets the join to the owner through", guard.check_sql("base_readQuery", {"sql": FAN_OWNER}, SID) is None, None)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "observe"
        check("observe logs, never blocks", guard.check_sql("base_readQuery", {"sql": FAN_SQL}, SID) is None, None)
        APP_CONFIG.PREFLIGHT_JOIN_MODE = "off"
        check("off is silent", guard.check_sql("base_readQuery", {"sql": FAN_SQL}, SID) is None, None)
    finally:
        APP_CONFIG.PREFLIGHT_JOIN_MODE = old
    check("the join check consults the fan-out rule", "fanout_violations" in (_calls_in(SRC / "agent/preflight/guard.py", "check_join_keys") or set()), None)
    h = (pathlib.Path(__file__).resolve().parents[1] / "components/builtin/knowledge_graph/handler.py").read_text()
    check("the renderer and the metadata share ONE fan-out computation", h.count("_fanout_keys(subgraph)") >= 2, h.count("_fanout_keys(subgraph)"))

for fn in (t_sql_joins, t_violations, t_recording, t_mode_ladder, t_metadata_source, t_wiring,
           t_cross_domain, t_cross_domain_ladder, t_cross_domain_metadata_source, t_fanout, t_fanout_ladder):
    try: fn()
    except Exception as exc:
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
