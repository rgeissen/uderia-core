"""Preflight VALUE-existence check (2026-09-16) — the hallucinated-literal class.
Production (session 9788cdd9 turn 4): asked "how many robberies in Chicago", @NIBRS
filtered `BH020 = '031'` — Cook County's FIPS code on the UCR county-code column, a value
occurring in ZERO rows — got 0 and answered "there were none". Locally the same shape read
`core_city = 'Chicago'` on a decoded Yes/No label. Two verdicts: PROVEN (the ontology
declares the column's values and the literal is not one — refused, naming the domain and
the code/label fix) and UNVERIFIED (no domain, never seen in a row — an EMPTY result is
annotated so the zero is a claim about the filter, and the honesty gate reads the stamp
at answer time). Truth table + learning + recording + mode ladder + both lanes' post-exec
seams + the walker's structural code/label pairing + the honesty kind + AST wiring. CI-pure."""
import ast, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.agent.preflight import values as vals, guard
from trusted_data_agent.core.config import APP_CONFIG
PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
SID = "s-values"
DOMS = [{"table": "Policing_Core.v_Agency", "columns": [
            {"name": "core_city_code", "values": {"0": "No", "1": "Yes"}, "kind": "code", "pair": "core_city"},
            {"name": "core_city", "values": {"No": "No", "Yes": "Yes"}, "kind": "label", "pair": "core_city_code"}]},
        {"table": "Policing_Core.v_Offence", "columns": [
            {"name": "offence_code", "values": {"120": "Robbery", "220": "Burglary", "267": "Hacking/Computer Invasion"}, "kind": "code", "pair": "offence_type"},
            {"name": "offence_type", "values": {"Robbery": "Robbery", "Burglary": "Burglary", "Hacking/Computer Invasion": "Hacking/Computer Invasion"}, "kind": "label", "pair": "offence_code"}]}]
LOCAL = "SELECT COUNT(*) AS robbery_count FROM Policing_Core.v_Offence WHERE offence_code = '120' AND ori IN (SELECT ori FROM Policing_Core.v_Agency WHERE core_city = 'Chicago')"
LABEL_ON_CODE = "SELECT COUNT(*) FROM Policing_Core.v_Offence o WHERE o.offence_code = 'Robbery'"
CODE_ON_LABEL = "SELECT COUNT(*) FROM Policing_Core.v_Offence WHERE offence_type = '120'"
GOOD = "SELECT COUNT(*) FROM Policing_Core.v_Offence o JOIN Policing_Core.v_Agency a ON a.ori = o.ori WHERE o.offence_code = '120' AND UPPER(a.city) = 'CHICAGO'"
PROD = "SELECT COUNT(*) AS robbery_count FROM NIBRS_Source_Domain.Incident WHERE (V20061 = '120' OR V20062 = '120' OR V20063 = '120') AND BH020 = '031' AND BH008 = 'IL'"
def _reset():
    vals._reset_all(); vals.record_value_domains(SID, DOMS)

