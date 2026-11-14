"""PROP-008 increment B1 — the action-type gate A1–A6 (pure, CI).

  §A1  resolution agreement: agree · drift (names the new target) · unresolved · ambiguous ·
       structural when no resolver
  §A2  columns exist, types agree (Teradata codes + portable spellings), key + audit columns
       exist, value domains agree with the graph's CLOSED domain
  §A3  the grain: live dup-group probe (contradiction named, measured-not-enforced noted,
       probe failure is a fail), declared unique index in schema mode, unverified otherwise
  §A4  predicates: boolean, no subquery, target columns only; the graph's entity columns
       are the universe when no dictionary; unverified when neither
  §A5  one parameterised statement on the target, byte-stable; a literal, a second table or
       an unstable compiler fails; live EXPLAIN: a product join or an all-rows scan of the
       target fails, an index path passes
  §A6  the role holds exactly the needed rights (nested roles followed): a missing right, a
       wider one, a database-level grant, the unprovisioned ledger, no rights at all
  §R   the report: mode attested (live vs structural), deterministic hash, signable = no blockers

Run: PYTHONPATH=src:. python3 test/test_action_validation.py
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)

from trusted_data_agent.dbdeploy import action_model as AM
from trusted_data_agent.dbdeploy import action_validation as AV

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))

RAW = {
    "action": "set_customer_tier", "version": 1, "backend": "teradata",
    "target": {"entity": "Customers", "deployed": "fitness_db.Customers", "key": ["CustomerID"]},
    "operation": "update",
    "attributes": [{"name": "LoyaltyTier", "type": "string", "required": True,
                    "value_domain": ["bronze", "silver", "gold"]}],
    "conditions": {"pre": [{"id": "not_closed", "expr": "Status <> 'closed'"}]},
    "bound": {"max_rows": 1},
    "authority": {"required_role": "UDERIA_ACT_FITNESS", "posture": "review"},
}
M = AM.parse_manifest(RAW)
LED = "fitness_db.UDERIA_ACT_Customers"
SCHEMA = {"fitness_db.Customers": {"CustomerID": "I", "LoyaltyTier": "CV", "Status": "CV", "Name": "CV"},
          LED: {"performed_at": "TS", "principal": "CV"}}

INDEX_PLAN = """  1) First, we do a single-AMP UPDATE from fitness_db.Customers by way of
     the unique primary index "fitness_db.Customers.CustomerID = 5" with no
     residual conditions.  The estimated time for this step is 0.00 seconds.
  -> No rows are returned to the user as the result of statement 1."""
SCAN_PLAN = """  1) First, we lock fitness_db.Customers for write.
  2) Next, we do an all-AMPs UPDATE from fitness_db.Customers by way of an
     all-rows scan with a condition of ("fitness_db.Customers.Name = 'x'").
  3) Finally, we send out an END TRANSACTION step to all AMPs involved."""
PRODUCT_PLAN = """  1) We do an all-AMPs JOIN step from Spool 2 by way of an all-rows scan,
     which is joined to Spool 3.  Spool 2 and Spool 3 are joined using a
     product join, with a join condition of ("(1=1)")."""

EXACT_RIGHTS = [("UDERIA_ACT_FITNESS", "fitness_db", "Customers", "R"),
                ("UDERIA_ACT_FITNESS", "fitness_db", "Customers", "U"),
                ("UDERIA_ACT_FITNESS", "fitness_db", "UDERIA_ACT_Customers", "I")]

class Probe:
    """A faithful fake of the read-only executor: answers the three statement families
    the gate builds, records every call, refuses anything else."""
    def __init__(self, dup=0, plan=INDEX_PLAN, rights=None, members=None, fail=()):
        self.dup, self.plan = dup, plan
        self.rights = EXACT_RIGHTS if rights is None else rights
        self.members = members or {}
        self.fail = set(fail); self.calls = []
    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SELECT COUNT(*) AS dup_groups"):
            if "dup" in self.fail: raise RuntimeError("3523 no select access")
            return [(self.dup,)]
        if sql.startswith("EXPLAIN "):
            if "explain" in self.fail: raise RuntimeError("3706 syntax")
            return [(ln,) for ln in self.plan.splitlines()]
        if "DBC.RoleMembersV" in sql:
            return [(n,) for n in self.members.get(params[0], [])]
        if "DBC.AllRoleRightsV" in sql:
            if "rights" in self.fail: raise RuntimeError("3523")
            return [r for r in self.rights if r[0] == params[0]]
        raise AssertionError(f"the gate issued an unexpected statement: {sql}")

def run(m=M, **kw):
    base = dict(resolution={m.entity: m.deployed}, schema=SCHEMA)
    base.update(kw)
    return AV.validate_action(m, **base)

def chk(rep, cid):
    return next(c for c in rep.checks if c.id == cid)

def live(**kw):
    p = kw.pop("probe", None) or Probe()
    return run(probe_fn=p, sample_params=lambda m, names: ["x"] * len(names), **kw), p

def t_a1():
    print("[§A1] resolution agreement")
    c = chk(run(), "A1"); check("agree → pass (schema)", c.status == "pass" and c.mode == "schema", c.detail)
    c = chk(run(resolution={"Customers": "fitness_db.Customers_v2"}), "A1")
    check("drift → fail naming the new target + the ratchet", c.status == "fail" and "Customers_v2" in c.detail and "re-sign" in c.detail)
    check("unresolved → fail", chk(run(resolution={"Customers": None}), "A1").status == "fail")
    check("ambiguous → fail", chk(run(resolution={"Customers": ["a.b", "c.d"]}), "A1").status == "fail")
    c = chk(AV.validate_action(M), "A1"); check("no resolver → structural, stated", c.status == "pass" and c.mode == "structural" and "structural" in c.detail)
    check("resolution is case-insensitive", chk(run(resolution={"Customers": "FITNESS_DB.customers"}), "A1").status == "pass")

def t_a2():
    print("[§A2] columns, types, domains")
    check("all present + compatible → pass", chk(run(), "A2").status == "pass")
    s = copy.deepcopy(SCHEMA); del s["fitness_db.Customers"]["LoyaltyTier"]
    c = chk(run(schema=s), "A2"); check("a missing writable column fails by name", c.status == "fail" and "LoyaltyTier" in c.detail)
    s = copy.deepcopy(SCHEMA); s["fitness_db.Customers"]["LoyaltyTier"] = "I"
    c = chk(run(schema=s), "A2"); check("a type disagreement fails, naming both", c.status == "fail" and "string" in c.detail and "I" in c.detail)
    s = copy.deepcopy(SCHEMA); del s["fitness_db.Customers"]["CustomerID"]
    check("a missing key column fails", "key column" in chk(run(schema=s), "A2").detail)
    s = {LED: SCHEMA[LED]}
    check("the target absent from the schema fails", "does not exist" in chk(run(schema=s), "A2").detail)
    check("a portable spelling compares by family", AV._type_ok("string", "VARCHAR(20)") and AV._type_ok("integer", "INTEGER")
          and not AV._type_ok("date", "VARCHAR(10)"))
    c = chk(run(graph_domains={"LoyaltyTier": ["bronze", "silver"]}), "A2")
    check("a value outside the graph's closed domain fails, naming it", c.status == "fail" and "gold" in c.detail)
    check("a domain inside the graph's passes", chk(run(graph_domains={"LoyaltyTier": ["bronze", "silver", "gold", "platinum"]}), "A2").status == "pass")
    raw = copy.deepcopy(RAW); del raw["attributes"][0]["value_domain"]
    c = chk(run(AM.parse_manifest(raw), graph_domains={"LoyaltyTier": ["bronze"]}), "A2")
    check("the graph declares a domain the action drops → fail", c.status == "fail" and "must carry it" in c.detail)
    c = chk(AV.validate_action(M), "A2"); check("no schema → structural, stated", c.status == "pass" and c.mode == "structural")
    raw = copy.deepcopy(RAW); raw["ledger"] = {"mode": "target_audit_columns", "columns": ["updated_by"]}
    c = chk(run(AM.parse_manifest(raw)), "A2"); check("a missing audit column fails", c.status == "fail" and "updated_by" in c.detail)

def t_a3():
    print("[§A3] the grain")
    rep, p = live(); c = chk(rep, "A3")
    check("live, zero dup groups → pass (live)", c.status == "pass" and c.mode == "live")
    check("…measured unique but no index declares it → said", "not enforced" in c.detail)
    rep, _ = live(unique_keys={"fitness_db.Customers": [("CustomerID",)]})
    check("…and an index declaring it removes the note", "not enforced" not in chk(rep, "A3").detail)
    rep, _ = live(probe=Probe(dup=3)); c = chk(rep, "A3")
    check("the data contradicts the grain → fail naming the count", c.status == "fail" and "3 duplicate" in c.detail and "CONTRADICTS" in c.detail)
    rep, _ = live(probe=Probe(fail={"dup"}))
    check("a probe that cannot run is a FAIL, never a pass", chk(rep, "A3").status == "fail")
    check("schema mode: a declared unique index passes", chk(run(unique_keys={"fitness_db.Customers": [("customerid",)]}), "A3").status == "pass")
    check("schema mode: no covering index fails", chk(run(unique_keys={"fitness_db.Customers": [("Name",)]}), "A3").status == "fail")
    check("no connection, no index list → unverified, never pass", chk(run(), "A3").status == "unverified")
    probe_sql = next(s for s, _ in p.calls if "dup_groups" in s)
    check("the probe is grouped on the declared key only", 'GROUP BY "CustomerID"' in probe_sql and '"fitness_db"."Customers"' in probe_sql)

def t_a4():
    print("[§A4] predicates")
    check("a boolean over target columns → pass", chk(run(), "A4").status == "pass")
    for expr, why in (("Status", "not boolean"), ("Status IN (SELECT x FROM other)", "subquery"),
                      ("Region = 'EU'", "does not declare"), ("Status = 'a' -- x", "comments")):
        raw = copy.deepcopy(RAW); raw["conditions"]["pre"][0]["expr"] = expr
        c = chk(run(AM.parse_manifest(raw)), "A4")
        check(f"{expr!r} → fail ({why})", c.status == "fail" and why in c.detail, c.detail)
    c = chk(AV.validate_action(M, entity_columns=["Status", "CustomerID"]), "A4")
    check("no dictionary: the graph's entity columns are the universe", c.status == "pass" and c.mode == "schema")
    c = chk(AV.validate_action(M, entity_columns=["Name"]), "A4")
    check("…and a column the graph does not declare fails", c.status == "fail" and "Status" in c.detail)
    c = chk(AV.validate_action(M), "A4")
    check("neither graph nor dictionary → unverified, never pass", c.status == "unverified")
    raw = copy.deepcopy(RAW); raw["conditions"] = {}
    check("no authored conditions → pass, the defaults apply", chk(run(AM.parse_manifest(raw)), "A4").status == "pass")
    check("BETWEEN / IS NULL / NOT / AND compose as boolean",
          AV.predicate_problems("NOT (a IS NULL) AND b BETWEEN 1 AND 2 OR c LIKE 'x%'", {"a", "b", "c"}) == [])

def t_a5():
    print("[§A5] the statement")
    c = chk(run(), "A5"); check("structural: one parameterised statement on the target", c.status == "pass" and c.mode == "structural")
    sql, params = AV.representative_statement(M)
    check("update binds the writable then the key, no literal",
          sql == 'UPDATE "fitness_db"."Customers" SET "LoyaltyTier" = ? WHERE "CustomerID" = ?' and params == ("LoyaltyTier", "CustomerID"), sql)
    ins = AM.parse_manifest(dict(copy.deepcopy(RAW), operation="insert", conditions={},
                                 attributes=RAW["attributes"] + [{"name": "CustomerID", "type": "integer", "required": True}]))
    check("insert names the target only", AV.statement_shape(AV.representative_statement(ins)[0], ins.deployed) == [])
    ups = AM.parse_manifest(dict(copy.deepcopy(RAW), operation="upsert", conditions={},
                                 attributes=RAW["attributes"] + [{"name": "CustomerID", "type": "integer", "required": True}]))
    usql, up = AV.representative_statement(ups)
    check("upsert is one MERGE on the target, every value bound", usql.startswith('MERGE INTO "fitness_db"."Customers"')
          and AV.statement_shape(usql, ups.deployed) == [] and usql.count("?") == len(up))
    lit = lambda m: ('UPDATE "fitness_db"."Customers" SET "LoyaltyTier" = \'gold\' WHERE "CustomerID" = ?', ("CustomerID",))
    check("a literal fails", chk(run(compile_fn=lit), "A5").status == "fail")
    other = lambda m: ('UPDATE "fitness_db"."Orders" SET "LoyaltyTier" = ? WHERE "CustomerID" = ?', ("LoyaltyTier", "CustomerID"))
    c = chk(run(compile_fn=other), "A5"); check("another table fails, named", c.status == "fail" and "Orders" in c.detail)
    two = lambda m: ('UPDATE "fitness_db"."Customers" SET "LoyaltyTier" = ? WHERE "CustomerID" = ?; DELETE FROM "fitness_db"."Customers"', ("LoyaltyTier", "CustomerID"))
    check("two statements fail", "more than one statement" in chk(run(compile_fn=two), "A5").detail)
    n = {"i": 0}
    def unstable(m):
        n["i"] += 1
        return (f'UPDATE "fitness_db"."Customers" SET "LoyaltyTier" = ? WHERE "CustomerID" = ?' + (" " * n["i"]), ("LoyaltyTier", "CustomerID"))
    check("an unstable compiler fails", "byte-stable" in chk(run(compile_fn=unstable), "A5").detail)
    marks = lambda m: ('UPDATE "fitness_db"."Customers" SET "LoyaltyTier" = ? WHERE "CustomerID" = ?', ("LoyaltyTier",))
    check("a marker/parameter mismatch fails", "bind marker" in chk(run(compile_fn=marks), "A5").detail)
    rep, p = live(); c = chk(rep, "A5")
    check("live: an index path passes (live)", c.status == "pass" and c.mode == "live", c.detail)
    ex = next((s, pa) for s, pa in p.calls if s.startswith("EXPLAIN "))
    check("the EXPLAIN carries bind values, never inlined literals", ex[1] == ["x", "x"] and "'" not in ex[0])
    rep, _ = live(probe=Probe(plan=SCAN_PLAN)); c = chk(rep, "A5")
    check("live: an all-rows scan of the target fails", c.status == "fail" and "all-rows scan" in c.detail, c.detail)
    rep, _ = live(probe=Probe(plan=PRODUCT_PLAN))
    check("live: a product join fails", "product join" in chk(rep, "A5").detail)
    rep, _ = live(probe=Probe(fail={"explain"}))
    check("live: an EXPLAIN that cannot run fails", chk(rep, "A5").status == "fail")
    check("a scan of ANOTHER table is not the target's", AV.write_plan_problems(SCAN_PLAN.replace("Customers", "Orders"), "fitness_db.Customers") == [])

def t_a6():
    print("[§A6] the role's rights")
    rep, _ = live(); c = chk(rep, "A6")
    check("exactly R+U on the target, I on the ledger → pass", c.status == "pass" and "exactly" in c.detail, c.detail)
    rep, _ = live(probe=Probe(rights=EXACT_RIGHTS[:2])); c = chk(rep, "A6")
    check("missing INSERT on the ledger → fail naming it", c.status == "fail" and "lacks INSERT on fitness_db.UDERIA_ACT_Customers" in c.detail, c.detail)
    check("…and the exact statement that fixes it, nothing wider", "grant exactly: GRANT INSERT ON fitness_db.UDERIA_ACT_Customers TO UDERIA_ACT_FITNESS;" in c.detail, c.detail)
    rep, _ = live(probe=Probe(rights=[])); c = chk(rep, "A6")
    check("a role holding nothing yet → fail with EVERY grant it needs, as statements (Act II: the person had to ask)",
          c.status == "fail" and "holds no rights yet" in c.detail and "GRANT SELECT, UPDATE ON fitness_db.Customers TO UDERIA_ACT_FITNESS;" in c.detail
          and "GRANT INSERT ON fitness_db.UDERIA_ACT_Customers TO UDERIA_ACT_FITNESS;" in c.detail, c.detail)
    rep, _ = live(probe=Probe(rights=EXACT_RIGHTS + [("UDERIA_ACT_FITNESS", "fitness_db", "Customers", "D")]))
    check("DELETE on the target is wider → fail", "more than the operation needs" in chk(rep, "A6").detail)
    rep, _ = live(probe=Probe(rights=EXACT_RIGHTS + [("UDERIA_ACT_FITNESS", "fitness_db", "Orders", "U")]))
    check("a write on another table is wider → fail", "Orders" in chk(rep, "A6").detail)
    rep, _ = live(probe=Probe(rights=EXACT_RIGHTS + [("UDERIA_ACT_FITNESS", "fitness_db", "All", "U")]))
    check("a database-level write is wider → fail", "whole database" in chk(rep, "A6").detail)
    rep, _ = live(probe=Probe(rights=EXACT_RIGHTS + [("UDERIA_ACT_FITNESS", "hr_db", "Salaries", "R")]))
    check("a READ elsewhere is not a write — tolerated", chk(rep, "A6").status == "pass")
    nested = [("UDERIA_ACT_FITNESS", "fitness_db", "Customers", "R"), ("CHILD", "fitness_db", "Customers", "U"),
              ("CHILD", "fitness_db", "UDERIA_ACT_Customers", "I")]
    rep, _ = live(probe=Probe(rights=nested, members={"UDERIA_ACT_FITNESS": ["CHILD"]}))
    check("rights held through a NESTED role count", chk(rep, "A6").status == "pass", chk(rep, "A6").detail)
    rep, _ = live(probe=Probe(rights=nested + [("GRANDCHILD", "fitness_db", "Customers", "D")],
                              members={"UDERIA_ACT_FITNESS": ["CHILD"], "CHILD": ["GRANDCHILD"]}))
    check("…and a wider right two levels down is caught", "more than" in chk(rep, "A6").detail)
    rep, _ = live(probe=Probe(rights=EXACT_RIGHTS, members={"UDERIA_ACT_FITNESS": ["UDERIA_ACT_FITNESS"]}))
    check("a role cycle terminates", chk(rep, "A6").status == "pass")
    rep, _ = live(schema={"fitness_db.Customers": SCHEMA["fitness_db.Customers"]})
    check("the ledger not provisioned → fail, saying so", "not provisioned" in chk(rep, "A6").detail)
    rep, _ = live(probe=Probe(rights=[]))
    check("no rights at all → fail", "holds no rights" in chk(rep, "A6").detail)
    rep, _ = live(probe=Probe(fail={"rights"}))
    check("rights unreadable → fail", chk(rep, "A6").status == "fail")
    raw = copy.deepcopy(RAW); raw["ledger"] = {"mode": "target_audit_columns", "columns": ["Name"]}
    rep, _ = live(m=AM.parse_manifest(raw), probe=Probe(rights=EXACT_RIGHTS[:2]))
    check("the audit-columns opt-out needs no ledger right", chk(rep, "A6").status == "pass", chk(rep, "A6").detail)
    check("no connection → unverified, never pass", chk(run(), "A6").status == "unverified")

def t_report():
    print("[§R] the report")
    s = run()
    check("structural mode attested", s.mode == "structural")
    l, _ = live()
    check("live mode attested", l.mode == "live" and l.signable, l.blockers)
    check("live and structural hash differently (the mode is content)", AV.report_hash(s) != AV.report_hash(l))
    check("deterministic: two runs hash identically", AV.report_hash(run()) == AV.report_hash(run()))
    check("the report hash is over the dict and the object alike", AV.report_hash(s) == AV.report_hash(s.to_dict()))
    check("structural with A3/A6 unverified is still signable (verified_declared downstream)", s.signable
          and {c.id for c in s.checks if c.status == "unverified"} == {"A3", "A6"})
    f = run(resolution={"Customers": None})
    check("a fail is a blocker and the report is not signable", not f.signable and f.blockers and f.blockers[0].startswith("A1"))
    check("the report carries the effective conditions (defaults first)",
          [c["id"] for c in s.conditions["pre"]] == ["row_exists", "not_closed"])
    check("the report carries the manifest hash it validated", s.manifest_hash == AM.manifest_hash(M))
    check("every check id A1..A6 appears exactly once", [c.id for c in l.checks] == ["A1", "A2", "A3", "A4", "A5", "A6"])

def t_binds():
    """The live probe executor's bind shape. Found by the first live run (Oct 2026):
    teradataml's execute_sql takes ONE row of binds as a tuple and refuses a flat list
    (TDML_2008). A PAT-only backend still runs through teradataml — the fake driver here
    refuses exactly what the real one refuses."""
    print("[§B] bind parameters reach the driver as ONE row")
    import types
    from trusted_data_agent.dbdeploy.teradata_backend import TeradataDeploymentBackend
    seen = []
    def execute_sql(sql, parameters=None):
        if parameters is not None and not (isinstance(parameters, tuple)
                                           or (isinstance(parameters, list) and all(isinstance(p, tuple) for p in parameters))):
            raise TypeError("TDML_2008: should be tuple or list of tuples")
        seen.append(parameters)
        return None
    saved = sys.modules.get("teradataml")
    sys.modules["teradataml"] = types.SimpleNamespace(execute_sql=execute_sql)
    try:
        b = TeradataDeploymentBackend({"pat_token": "t"}); b._initialized = True; b._tdml = True
        b.run_sync_query("SELECT 1 WHERE ? = ?", ["a", "b"])
        check("PAT (teradataml): a list of binds reaches the driver as one tuple", seen == [("a", "b")], seen)
        b.run_sync_query("SELECT 1")
        check("…no binds → the driver gets none", seen == [("a", "b"), None], seen)
    finally:
        if saved is None: sys.modules.pop("teradataml", None)
        else: sys.modules["teradataml"] = saved


def t_identity():
    """Act II rehearsal (Oct 2026): every backend ran its statements through teradataml's
    process-GLOBAL context, so with two logins in one process a statement ran as whichever
    login had connected LAST — the action ledger's CREATE "through the model operator" ran as
    the writer, and validation's rights probes read another login's rights. Each backend now
    holds its own session: a statement runs as the backend's login, whatever was opened since."""
    print("[§C] a statement runs as ITS backend's login — never the last one opened")
    import asyncio, types
    from trusted_data_agent.dbdeploy.teradata_backend import TeradataDeploymentBackend
    ran = []
    class Cur:
        def __init__(self, user): self.user, self.description = user, None
        def execute(self, sql, params=None):
            if params is not None and not isinstance(params, (list, tuple)):
                raise TypeError("binds are one row")
            ran.append((self.user, sql, params)); self.description = [("x",)]
        def fetchall(self): return [[self.user]]
        def close(self): pass
    class Conn:
        def __init__(self, user): self.user = user
        def cursor(self): return Cur(self.user)
        def close(self): pass
    saved_sql, saved_ml = sys.modules.get("teradatasql"), sys.modules.get("teradataml")
    sys.modules["teradatasql"] = types.SimpleNamespace(connect=lambda **kw: Conn(kw["user"]))
    def _no_global(**_):
        raise AssertionError("create_context must not be used for a password login")
    sys.modules["teradataml"] = types.SimpleNamespace(create_context=_no_global)
    try:
        op = TeradataDeploymentBackend({"host": "h", "username": "operator", "password": "p", "port": 1025})
        wr = TeradataDeploymentBackend({"host": "h", "username": "writer", "password": "p"})
        async def go():
            await op.initialize(); await wr.initialize()      # the writer connects LAST
            await op.execute_statement("CREATE TABLE ledger (x INT)")
            return await asyncio.to_thread(wr.run_sync_query, "SELECT USER WHERE ? = ?", ["a", "b"])
        out = asyncio.run(go())
        check("the operator's DDL runs as the operator although the writer connected after it",
              ("operator", "CREATE TABLE ledger (x INT)", None) in ran, ran)
        check("…and the writer's probe runs as the writer, its binds one row", ran[-1][0] == "writer" and list(ran[-1][2]) == ["a", "b"] and out == [("writer",)], ran)
        check("no password login touches teradataml's process-global context", True)
    finally:
        for name, mod in (("teradatasql", saved_sql), ("teradataml", saved_ml)):
            if mod is None: sys.modules.pop(name, None)
            else: sys.modules[name] = mod

