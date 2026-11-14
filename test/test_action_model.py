"""PROP-008 increment B1 — the action-type manifest model (pure, CI).

  §1  the closed REJECT set: every code in ERROR_CODES is driven to fire by name, and no
      code fires that is not in the set
  §2  canonicalisation: key order, an omitted default, whitespace and the input dict never
      move the hash; the input is never mutated
  §3  the ledger: on by default (an identifier, an INSERT right, a DDL), and the
      `target_audit_columns` opt-out (no ledger, no ledger right, audit columns never writable)
  §4  the derived default conditions (§2a): a pure function of the operation, first in the
      effective list, never redeclarable
  §5  the grain against the allowlist: an update never sets its key; insert/upsert supply it
  §6  near-miss hints name the closest declared value

Run: PYTHONPATH=src python3 test/test_action_model.py
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.dbdeploy import action_model as AM

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))

BASE = {
    "action": "set_customer_tier", "version": 1, "backend": "teradata",
    "description": "Set a customer's loyalty tier",
    "target": {"entity": "fitness_db.Customers", "deployed": "fitness_db.Customers", "key": ["CustomerID"]},
    "operation": "update",
    "attributes": [{"name": "LoyaltyTier", "type": "string", "required": True,
                    "value_domain": ["bronze", "silver", "gold"]}],
    "conditions": {"pre": [{"id": "not_closed", "expr": "Status <> 'closed'"}]},
    "bound": {"max_rows": 1},
    "authority": {"required_role": "UDERIA_ACT_FITNESS", "posture": "review"},
}

def code_of(raw):
    try:
        AM.parse_manifest(raw); return None
    except AM.ActionManifestError as e:
        return e.code

def mutate(**kw):
    r = copy.deepcopy(BASE)
    for path, val in kw.items():
        node = r; parts = path.split("__")
        for p in parts[:-1]:
            node = node[int(p)] if isinstance(node, list) else node[p]
        last = parts[-1]
        if val is ...:
            node.pop(last, None)
        elif isinstance(node, list):
            node[int(last)] = val
        else:
            node[last] = val
    return r

INSERT = dict(copy.deepcopy(BASE), operation="insert",
              attributes=BASE["attributes"] + [{"name": "CustomerID", "type": "integer", "required": True}],
              conditions={})

CASES = {
    "bad_manifest_shape": ["not a dict"],
    "bad_action_name": [mutate(action="Set Tier"), mutate(action="")],
    "bad_version": [mutate(version=0), mutate(version=True), mutate(version="1")],
    "missing_backend": [mutate(backend=" ")],
    "bad_description": [mutate(description=7)],
    "missing_target": [mutate(target=...)],
    "missing_target_entity": [mutate(target__entity="")],
    "missing_deployed": [mutate(target__deployed="")],
    "bad_deployed": [mutate(target__deployed="Customers"), mutate(target__deployed="fitness_db.Cust;omers")],
    "missing_key": [mutate(target__key=[])],
    "bad_key": [mutate(target__key=["Customer ID"]), mutate(target__key=["a", "a"])],
    "bad_operation": [mutate(operation="delete")],
    "no_attributes": [mutate(attributes=[])],
    "bad_attribute": [mutate(attributes=[{"name": "x y", "type": "string"}]),
                      mutate(attributes=[{"name": "x", "type": "string", "required": "yes"}]),
                      mutate(attributes=[{"name": "x", "type": "string", "default": [1]}])],
    "duplicate_attribute": [mutate(attributes=[{"name": "x", "type": "string"}, {"name": "x", "type": "integer"}])],
    "bad_attribute_type": [mutate(attributes=[{"name": "x", "type": "varchar"}])],
    "bad_value_domain": [mutate(attributes=[{"name": "x", "type": "string", "value_domain": []}]),
                         mutate(attributes=[{"name": "x", "type": "string", "value_domain": ["a", "a"]}])],
    "default_outside_domain": [mutate(attributes=[{"name": "x", "type": "string", "value_domain": ["a"], "default": "b"}])],
    "key_is_writable": [mutate(attributes=[{"name": "CustomerID", "type": "integer"}])],
    "key_not_supplied": [dict(copy.deepcopy(BASE), operation="insert")],
    "bad_conditions": [mutate(conditions={"during": []}), mutate(conditions={"pre": "x > 1"})],
    "bad_condition": [mutate(conditions={"pre": [{"id": "a b", "expr": "x > 1"}]}),
                      mutate(conditions={"pre": [{"id": "c", "expr": "x > 1; DROP TABLE t"}]}),
                      mutate(conditions={"pre": [{"id": "c", "expr": " "}]})],
    "duplicate_condition": [mutate(conditions={"pre": [{"id": "c", "expr": "x > 1"}], "post": [{"id": "c", "expr": "x > 2"}]})],
    "reserved_condition": [mutate(conditions={"post": [{"id": "one_row_affected", "expr": "1 = 1"}]})],
    "missing_bound": [mutate(bound=...)],
    "bad_bound": [mutate(bound={"max_rows": 0}), mutate(bound={"max_rows": AM.MAX_ROWS_CEILING + 1}),
                  mutate(bound={"max_rows": True})],
    "missing_authority": [mutate(authority=...)],
    "bad_role": [mutate(authority={"required_role": "role; x", "posture": "review"})],
    "bad_posture": [mutate(authority={"required_role": "R", "posture": "auto"})],
    "bad_ledger": [mutate(ledger={"mode": "file"}), mutate(ledger={"mode": "platform", "columns": ["a"]})],
    "bad_ledger_columns": [mutate(ledger={"mode": "target_audit_columns"}),
                           mutate(ledger={"mode": "target_audit_columns", "columns": ["a b"]})],
    "ledger_column_writable": [mutate(ledger={"mode": "target_audit_columns", "columns": ["LoyaltyTier"]})],
}

def t_closed_set():
    print("[§1] the closed REJECT set")
    check("the base manifest parses", code_of(BASE) is None, str(code_of(BASE)))
    check("the insert fixture parses", code_of(INSERT) is None, str(code_of(INSERT)))
    fired = set()
    for code, raws in CASES.items():
        for raw in raws:
            got = code_of(raw)
            fired.add(got)
            check(f"{code} fires", got == code, f"got {got}")
    check("every declared code is driven by a case", set(CASES) == AM.ERROR_CODES,
          f"undriven={sorted(AM.ERROR_CODES - set(CASES))} unknown={sorted(set(CASES) - AM.ERROR_CODES)}")
    check("no code fires outside the declared set", fired <= AM.ERROR_CODES, str(fired - AM.ERROR_CODES))

def t_canonical():
    print("[§2] canonicalisation")
    h = AM.manifest_hash(AM.parse_manifest(BASE))
    reordered = dict(reversed(list(copy.deepcopy(BASE).items())))
    check("key order never moves the hash", AM.manifest_hash(AM.parse_manifest(reordered)) == h)
    explicit = copy.deepcopy(BASE); explicit["ledger"] = {"mode": "platform"}
    explicit["attributes"][0]["required"] = True
    check("an omitted default written explicitly hashes the same", AM.manifest_hash(AM.parse_manifest(explicit)) == h)
    padded = copy.deepcopy(BASE); padded["backend"] = "  teradata  "; padded["conditions"]["pre"][0]["expr"] = "  Status <> 'closed'  "
    check("surrounding whitespace never moves the hash", AM.manifest_hash(AM.parse_manifest(padded)) == h)
    before = copy.deepcopy(BASE); AM.parse_manifest(BASE)
    check("the input is never mutated", before == BASE)
    changed = copy.deepcopy(BASE); changed["bound"]["max_rows"] = 2
    check("a content change moves the hash", AM.manifest_hash(AM.parse_manifest(changed)) != h)
    check("the hash is sha256 hex", len(h) == 64 and all(c in "0123456789abcdef" for c in h))
    check("canonical_json is the Trust Membrane's form (sorted, compact)",
          AM.canonical_json(AM.parse_manifest(BASE)).startswith('{"action":') and ", " not in AM.canonical_json(AM.parse_manifest(BASE)))

def t_ledger():
    print("[§3] the ledger")
    m = AM.parse_manifest(BASE)
    check("on by default: the platform ledger is named beside the target",
          m.ledger_mode == "platform" and AM.ledger_identifier(m) == "fitness_db.UDERIA_ACT_Customers")
    rights = AM.required_rights(m)
    check("the role needs U+R on the target and INSERT only on the ledger",
          rights == {"fitness_db.Customers": ("R", "U"), "fitness_db.UDERIA_ACT_Customers": ("I",)}, str(rights))
    ddl = AM.ledger_ddl(m)
    check("the ledger DDL is append-only audit (who, when, invocation, before-image), NoPI",
          ddl and '"fitness_db"."UDERIA_ACT_Customers"' in ddl and '"before_json"' in ddl and '"principal"' in ddl
          and '"invocation_hash"' in ddl and "NO PRIMARY INDEX" in ddl)
    opt = AM.parse_manifest(mutate(ledger={"mode": "target_audit_columns", "columns": ["updated_by", "updated_at"]}))
    check("the opt-out names no ledger and needs no ledger right",
          AM.ledger_identifier(opt) is None and AM.ledger_ddl(opt) is None
          and AM.required_rights(opt) == {"fitness_db.Customers": ("R", "U")})
    check("…and the audit columns travel in the canonical form",
          opt.to_dict()["ledger"] == {"mode": "target_audit_columns", "columns": ["updated_by", "updated_at"]})
    check("insert needs I+R; upsert I+U+R", AM.required_rights(AM.parse_manifest(INSERT))["fitness_db.Customers"] == ("I", "R")
          and AM.required_rights(AM.parse_manifest(dict(INSERT, operation="upsert")))["fitness_db.Customers"] == ("I", "R", "U"))
    long = AM.parse_manifest(mutate(target__deployed="fitness_db." + "T" * 125))
    check("the ledger name respects the 128-character object limit", len(AM.ledger_identifier(long).split(".", 1)[1]) == 128)

def t_conditions():
    print("[§4] the derived default conditions")
    upd = AM.effective_conditions(AM.parse_manifest(BASE))
    check("update: row_exists first, then the author's", [c["id"] for c in upd["pre"]] == ["row_exists", "not_closed"])
    check("update: exactly one row affected after", [c["id"] for c in upd["post"]] == ["one_row_affected"])
    ins = AM.effective_conditions(AM.parse_manifest(INSERT))
    check("insert: row_absent before, one_row_present after", [c["id"] for c in ins["pre"]] == ["row_absent"]
          and [c["id"] for c in ins["post"]] == ["one_row_present"])
    ups = AM.effective_conditions(AM.parse_manifest(dict(INSERT, operation="upsert")))
    check("upsert: no pre default, one row affected after", ups["pre"] == [] and [c["id"] for c in ups["post"]] == ["one_row_affected"])
    check("defaults are marked as such and carry words, never an expression",
          all(c["kind"] == "default" and c["words"] and "expr" not in c for c in upd["pre"][:1] + upd["post"]))
    check("the defaults are NOT stored in the canonical manifest (a pure function of the operation)",
          "row_exists" not in AM.canonical_json(AM.parse_manifest(BASE)))
    check("every default id is reserved", {"row_exists", "row_absent", "one_row_affected", "one_row_present"} == AM.RESERVED_CONDITION_IDS)

def t_grain():
    print("[§5] the grain against the allowlist")
    check("an update never sets its own key", code_of(mutate(attributes=BASE["attributes"] + [{"name": "CustomerID", "type": "integer"}])) == "key_is_writable")
    check("an insert must supply its key", code_of(dict(copy.deepcopy(BASE), operation="insert")) == "key_not_supplied")
    with_default = dict(copy.deepcopy(INSERT))
    with_default["attributes"] = [BASE["attributes"][0], {"name": "CustomerID", "type": "integer", "default": 0}]
    check("…a key with a default counts as supplied", code_of(with_default) is None)

def t_hints():
    print("[§6] near-miss hints")
    try: AM.parse_manifest(mutate(operation="updte"))
    except AM.ActionManifestError as e: check("an operation typo names the closest", "closest declared: update" in e.message, e.message)
    try: AM.parse_manifest(mutate(authority={"required_role": "R", "posture": "enforced"}))
    except AM.ActionManifestError as e: check("a posture typo names the closest", "enforce" in e.message, e.message)
    try: AM.parse_manifest(mutate(attributes=[{"name": "x", "type": "strng"}]))
    except AM.ActionManifestError as e: check("a type typo names the closest", "closest declared: string" in e.message, e.message)

for fn in (t_closed_set, t_canonical, t_ledger, t_conditions, t_grain, t_hints):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc(); check(f"{fn.__name__} raised", False, repr(exc))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