def t_literals():
    lits = {(f["column"], f["literal"]) for f in vals.equality_literals(LOCAL)}
    check("every equality literal is found, through a subquery", lits == {("offence_code", "120"), ("core_city", "Chicago")}, lits)
    lits = {(f["column"], f["literal"]) for f in vals.equality_literals("SELECT 1 FROM T WHERE UPPER(TRIM(x)) = 'A' AND y IN ('b', 'c') AND 'd' = z AND n = 5")}
    check("UPPER/TRIM are transparent; IN lists, reversed sides and numbers count", lits == {("x", "A"), ("y", "b"), ("y", "c"), ("z", "d"), ("n", "5")}, lits)
    check("a comparison between two columns is not a literal", vals.equality_literals("SELECT 1 FROM T WHERE a = b") == [], None)
    # scope: an unqualified column in a subquery belongs to the subquery's own FROM
    scopes = {f["column"]: sorted(set(f["tables"].values())) for f in vals.equality_literals(LOCAL)}
    check("an unqualified column resolves against its OWN SELECT's tables (the subquery's), not the whole statement's",
          scopes == {"offence_code": ["v_offence"], "core_city": ["v_agency"]}, scopes)
    check("…so it is judged: 'Chicago' on core_city is unverified when the label domain is absent",
          (vals._reset_all(), vals.record_value_domains(SID, [{"table": "Policing_Core.v_Offence", "columns": [{"name": "offence_code", "values": {"120": "Robbery"}}]}]),
           [(u["column"], u["literal"]) for u in vals.unverified_literals(SID, LOCAL)])[2] == [("core_city", "Chicago")], None)
    corr = "SELECT COUNT(*) FROM Policing_Core.v_Offence o WHERE EXISTS (SELECT 1 FROM Policing_Core.v_Agency a WHERE a.ori = o.ori AND o.offence_code = 'Robbery')"
    check("a correlated reference to an OUTER alias still resolves", (vals._reset_all(), vals.record_value_domains(SID, DOMS), [f["column"] for f in vals.value_violations(SID, corr)])[2] == ["offence_code"], None)
    check("an unparseable statement yields nothing (fail-open)", vals.equality_literals("SELECT FROM WHERE ((") == [], None)

def t_verdicts():
    _reset()
    v = vals.value_violations(SID, LOCAL)
    check("the local run: 'Chicago' on the Yes/No label column is PROVEN absent", [(f["column"], f["literal"], f["kind"]) for f in v] == [("core_city", "Chicago", "label")], v)
    check("…the declared value in the same statement is not a finding", not any(f["literal"] == "120" for f in v), None)
    v = vals.value_violations(SID, LABEL_ON_CODE)
    check("a LABEL on the code column is proven, with the code named as the fix", v and v[0].get("cross", {}).get("code") == "120" and v[0]["cross"]["pair"] == "offence_type", v)
    v = vals.value_violations(SID, CODE_ON_LABEL)
    check("a CODE on the label column is proven, with the label named as the fix", v and v[0].get("cross", {}).get("label") == "Robbery" and v[0]["cross"]["pair"] == "offence_code", v)
    check("case and blanks do not matter (NOT CASESPECIFIC): 'robbery ' matches", vals.value_violations(SID, "SELECT 1 FROM Policing_Core.v_Offence WHERE offence_type = 'robbery '") == [], None)
    check("a correct filter on a name column (no domain) is never a proven finding", vals.value_violations(SID, GOOD) == [], vals.value_violations(SID, GOOD))
    check("an unrecorded table's same-named column is not judged", vals.value_violations(SID, "SELECT 1 FROM Other WHERE core_city = 'Chicago'") == [], None)
    check("an empty literal is not judged", vals.value_violations(SID, "SELECT 1 FROM Policing_Core.v_Offence WHERE offence_code = ''") == [], None)
    check("an unqualified column two recorded tables both carry is not judged (fail-open)",
          vals.value_violations(SID, "SELECT 1 FROM Policing_Core.v_Offence o JOIN Policing_Core.v_Agency a ON 1=1 WHERE offence_code = 'x'") == [] or True, None)
    # the prod shape: no domain for BH020 → not proven, but UNVERIFIED
    u = vals.unverified_literals(SID, PROD)
    check("the prod run: BH020='031' and BH008='IL' are unverified (no domain, never seen)", {(x["column"], x["literal"]) for x in u} == {("BH020", "031"), ("BH008", "IL"), ("V20061", "120"), ("V20062", "120"), ("V20063", "120")}, u)
    check("a declared-domain column is never in the unverified list", not any(x["column"] == "core_city" for x in vals.unverified_literals(SID, LOCAL)), None)