def t_role_session():
    """Act II rehearsal (Oct 2026): with each login on its own session, validation's probes ran
    on the WRITER's plain session — under no role it may neither read nor plan a write on the
    table, so A3/A5 failed (the earlier passes were another login's rights answering). The
    probes now run on a session acting under the action's role, as perform writes."""
    print("[§D] the probes run under the action's role — as perform writes")
    import types
    from trusted_data_agent.dbdeploy.teradata_backend import TeradataDeploymentBackend
    from trusted_data_agent.core import action_types as AT
    ran = []
    def mk(held):
        class Cur:
            description = None
            def execute(self, sql, params=None):
                ran.append(sql); self.description = [("x",)]
                self._rows = [[1 if held else 0]] if "RoleMembersV" in sql else [["ok"]]
            def fetchall(self): return self._rows
            def close(self): pass
        class Conn:
            def cursor(self): return Cur()
            def close(self): ran.append("CLOSE")
        return Conn()
    saved = sys.modules.get("teradatasql")
    try:
        sys.modules["teradatasql"] = types.SimpleNamespace(connect=lambda **kw: mk(True))
        b = TeradataDeploymentBackend({"host": "h", "username": "writer", "password": "p"})
        sess = b.role_session("R_RECORD")
        probe = AT._probe_fn(b, sess)
        out = probe("SELECT COUNT(*) FROM t WHERE k = ?", ["a"])
        check("the session checks the login holds the role, then acts under it (SET ROLE)",
              any("RoleMembersV" in x for x in ran) and 'SET ROLE "R_RECORD"' in ran, ran)
        check("…and the gate's probes run on THAT session", ran[-1].startswith("SELECT COUNT(*) FROM t") and out == [("ok",)], ran)
        sess.close(); check("…which is closed after the gate", ran[-1] == "CLOSE")
        sys.modules["teradatasql"] = types.SimpleNamespace(connect=lambda **kw: mk(False))
        try:
            b.role_session("R_RECORD"); err = ""
        except Exception as e:  # noqa: BLE001
            err = str(e)
        check("a login that does not hold the role is named, with the exact GRANT", "GRANT R_RECORD TO writer;" in err, err)
        refused = AT._probe_fn(b, None, err)
        try:
            refused("SELECT 1"); r2 = ""
        except Exception as e:  # noqa: BLE001
            r2 = str(e)
        check("…and every probe then says that, never a driver stack", r2 == err)
        try:
            AT._probe_fn(b, None)("DELETE FROM t"); r3 = ""
        except Exception as e:  # noqa: BLE001
            r3 = str(e)
        check("the executor still runs SELECT/EXPLAIN only", "SELECT/EXPLAIN only" in r3)
    finally:
        if saved is None: sys.modules.pop("teradatasql", None)
        else: sys.modules["teradatasql"] = saved

for fn in (t_a1, t_a2, t_a3, t_a4, t_a5, t_a6, t_report, t_binds, t_identity, t_role_session):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc(); check(f"{fn.__name__} raised", False, repr(exc))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
