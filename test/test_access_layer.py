"""
CI-pure tests for the ANDP Access Layer emitter.

Every property here is a SECURITY property, and each fails silently: a missing GRANT
looks like a working system until someone needs it, and a surplus one looks like a
working system forever.

The two that matter most:

  * **ROLE_AGENT must never receive INSERT or UPDATE on Domain or Semantic.** It is
    deliberately not uniformly read-only — it writes conversations and learned
    strategies to Memory, usage events and quality feedback to Observability — and
    that asymmetry is exactly what a careless "grant the agent write access" would
    erase.
  * **ROLE_READ_NOPII must hold no grant on a base table.** The guarantee is not that
    PII is discouraged; it is that there is no path to it. One `GRANT SELECT ON
    <db>` for that role would silently undo the whole thing while every view still
    looked correct.

No database — the emitter is pure, which is why this can be proven offline.

Run: python3 test/test_access_layer.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "agent_packs", "policing-data-products", "scripts"))

from access_layer import (  # noqa: E402
    ADMIN_PRIVILEGES,
    AGENT_WRITABLE,
    PHASE_1_5,
    PHASE_2_5,
    create_roles,
    design_decision,
    emit_plan,
    grant_nopii_views,
    grant_role_to_user,
    role_name,
    grants_for_phase,
    pii_free_view,
    service_account,
    service_account_name,
    endpoint_name,
    mcp_entry_name,
    ESTATE_SUFFIX,
    estate_role,
    estate_service_account,
    emit_estate_plan,
    estate_design_decision,
    project_decision,
    DecisionShapeError,
    LATIN_TRANSLITERATIONS,
)

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


P = "NIBRS_Source"
TABLES = {
    "Domain": {"Victim": ["ORI", "INCNUM", "V4019", "V4018"],
               "Incident": ["ORI", "INCNUM", "V1006"]},
    "Memory": {"Design_Decision": ["decision_id", "rationale"]},
}
PII = {"Victim": ["V4019", "V4018"]}


def plan():
    return emit_plan(P, TABLES, PII)


# ── the agent's write boundary ──────────────────────────────────────────────
def t_agent_cannot_write_the_data():
    """SELECT everywhere; INSERT only where write-back belongs."""
    all_grants = grants_for_phase(P, PHASE_1_5) + grants_for_phase(P, PHASE_2_5)
    agent = [g for g in all_grants if "ROLE_AGENT" in g]

    for module in ("Domain", "Semantic"):
        writes = [g for g in agent
                  if f"{P}_{module}" in g and re.search(r"\b(INSERT|UPDATE|DELETE|ALL)\b", g)]
        check(f"agent gets NO write on {module}", writes == [], str(writes))
        reads = [g for g in agent if f"{P}_{module}" in g and "SELECT" in g]
        check(f"agent still READS {module}", len(reads) == 1, str(reads))

    for module in AGENT_WRITABLE:
        ins = [g for g in agent if f"{P}_{module}" in g and "INSERT" in g]
        check(f"agent CAN write back to {module}", len(ins) == 1, str(ins))

    check("agent never receives a blanket ALL",
          not any("ALL" in g for g in agent), str([g for g in agent if "ALL" in g]))
    check("agent is never given grant authority",
          not any("WITH GRANT OPTION" in g for g in agent))


def t_read_and_admin():
    all_grants = grants_for_phase(P, PHASE_1_5) + grants_for_phase(P, PHASE_2_5)
    read = [g for g in all_grants if "ROLE_READ TO" in g or "TO NIBRS_Source_ROLE_READ;" in g]
    check("read role gets SELECT on all four modules", len(read) == 4, str(len(read)))
    check("read role never writes",
          not any(re.search(r"\b(INSERT|UPDATE|DELETE|ALL)\b", g) for g in read))
    admin = [g for g in all_grants if "ROLE_ADMIN" in g]
    check("admin is granted on all four modules", len(admin) == 4, str(len(admin)))
    # Teradata refuses both for a ROLE: 5615 (ALL implies CREATE USER) and 5613
    # (GRANT OPTION). The pure emitter cannot know that, so it is pinned here —
    # this gate previously asserted "admin can delegate" via WITH GRANT OPTION,
    # which the database rejects outright. An assertion can encode an impossible
    # expectation and still pass.
    check("admin is never granted ALL (Teradata 5615)",
          not any(re.search(r"GRANT\s+ALL\b", g) for g in admin), str(admin[:1]))
    check("admin never carries GRANT OPTION (Teradata 5613)",
          not any("GRANT OPTION" in g for g in admin), str(admin[:1]))
    check("admin gets an explicit privilege list",
          all(all(p in g for p in ("SELECT", "INSERT", "UPDATE", "DELETE")) for g in admin))
    check("the list never includes a user-management right",
          not any(re.search(r"CREATE USER|CREATE DATABASE|GRANT\b.*\bROLE\b", p)
                  for p in ADMIN_PRIVILEGES), str(ADMIN_PRIVILEGES))

    # delegation lives on the ROLE grant, which is where Teradata allows it
    d = grant_role_to_user(P, "ROLE_ADMIN", "steward", admin_option=True)
    check("delegation is WITH ADMIN OPTION on the role", "WITH ADMIN OPTION" in d, d)
    check("a plain role grant delegates nothing",
          "ADMIN OPTION" not in grant_role_to_user(P, "ROLE_READ", "analyst"))


def t_phases_are_split_as_the_framework_says():
    p15 = grants_for_phase(P, PHASE_1_5)
    p25 = grants_for_phase(P, PHASE_2_5)
    check("phase 1.5 is Semantic + Memory",
          all(("Semantic" in g or "Memory" in g) for g in p15))
    check("phase 2.5 is Domain + Observability",
          all(("Domain" in g or "Observability" in g) for g in p25))
    check("no module is granted in both phases",
          not (set(PHASE_1_5) & set(PHASE_2_5)))
    check("roles are created before anything is granted on them",
          all(g.startswith("CREATE ROLE") for g in create_roles(P)))


# ── the PII guarantee ───────────────────────────────────────────────────────
def t_pii_never_appears_in_a_view():
    sql = pii_free_view(P, "Domain", "Victim", TABLES["Domain"]["Victim"], PII["Victim"])
    for col in PII["Victim"]:
        check(f"{col} is absent from the view", f'"{col}"' not in sql, sql)
    for col in ("ORI", "INCNUM"):
        check(f"{col} is kept", f'"{col}"' in sql)
    check("the view reads the base table", 'FROM NIBRS_Source_Domain."Victim"' in sql)
    check("REPLACE VIEW, so re-running is safe", sql.startswith("REPLACE VIEW"))


def t_pii_match_is_case_insensitive():
    """The catalog and the dictionary do not agree on case; a miss here leaks a column."""
    sql = pii_free_view(P, "Domain", "T", ["Keep", "SECRET", "other"], ["secret"])
    check("a case-mismatched PII column is still dropped", '"SECRET"' not in sql, sql)
    check("the rest survives", '"Keep"' in sql and '"other"' in sql)


def t_all_pii_table_yields_no_view():
    """An empty view would look like access to something. No view is the honest answer."""
    check("no view when every column is PII",
          pii_free_view(P, "Domain", "T", ["A", "B"], ["a", "b"]) is None)


def t_restricted_role_has_no_path_to_a_base_table():
    pl = plan()
    grants = pl["view_grants"]
    check("restricted role is granted something", len(grants) >= 2, str(len(grants)))
    check("every restricted grant names a _NoPII view",
          all("_NoPII" in g for g in grants), str(grants))
    check("no restricted grant is database-wide",
          not any(re.search(r"ON \w+ TO .*ROLE_READ_NOPII", g) for g in grants), str(grants))

    # Only GRANT statements can widen access. CREATE ROLE names the role, and
    # DD-ACCESS-001 describes it in prose — neither confers anything, and sweeping
    # them in makes the assertion about substrings rather than about privilege.
    everything = sum((v for k, v in pl.items()), [])
    nopii_grants = [g for g in everything
                    if g.lstrip().upper().startswith("GRANT") and "ROLE_READ_NOPII" in g]
    check("every grant to the restricted role names a view",
          nopii_grants and all("_NoPII" in g for g in nopii_grants), str(nopii_grants))
    check("and never on a module database",
          not any(re.search(rf"ON {P}_(Domain|Semantic|Memory|Observability) TO "
                            rf"{P}_ROLE_READ_NOPII", g) for g in nopii_grants))
    # a table with no PII still gets a view, so the role sees the whole product
    check("a PII-free table is still exposed",
          any('Incident_NoPII' in g for g in grants), str(grants))


# ── the recorded decision ───────────────────────────────────────────────────
def t_decision_is_recorded_and_safe():
    sql = design_decision(P, 511, 6)
    check("targets the product's Memory module",
          f"INSERT INTO {P}_Memory.Design_Decision" in sql)
    check("carries the framework's id", "'DD-ACCESS-001'" in sql)
    check("states the PII column count", "511" in sql)
    check("states the view count", "6 PII-free views" in sql)
    check("marked current", "is_current" in sql and ", 1," in sql)
    check("apostrophes are escaped, not left to break the statement",
          "''" in design_decision(P, 1, 1, decided_by="O'Brien"),
          design_decision(P, 1, 1, decided_by="O'Brien")[:80])


# ── the agent's login ───────────────────────────────────────────────────────
def t_service_account():
    stmts = service_account(P, "nibrs_agent", "s3cret")
    joined = " ".join(stmts)
    check("owns no space of its own (PERM = 0)", "PERM = 0" in joined, joined)
    check("holds the agent role", f"GRANT {P}_ROLE_AGENT TO nibrs_agent;" in joined)
    check("the role is its DEFAULT, so a forgotten SET ROLE still works",
          "DEFAULT ROLE" in joined)
    check("it is granted no other role",
          joined.count("GRANT ") == 1, joined)
    check("it is not given the admin role", "ROLE_ADMIN" not in joined)


def t_identity_names_are_derived():
    # one identity per product, every name derived from the product — no hand list
    check("account = {product}_agent, lowercase", service_account_name("NIBRS_Source") == "nibrs_source_agent")
    check("endpoint = teradata-mcp-{product}, kebab", endpoint_name("CASE_Source") == "teradata-mcp-case-source")
    check("Uderia entry follows the 'Teradata MCP - <identity>' pattern",
          mcp_entry_name("NIBRS_Source") == "Teradata MCP - nibrs_source_agent")
    check("two products never share an identity",
          len({f(p) for p in ("NIBRS_Source", "CASE_Source") for f in (service_account_name,)}) == 2)
    stmts = " ".join(service_account("CASE_Source", None, "s3cret"))
    check("the account defaults to the derived name and holds ONLY its own product's agent role",
          "CREATE USER case_source_agent" in stmts and "GRANT CASE_Source_ROLE_AGENT TO case_source_agent;" in stmts
          and "NIBRS" not in stmts and stmts.count("GRANT ") == 1, stmts)


def t_plan_is_complete_and_ordered():
    pl = plan()
    for key in ("roles", "phase_1_5", "phase_2_5", "views", "view_grants", "decision"):
        check(f"plan has {key}", key in pl and pl[key], key)
    check("one view per table that has any non-PII column", len(pl["views"]) == 3,
          str(len(pl["views"])))
    check("a grant for every view", len(pl["view_grants"]) == len(pl["views"]))
    check("deterministic", emit_plan(P, TABLES, PII) == pl)
    check("no statement is unterminated",
          all(s.rstrip().endswith(";") for v in pl.values() for s in v))


# ── the ESTATE role ─────────────────────────────────────────────────────────────
#
# The estate role's whole safety argument is that it holds EXACTLY the union of the
# product roles it nests and can never widen them. That property fails silently in
# both directions: a dropped member looks like a working system until someone needs
# that product, and a direct grant on the estate role looks like a working system
# forever while bypassing every per-product rule above.

ESTATE_SCOPE = "Policing_Estate"
ESTATE_PRODUCTS = ("NIBRS_Source", "CASE_Source")


def t_estate_role_nests_and_never_widens():
    stmts = estate_role(ESTATE_SCOPE, ESTATE_PRODUCTS)
    joined = " ".join(stmts)
    check("the estate role is created",
          "CREATE ROLE Policing_Estate_ROLE_AGENT;" in stmts, joined)
    for p in ESTATE_PRODUCTS:
        check(f"{p}'s agent role is nested inside the estate role",
              f"GRANT {p}_ROLE_AGENT TO Policing_Estate_ROLE_AGENT;" in stmts, joined)
    # THE property: every GRANT is a role grant. A direct privilege grant here would
    # give the estate role rights no product role holds — the one way nesting stops
    # being a union and starts being a widening.
    grants = [s for s in stmts if s.startswith("GRANT ")]
    check("every grant on the estate role is a ROLE grant, never a privilege",
          all(re.match(r"GRANT \w+_ROLE_\w+ TO ", g) for g in grants), str(grants))
    check("the estate role grants no privilege on a database or table",
          " ON " not in joined, joined)
    check("the estate role writes nothing of its own — the asymmetry is inherited",
          not any(w in joined.upper() for w in ("INSERT", "UPDATE", "DELETE")), joined)
    check("one grant per product in scope, no more",
          len(grants) == len(ESTATE_PRODUCTS), str(grants))
    # a further product joins with ONE more grant and nothing else changes
    three = estate_role(ESTATE_SCOPE, list(ESTATE_PRODUCTS) + ["Fitness360"])
    check("adding a product to the estate adds exactly one statement",
          len(three) == len(stmts) + 1 and three[:len(stmts)] == stmts, str(three))


def t_estate_account_holds_only_the_estate_role():
    stmts = estate_service_account(ESTATE_SCOPE, ESTATE_PRODUCTS, "s3cret")
    joined = " ".join(stmts)
    check("the account name derives through the same helper as a product's",
          "CREATE USER policing_estate_agent " in joined, joined)
    check("PERM 0 — the estate identity owns no objects",
          "PERM = 0" in joined, joined)
    role_grants = [s for s in stmts if s.startswith("GRANT ")]
    check("it holds EXACTLY ONE role", len(role_grants) == 1, str(role_grants))
    check("and that role is the estate role, not a product role",
          role_grants[0] == "GRANT Policing_Estate_ROLE_AGENT TO policing_estate_agent;",
          str(role_grants))
    # holding the product roles directly would work today and re-create the drift the
    # estate role exists to remove: every new product would edit the identity.
    check("the account never holds a product role directly",
          not any(f"{p}_ROLE_AGENT TO policing_estate_agent" in joined
                  for p in ESTATE_PRODUCTS), joined)
    check("DEFAULT ROLE names the estate role — not ALL, which says only "
          "'whatever this account happens to hold'",
          "DEFAULT ROLE = Policing_Estate_ROLE_AGENT;" in joined
          and "DEFAULT ROLE = ALL" not in joined, joined)
    check("the account is granted no privilege directly", " ON " not in joined, joined)


def t_estate_names_use_the_product_helpers():
    # No second naming convention: the estate is a SCOPE handed to the same helpers.
    check("role name derives through role_name(scope, suffix)",
          role_name(ESTATE_SCOPE, ESTATE_SUFFIX) == "Policing_Estate_ROLE_AGENT",
          role_name(ESTATE_SCOPE, ESTATE_SUFFIX))
    check("account name", service_account_name(ESTATE_SCOPE) == "policing_estate_agent",
          service_account_name(ESTATE_SCOPE))
    check("endpoint name", endpoint_name(ESTATE_SCOPE) == "teradata-mcp-policing-estate",
          endpoint_name(ESTATE_SCOPE))
    check("Uderia entry name",
          mcp_entry_name(ESTATE_SCOPE) == "Teradata MCP - policing_estate_agent",
          mcp_entry_name(ESTATE_SCOPE))
    check("the estate takes the AGENT suffix — same meaning, wider scope",
          ESTATE_SUFFIX == "ROLE_AGENT", ESTATE_SUFFIX)
    check("the estate identity is distinct from every product identity",
          service_account_name(ESTATE_SCOPE) not in
          {service_account_name(p) for p in ESTATE_PRODUCTS})


def t_estate_plan():
    pl = emit_estate_plan(ESTATE_SCOPE, ESTATE_PRODUCTS, "s3cret")
    check("role precedes account (an account cannot default to a role that is absent)",
          list(pl) == ["role", "account"], str(list(pl)))
    check("deterministic", emit_estate_plan(ESTATE_SCOPE, ESTATE_PRODUCTS, "s3cret") == pl)
    check("no statement is unterminated",
          all(s.rstrip().endswith(";") for v in pl.values() for s in v))
    role_only = emit_estate_plan(ESTATE_SCOPE, ESTATE_PRODUCTS)
    check("without a password the reviewable half is emitted and no account is",
          role_only["role"] == pl["role"] and role_only["account"] == [],
          str(role_only))


def t_decision_projector_survives_shape_divergence():
    """Both failures below were found by the DATABASE, mid-apply, not by review.

    The two products' decision tables have now diverged twice: the v2 uplift added
    NOT NULL audit columns the reference shape never had, and CASE requires
    `module_name` while the first-match loop fills its alias `source_module`. Either
    surfaces as Teradata 3811 half way through an apply.
    """
    CASE_COLS = ["decision_id", "module_name", "decision_title", "context_description",
                 "decision_taken", "alternatives_considered", "consequences",
                 "decision_status", "decided_by", "is_active", "source_module"]
    stmt = estate_design_decision("CASE_Source", "Policing_Estate",
                                  ["NIBRS_Source", "CASE_Source"],
                                  available_columns=CASE_COLS,
                                  required_columns=["module_name"])
    check("a NOT NULL column whose ALIAS was filled is filled too",
          "module_name" in stmt, stmt[:200])
    NIBRS_COLS = ["decision_id", "decision_title", "context", "rationale",
                  "consequences", "valid_from_dts"]
    stmt2 = estate_design_decision("NIBRS_Source", "Policing_Estate",
                                   ["NIBRS_Source", "CASE_Source"],
                                   available_columns=NIBRS_COLS,
                                   required_columns=["valid_from_dts"])
    check("a NOT NULL audit column the reference shape lacks is supplied",
          "valid_from_dts" in stmt2, stmt2[:200])
    # and a shape this emitter does NOT know must be refused BY NAME, offline —
    # guessing a value for a NOT NULL column writes a confident wrong record.
    try:
        project_decision("NIBRS_Source", {"id": "DD-X", "title": "t"},
                         available_columns=["decision_id", "decision_title", "tenant_id"],
                         required_columns=["tenant_id"])
        check("an unknown NOT NULL column is refused", False, "no refusal raised")
    except DecisionShapeError as exc:
        check("an unknown NOT NULL column is refused, naming it", "tenant_id" in str(exc), str(exc))


def t_decision_text_fits_a_LATIN_column():
    """Teradata 6706 — the products' text columns are LATIN and every doc in this repo
    is written with em dashes and curly quotes."""
    for bad in ("\u2014", "\u2019", "\u201c", "\u2192"):
        check(f"U+{ord(bad):04X} is transliterated, not emitted",
              bad in LATIN_TRANSLITERATIONS)
    stmt = project_decision("NIBRS_Source",
                            {"id": "DD-X", "title": "a \u2014 b \u2019c\u2019 \u2192 d"},
                            available_columns=["decision_id", "decision_title"])
    check("the emitted statement is pure ASCII", all(ord(c) < 128 for c in stmt), stmt)
    try:
        project_decision("NIBRS_Source", {"id": "DD-X", "title": "check \u2713"},
                         available_columns=["decision_id", "decision_title"])
        check("a character LATIN cannot store is refused", False, "no refusal raised")
    except DecisionShapeError as exc:
        check("a character LATIN cannot store is refused, naming it",
              "2713" in str(exc), str(exc))
    # the real records this pack writes must already be clean
    for product in ("NIBRS_Source", "CASE_Source"):
        st = estate_design_decision(product, "Policing_Estate",
                                    ["NIBRS_Source", "CASE_Source"])
        check(f"DD-ACCESS-002 for {product} is ASCII as authored",
              all(ord(c) < 128 for c in st))


def t_estate_decision_is_recorded_in_every_product():
    products = ["NIBRS_Source", "CASE_Source"]
    for product in products:
        st = estate_design_decision(product, "Policing_Estate", products)
        other = [p for p in products if p != product][0]
        check(f"{product}'s record names the OTHER product in scope", other in st, st[:200])
        check(f"{product}'s record is written into ITS OWN Memory",
              st.startswith(f"INSERT INTO {product}_Memory.Design_Decision"), st[:80])
        check(f"{product}'s record carries the estate role name",
              "Policing_Estate_ROLE_AGENT" in st)
        check(f"{product}'s record states the identity holds that role and nothing else",
              "and nothing else" in st)


for fn in [t_agent_cannot_write_the_data, t_read_and_admin,
           t_phases_are_split_as_the_framework_says, t_pii_never_appears_in_a_view,
           t_pii_match_is_case_insensitive, t_all_pii_table_yields_no_view,
           t_restricted_role_has_no_path_to_a_base_table, t_decision_is_recorded_and_safe,
           t_service_account, t_identity_names_are_derived, t_plan_is_complete_and_ordered,
           t_estate_role_nests_and_never_widens, t_estate_account_holds_only_the_estate_role,
           t_estate_names_use_the_product_helpers, t_estate_plan,
           t_decision_projector_survives_shape_divergence,
           t_decision_text_fits_a_LATIN_column,
           t_estate_decision_is_recorded_in_every_product]:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