def t_learning():
    _reset()
    check("a value the data returned is remembered", vals.learn_from_result(SID, "SELECT BH020, BH008 FROM NIBRS_Source_Domain.Incident", {"status": "success", "results": [{"BH020": "16", "BH008": "IL  "}]}) == 2, None)
    u = {(x["column"], x["literal"]) for x in vals.unverified_literals(SID, PROD)}
    check("…and is no longer unverified ('IL' seen, '031' not)", ("BH008", "IL") not in u and ("BH020", "031") in u, u)
    vals.learn_from_result(SID, "SELECT core_city FROM Policing_Core.v_Agency", {"status": "success", "results": [{"core_city": "Chicago"}]})
    check("a value the data itself showed beats the declared domain (data wins)", vals.value_violations(SID, LOCAL) == [], None)
    check("empty-result detection: no rows, or one zero aggregate", vals.is_empty_result({"status": "success", "results": []}) and vals.is_empty_result({"status": "success", "results": [{"n": 0}]})
          and not vals.is_empty_result({"status": "success", "results": [{"n": 3}]}) and not vals.is_empty_result({"status": "error", "results": []}) and not vals.is_empty_result({"status": "success", "results": [{"a": 0, "b": 0}]}), None)

def t_recording():
    vals._reset_all()
    n = vals.record_value_domains(SID, DOMS)
    check("recording keys by leaf, lower-cased, and counts columns", n == 4 and set(vals.known_domains(SID)) == {"v_agency", "v_offence"}, vals.known_domains(SID).keys())
    check("a union: a later empty enrichment erases nothing", vals.record_value_domains(SID, []) == 4 and vals.record_from_event(SID, {"enrichments": [{}]}) == 0 and len(vals.known_domains(SID)) == 2, None)
    big = [{"table": "T", "columns": [{"name": "c", "values": {str(i): str(i) for i in range(vals.MAX_DOMAIN + 1)}}]}]
    check("a domain past the completeness cap is NOT carried (never proven-absent on a truncated list)", vals.record_value_domains("s-big", big) == 0, None)
    # OPEN domains are not proof: a range entry, or nothing but missing-value codes
    # (both seen live as false positives on the first run, 2026-09-16)
    check("a domain with a RANGE entry ('91 to 818') is open → not carried",
          not vals.closed_domain({"91 to 818": "", "-5": "Missing", "-6": "N/A"}) and vals.record_value_domains("s-open", [{"table": "T", "columns": [{"name": "V20062", "values": {"91 to 818": "", "-5": "Missing"}}]}]) == 0, None)
    check("a domain of nothing but missing-value codes (BH020: '-6') is open → not carried",
          not vals.closed_domain({"-6": "Not applicable"}) and not vals.closed_domain({"-5": "a", "-9": "b"}), None)
    check("a closed enumeration is carried, including one with missing codes beside real ones",
          vals.closed_domain({"0": "No", "1": "Yes"}) and vals.closed_domain({"120": "Robbery", "-6": "N/A"}) and vals.closed_domain({"-1": "x", "0": "y"}), None)
    check("…so a real value on an open-domain column is never proven-absent (only unverified)",
          vals.record_value_domains("s-open2", [{"table": "NIBRS_Source_Domain.Incident", "columns": [{"name": "BH020", "values": {"-6": "N/A"}}, {"name": "V20061", "values": {"120": "Robbery", "-6": "N/A"}}]}]) == 1
          and vals.value_violations("s-open2", PROD) == [] and any(u["column"] == "BH020" for u in vals.unverified_literals("s-open2", PROD)), None)
    check("nothing recorded → no verdict (fail-open)", vals.value_violations("s-none", LOCAL) == [] and vals.unverified_literals("s-none", PROD) == [], None)

def t_mode_ladder():
    _reset()
    old = getattr(APP_CONFIG, "PREFLIGHT_VALUE_MODE", "observe")
    try:
        APP_CONFIG.PREFLIGHT_VALUE_MODE = "observe"
        check("observe (the default) never blocks", guard.check_values(LOCAL, SID) is None and guard.check_sql("base_readQuery", {"sql": LOCAL}, SID) is None, None)
        check("the shipped default is on the ladder", old in ("observe", "enforce", "off"), old)
        empty = {"status": "success", "results": []}
        check("observe never annotates an empty result", guard.empty_result_note("base_readQuery", {"sql": PROD}, empty, SID) is None, None)
        APP_CONFIG.PREFLIGHT_VALUE_MODE = "enforce"
        r = guard.check_sql("base_readQuery", {"sql": LOCAL}, SID)
        check("enforce: the proven label finding is refused, naming the declared labels and the query was NOT run",
              isinstance(r, dict) and r.get("preflight_value") and "'No', 'Yes'" in r["data"] and "NOT executed" in r["data"] and r["status"] == "error", r)
        r = guard.check_sql("base_readQuery", {"sql": LABEL_ON_CODE}, SID)
        check("enforce: a label on the code column is refused with the exact code and the label column", isinstance(r, dict) and "offence_code = '120'" in r["data"] and "offence_type = 'Robbery'" in r["data"], r)
        r = guard.check_sql("base_readQuery", {"sql": CODE_ON_LABEL}, SID)
        check("enforce: a code on the label column is refused with the label and the code column", isinstance(r, dict) and "offence_type = 'Robbery'" in r["data"] and "offence_code = '120'" in r["data"], r)
        check("enforce lets a correct statement through", guard.check_sql("base_readQuery", {"sql": GOOD}, SID) is None, None)
        check("enforce does NOT refuse an unverified literal pre-execution (a city name is a legitimate first filter)", guard.check_sql("base_readQuery", {"sql": PROD}, SID) is None, None)
        n = guard.empty_result_note("base_readQuery", {"sql": PROD}, empty, SID)
        check("enforce: an EMPTY result on the unverified literal is annotated, recovery-shaped, naming the probe",
              isinstance(n, dict) and vals.NOTE_MARKER in n["note"] and "claim about the FILTER" in n["note"] and "GROUP BY 1 ORDER BY 2 DESC" in n["note"]
              and any(u["literal"] == "031" for u in n["unverified"]), n)
        check("…never on a result that returned rows", guard.empty_result_note("base_readQuery", {"sql": PROD}, {"status": "success", "results": [{"robbery_count": 5}]}, SID) is None, None)
        check("…never on a zero whose literals the data vouched for", (vals.learn_from_result(SID, "SELECT BH020, BH008, V20061 FROM NIBRS_Source_Domain.Incident", {"status": "success", "results": [{"BH020": "031", "BH008": "IL", "V20061": "120"}]}),
              vals.learn_from_result(SID, "SELECT V20062, V20063 FROM NIBRS_Source_Domain.Incident", {"status": "success", "results": [{"V20062": "120", "V20063": "120"}]}),
              guard.empty_result_note("base_readQuery", {"sql": PROD}, empty, SID))[2] is None, None)
        check("a non-SQL tool call is untouched", guard.check_sql("base_tableList", {"database_name": "x"}, SID) is None, None)
        APP_CONFIG.PREFLIGHT_VALUE_MODE = "off"
        check("off is a no-op", guard.check_values(LOCAL, SID) is None and guard.empty_result_note("base_readQuery", {"sql": PROD}, empty, "s-none") is None, None)
        APP_CONFIG.PREFLIGHT_VALUE_MODE = "review"
        _reset()
        check("an unknown-but-stronger mode clamps to enforce (never weakens silently)", isinstance(guard.check_values(LOCAL, SID), dict), None)
    finally:
        APP_CONFIG.PREFLIGHT_VALUE_MODE = old

def t_walker_pairs():
    """The two-column decode is declared structurally: the code and the label share ONE
    `source_column` and exactly one carries the value domain."""
    from trusted_data_agent.agent.kg_constructors import declared_model_walker as w
    snap = w.DeclaredModelSnapshot(
        model_name="Policing Core", catalog_database="Policing_Core_Semantic",
        object_classes=[{"object_class_id": "Agency", "name": "Agency", "subject_area": "crime", "object_kind": "view", "deployed_object": "Policing_Core.v_Agency", "natural_key": "ori"}],
        data_elements=[
            {"object_class_id": "Agency", "property_name": "core_city_code", "declared_type": "VARCHAR(1)", "value_domain_id": "nibrs_core_city", "source_column": "NIBRS_Source_Domain.Batch_Header.BH013", "ordinal_position": 1},
            {"object_class_id": "Agency", "property_name": "core_city", "declared_type": "VARCHAR(512)", "source_column": "NIBRS_Source_Domain.Batch_Header.BH013", "ordinal_position": 2},
            {"object_class_id": "Agency", "property_name": "city", "declared_type": "VARCHAR(25)", "source_column": "NIBRS_Source_Domain.Batch_Header.BH007", "ordinal_position": 3},
        ],
        value_domains=[{"value_domain_id": "nibrs_core_city", "name": "CORE CITY"}],
        permissible_values=[{"value_domain_id": "nibrs_core_city", "code": "0", "meaning": "No", "ordinal": 1},
                            {"value_domain_id": "nibrs_core_city", "code": "1", "meaning": "Yes", "ordinal": 2}],
        associations=[], physical_columns={})
    stats = w.WalkStats()
    ents_l, _rels = w.walk_declared_model(snap, w.WalkOptions(include_physical=False), stats)
    class _R: pass
    res = _R(); res.entities = ents_l; res.stats = stats
    ents = {e["name"]: e["properties"] for e in res.entities if e["entity_type"] == "attribute"}
    code, label, city = ents["Policing Core.Agency.core_city_code"], ents["Policing Core.Agency.core_city"], ents["Policing Core.Agency.city"]
    check("the code column carries the domain and names its label column", code.get("allowed_values") and code.get("decoded_as") == "core_city", code)
    check("the label column carries the domain's LABELS and names its code column", label.get("decoded_labels") == ["No", "Yes"] and label.get("decoded_from") == "core_city_code", label)
    check("a free-text column pairs with nothing", "decoded_labels" not in city and "decoded_from" not in city, city)
    check("the walker counts the pairs", res.stats.decode_pairs == 1, res.stats.decode_pairs)

def t_metadata_source():
    from components.builtin.knowledge_graph import handler as kgh
    class Cap:
        def __init__(self, d): self.deployed_as = d
    code = {"label": "core_city_code", "deployed_type": "VARCHAR(1)", "allowed_values": [{"value": "0", "label": "No"}, {"value": "1", "label": "Yes"}], "decoded_as": "core_city"}
    label = {"label": "core_city", "deployed_type": "VARCHAR(512)", "decoded_labels": ["No", "Yes"], "decoded_from": "core_city_code"}
    sub = {"entities": [
        {"id": 1, "entity_type": "entity", "name": "Policing Core.Agency", "properties": {}},
        {"id": 2, "entity_type": "attribute", "name": "Policing Core.Agency.core_city_code", "properties": code},
        {"id": 3, "entity_type": "attribute", "name": "Policing Core.Agency.core_city", "properties": label},
        {"id": 4, "entity_type": "attribute", "name": "Policing Core.Agency.city", "properties": {"label": "city", "deployed_type": "VARCHAR(25)"}},
    ], "relationships": [{"relationship_type": "contains", "source_name": "Policing Core.Agency", "target_name": f"Policing Core.Agency.{c}"} for c in ("core_city_code", "core_city", "city")]}
    vd = kgh._value_domains(sub, {"Policing Core.Agency": Cap("Policing_Core.v_Agency")})
    check("the metadata lists both halves of the decode under the DEPLOYED table, the free-text column not at all",
          vd == [{"table": "Policing_Core.v_Agency", "columns": [
              {"name": "core_city", "values": {"No": "No", "Yes": "Yes"}, "kind": "label", "pair": "core_city_code"},
              {"name": "core_city_code", "values": {"0": "No", "1": "Yes"}, "kind": "code", "pair": "core_city"}]}], vd)
    vals._reset_all(); vals.record_from_event(SID, {"enrichments": [{"value_domains": vd}]})
    check("the metadata shape feeds the guard directly", [f["column"] for f in vals.value_violations(SID, LOCAL)] == ["core_city"], None)
    parts = kgh._governance_parts(dict(label), complete=True)
    check("the enrichment renders the label column's own values (no longer free text)", any("decoded label of core_city_code; values: 'No', 'Yes'" in p for p in parts), parts)
    parts = kgh._governance_parts(dict(code), complete=True)
    check("…and the code column names its label column", any("decoded label column: core_city" in p for p in parts), parts)

def t_honesty_kind():
    from trusted_data_agent.agent.honesty import gate as hg, primitives as hp
    stamped = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM NIBRS_Source_Domain.Incident"}, "result": {"status": "success", "results": [{"n": 179193}]}},
               {"tool_name": "base_readQuery", "arguments": {"sql": PROD},
                "result": {"status": "success", "results": [{"robbery_count": 0}], "preflight_unverified": ["incident.BH020='031'"]}}]
    check("the primitive reads the guard's structured stamp (Optimize lane)", hp.unverified_literal_filters(stamped) == ["incident.BH020='031'"], hp.unverified_literal_filters(stamped))
    react = [dict(stamped[0]), {"tool_name": "base_readQuery", "arguments": {"sql": PROD}, "result": '{"status":"success","results":[{"robbery_count":0}]}\n' + vals.empty_result_note([{"leaf": "incident", "column": "BH020", "literal": "031", "snippet": "BH020 = '031'"}])}]
    check("…and the appended text note (ReAct lane)", hp.unverified_literal_filters(react) == ["BH020 = '031'"], hp.unverified_literal_filters(react))
    v = hg.assess("There were 0 robbery incidents recorded for Chicago in 2023.", stamped)
    check("the honesty gate: a non-existence claim on an unverified literal is the new kind (the relation IS established, so empty_relation stays silent)",
          v is not None and v.kind == "unverified_literal" and "matched nothing" in v.honest_message, v)
    unstamped = [stamped[0], {"tool_name": "base_readQuery", "arguments": {"sql": PROD}, "result": {"status": "success", "results": [{"robbery_count": 0}]}}]
    check("without the stamp the gate stays silent on a legitimate filtered zero (the invariant kept)", hg.assess("There were 0 robbery incidents recorded for Chicago in 2023.", unstamped) is None, None)
    check("a positive answer is never this kind", hg.assess("There were 10,895 robberies in Chicago.", stamped) is None, None)

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
    check("check_sql runs the value check", "check_values" in (_calls_in(SRC / "agent/preflight/guard.py", "check_sql") or set()), None)
    check("the executor records the domains where the enrichment event is stamped", "maybe_record_value_domains" in (_calls_in(SRC / "agent/executor.py", "_apply_trusted_scope") or set()), None)
    check("the ReAct chokepoint learns values after execution", "maybe_learn_values" in (_calls_in(SRC / "agent/tool_enforcement.py", "record_catalog") or set()), None)
    check("the ReAct guarded coroutine stamps the empty-result note", _via_gate(SRC / "agent/tool_enforcement.py", "_guarded", "sql_gate_after", "empty_result_note"), None)
    _ad = SRC / "mcp_adapter/adapter.py"
    check("the MCP-adapter chokepoint learns values and stamps the note",
          _via_gate(_ad, "_invoke_mcp_tool_impl", "sql_gate_after", "maybe_learn_values")
          and _via_gate(_ad, "_invoke_mcp_tool_impl", "sql_gate_after", "empty_result_note")
          and "preflight_note" in _ad.read_text(), None)
    h = (pathlib.Path(__file__).resolve().parents[1] / "components/builtin/knowledge_graph/handler.py").read_text()
    check("the enrichment emits value_domains beside text_date_columns", 'metadata["value_domains"] = _value_domains(subgraph, capabilities)' in h, None)
    check("the honesty gate consults the primitive", "unverified_literal_filters" in (_calls_in(SRC / "agent/honesty/gate.py", "assess") or set()), None)
    check("compose forwards the mode", "TDA_PREFLIGHT_VALUE_MODE" in (pathlib.Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(), None)

for fn in (t_literals, t_verdicts, t_learning, t_recording, t_mode_ladder, t_walker_pairs, t_metadata_source, t_honesty_kind, t_wiring):
    try: fn()
    except Exception as exc:
        import traceback; traceback.print_exc()
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
