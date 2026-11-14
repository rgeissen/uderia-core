"""
Semantic demo-data generator — the gate.

CI-pure (no Teradata, no network).  Drives the REAL ``SemanticDataSource`` over a
small industry-model document shaped exactly like Databricks' ``model.json``
(domains → products → attributes with value_regex / tags / foreign_key_to /
descriptions), through the REAL ``extract_physical_model`` + ``flatten`` path the
deploy takes, and asserts the PROPERTIES the generator promises:

  §1 regex_gen        every declared shape family → a value that fullmatches; the
                      unsupported forms → None (never a wrong string)
  §2 declarations     an alternation's every option appears, weighted toward the
                      author's first; a shape regex is honoured; ISO vocab where
                      the description names the standard; description examples
                      fill a weak column and never override a strong one
  §3 coherence        one place per row (city/state/country/currency/phone agree);
                      e-mail derives from the row's name; person vs company names
  §4 time             anchors trend toward recent; start <= end, created <= updated;
                      birth 18–90y back; a child's date is never before its parent's
  §5 keys             FK values ALWAYS exist in the parent (incl. on the FLAT model
                      — the bug both generators had), Zipf-skewed; PKs unique;
                      self-reference sampled from the table's own rows
  §6 volume           role-scaled rows, reference tables capped, per-model cap,
                      MIN_ROWS floor
  §7 hygiene          determinism (byte-identical twice), ASCII only, DECIMAL fits
                      its precision, NULL only where nullable and bounded
  §8 wiring           the deploy seam selects the profile; the REST payload carries
                      it; the loader chunks executemany

Run:  python test/test_semantic_datasource.py
"""

import copy
import json
import os
import random
import re
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.dbdeploy import regex_gen  # noqa: E402
from trusted_data_agent.dbdeploy.datasource import SyntheticDataSource  # noqa: E402
from trusted_data_agent.dbdeploy.physical_model import (  # noqa: E402
    extract_physical_model, flatten_to_single_database,
)
from trusted_data_agent.dbdeploy.semantic_datasource import (  # noqa: E402
    DEFAULT_MAX_ROWS_PER_MODEL, LOOKUP_MAX_ROWS, MIN_ROWS, ROLE_ROW_FACTORS, TODAY,
    SemanticDataSource, _ascii,
)
from trusted_data_agent.dbdeploy import vocab  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}" + (f"  — {detail}" if detail else ""))


# ── fixture: a model.json in the Databricks shape ────────────────────────────

def attr(name, typ="STRING", desc="", regex="", fk="", tags="", term=None):
    return {"name": name, "column_name": name, "type": typ, "description": desc,
            "value_regex": regex, "tags": tags, "foreign_key_to": fk,
            "business_glossary_term": term or name.replace("_", " ").title(), "references": ""}


def product(name, pk, attrs, desc=""):
    return {"name": name, "table_name": name, "primary_key": pk, "description": desc,
            "subdomain": "core", "type": "core", "attributes": attrs}


MODEL = {
    "model_requirements": {"model_conventions": {"deployment_catalog": "acme_mvm"}},
    "model": {
        "name": "acme", "domains": [
            {"name": "reference", "division": "shared", "database_name": "reference", "products": [
                product("country", "country_id", [
                    attr("country_id", "BIGINT", tags="primary_key"),
                    attr("country_name", desc="Official short name of the country."),
                    attr("iso_alpha_3_code", regex="^[A-Z]{3}$", desc="ISO 3166-1 alpha-3 code."),
                    attr("iso_alpha_2_code", regex="^[A-Z]{2}$", desc="ISO 3166-1 alpha-2 code."),
                    attr("calling_code", desc="International dialing prefix."),
                    attr("time_zone", desc="Primary time zone, e.g., America/New_York, Europe/London."),
                    attr("active_flag", "BOOLEAN"),
                ]),
                product("status_code", "status_code_id", [
                    attr("status_code_id", "BIGINT", tags="primary_key"),
                    attr("code", regex="open|in_progress|resolved|closed"),
                    attr("description", desc="Meaning of the code."),
                ]),
            ]},
            {"name": "customer", "division": "front", "database_name": "customer", "products": [
                product("party", "party_id", [
                    attr("party_id", "BIGINT", tags="primary_key"),
                    attr("domicile_country_id", "BIGINT", fk="reference.country.country_id"),
                    attr("parent_party_id", "BIGINT", fk="customer.party.party_id", tags="self_ref_fk"),
                    attr("first_name", tags="pii"), attr("last_name", tags="pii"),
                    attr("email_address", regex=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"),
                    attr("phone_number", regex=r"^\+?[1-9]\d{7,14}$"),
                    attr("city"), attr("state_code"), attr("country_code", regex="^[A-Z]{2}$",
                                                                desc="ISO 3166-1 alpha-2 of the residence."),
                    attr("postal_code"), attr("currency_code", regex="^[A-Z]{3}$", desc="ISO 4217."),
                    attr("customer_segment", regex="retail|corporate|institutional|government"),
                    attr("risk_rating", regex="low|medium|high|prohibited"),
                    attr("source_system_code",
                         desc="System of record the party came from (e.g., T24 for Temenos, FIS_PROFILE for FIS, LEGACY)."),
                    attr("date_of_birth", "DATE"), attr("kyc_status", regex="pending|verified|expired|rejected"),
                    attr("created_timestamp", "TIMESTAMP"), attr("updated_timestamp", "TIMESTAMP"),
                    attr("is_deleted", "BOOLEAN"), attr("is_active", "BOOLEAN"),
                    attr("annual_income_amount", "DECIMAL(18,2)"), attr("notes"),
                    attr("employer_name"), attr("cif_number", regex="^CN-[0-9]{8}$"),
                ]),
                product("deposit_account", "deposit_account_id", [
                    attr("deposit_account_id", "BIGINT", tags="primary_key"),
                    attr("party_id", "BIGINT", fk="customer.party.party_id"),
                    attr("currency_code", regex="^[A-Z]{3}$", desc="ISO 4217 currency."),
                    attr("opening_date", "DATE"), attr("closing_date", "DATE"),
                    attr("current_balance_amount", "DECIMAL(18,2)"),
                    attr("interest_rate", "DECIMAL(7,4)"), attr("overdraft_limit_amount", "DECIMAL(15,2)"),
                    attr("account_status", regex="active|dormant|closed"),
                    attr("product_name"),
                ]),
            ]},
            {"name": "transaction", "division": "ops", "database_name": "transaction", "products": [
                product("account_transaction", "account_transaction_id", [
                    attr("account_transaction_id", "BIGINT", tags="primary_key"),
                    attr("deposit_account_id", "BIGINT", fk="customer.deposit_account.deposit_account_id"),
                    attr("status_code_id", "BIGINT", fk="reference.status_code.status_code_id"),
                    attr("transaction_date", "DATE"), attr("settlement_date", "DATE"),
                    attr("amount", "DECIMAL(18,2)"), attr("fee_amount", "DECIMAL(9,2)"),
                    attr("merchant_category_code", regex="^[0-9]{4}$"),
                    attr("transaction_type", regex="debit|credit|fee|interest|reversal"),
                    attr("narrative"), attr("reference_number"), attr("item_count", "INT"),
                    attr("completion_pct", "DECIMAL(5,2)"), attr("posted_flag", "BOOLEAN"),
                    attr("replenishment_status", regex="normal|triggered|vm\u0131"),   # an author's dotless-i typo
                ]),
            ]},
        ],
        "metric_views": [],
    },
}


def model():
    pm = extract_physical_model(copy.deepcopy(MODEL), industry="acme", flavour="mvm")
    return flatten_to_single_database(pm, "acme_mvm")


def gen(rows=60, cap=DEFAULT_MAX_ROWS_PER_MODEL, seed=1469, null_rate=0.03):
    src = SemanticDataSource(rows_per_table=rows, seed=seed, max_rows_per_model=cap, null_rate=null_rate)
    pm = model()
    return src, pm, src.generate(pm)


def T(data, name):
    return data[("acme_mvm", name)]


# ── §1 regex_gen ─────────────────────────────────────────────────────────────

def t_regex_gen():
    print("\n[§1 regex_gen]")
    rng = random.Random(7)
    shapes = ["^[A-Z]{3}$", "^[A-Z0-9]{8}$", "^[0-9]{9}$", r"^\d{4}$", r"^\+?[1-9]\d{7,14}$",
              r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", "^[A-Z0-9_-]{6,12}$",
              "^[a-z]{2}(-[A-Z]{2})?$", "^([01]?[0-9]|2[0-3]):[0-5][0-9]$", "^PO-[0-9]{6}$",
              "^[A-Z]{2}-[0-9]{4}-[0-9]{2}$", "^FY[0-9]{4}$", r"^[0-9]+\.[0-9]+(\.[0-9]+)?$",
              "^https?://.*$", "^[0-9]{4}-(0[1-9]|1[0-2])$", "[A-Z]{3}", r"NP-\d{8}", "^[A-HJ-NPR-Z0-9]{17}$",
              r"^\+?[0-9\s\-().]{7,20}$", "^[A-Z]$"]
    all_ok = True
    for p in shapes:
        vals = [regex_gen.generate(p, rng) for _ in range(30)]
        ok = all(v is not None and re.fullmatch(p, v) for v in vals)
        all_ok &= ok
        if not ok:
            print("    shape failed:", p, vals[:3])
    check("every shape family yields fullmatching values (30 each)", all_ok)
    check("unsupported → None, never a guess",
          all(regex_gen.generate(p, rng) is None for p in ["(?=a)b", r"(\w)\1", "[^a-z]+", "E|*|", r"^https?://[^\s]{3,255}$"]))
    check("supports() agrees with generate()",
          all(regex_gen.supports(p) for p in shapes) and not regex_gen.supports("[^x]"))
    check("alternation detection incl. trailing-plus band labels",
          regex_gen.is_alternation("low|medium|high") and regex_gen.is_alternation("0-30|31-60|90+")
          and not regex_gen.is_alternation("[0-9]+|[A-Z]+") and not regex_gen.is_alternation("^[A-Z]{3}$"))
    check("alternation options in the author's order",
          regex_gen.alternation_options("^a|b c|d-e$") == ["a", "b c", "d-e"])
    r1, r2 = random.Random(3), random.Random(3)
    check("deterministic for the same rng state",
          [regex_gen.generate("^[A-Z0-9]{6}$", r1) for _ in range(5)] == [regex_gen.generate("^[A-Z0-9]{6}$", r2) for _ in range(5)])
    check("non-ASCII class ranges are refused (LATIN columns)", not regex_gen.supports("[À-ÿ]{3}"))


# ── §2 declarations ─────────────────────────────────────────────────────────

def t_declarations():
    print("\n[§2 declarations]")
    src, pm, data = gen(rows=200)
    party, txn = T(data, "party"), T(data, "account_transaction")
    seg = Counter(r["customer_segment"] for r in party if r["customer_segment"] is not None)
    check("alternation: every declared option appears", set(seg) == {"retail", "corporate", "institutional", "government"}, str(seg))
    check("alternation: weighted toward the author's first option", seg["retail"] > 1.5 * seg["government"], str(seg))
    rs = {r["replenishment_status"] for r in txn if r["replenishment_status"] is not None}
    check("a declared option with a non-ASCII typo is transliterated, never loaded as-is", rs <= {"normal", "triggered", "vmi"} and "vmi" in rs, str(rs))
    check("shape regex honoured (merchant_category_code ^[0-9]{4}$)",
          all(re.fullmatch(r"[0-9]{4}", r["merchant_category_code"]) for r in txn if r["merchant_category_code"] is not None))
    check("shape regex with literal prefix honoured (cif_number)",
          all(re.fullmatch(r"CN-[0-9]{8}", r["cif_number"]) for r in party if r["cif_number"] is not None))
    a3 = {r["iso_alpha_3_code"] for r in T(data, "country")}
    check("ISO vocabulary where the description names the standard (alpha-3)",
          a3 <= set(vocab.COUNTRY_ALPHA3) and len(a3) > 5, str(list(a3)[:6]))
    cur = {r["currency_code"] for r in T(data, "deposit_account") if r["currency_code"]}
    check("ISO 4217 currencies, home-market heavy",
          cur <= set(vocab.CURRENCIES) | {p[6] for p in vocab.PLACES}
          and Counter(r["currency_code"] for r in T(data, "deposit_account")).most_common(1)[0][0] == "USD", str(cur))
    ss = {r["source_system_code"] for r in party if r["source_system_code"] is not None}
    check("description examples fill a WEAK column ('e.g., T24 for …, FIS_PROFILE for …, LEGACY')",
          ss == {"T24", "FIS_PROFILE", "LEGACY"}, str(ss))
    tz = {r["time_zone"] for r in T(data, "country") if r["time_zone"] is not None}
    check("description examples never override a STRONG reading (time_zone follows the row's place)",
          tz <= {p[9] for p in vocab.PLACES} and len(tz) > 2, str(list(tz)[:4]))
    notes = src.describe_plan()
    check("every column carries an explanation", all(("acme_mvm", t.name, c.name) in notes for t in pm.tables for c in t.columns))
    check("the explanation names the source ('declared list' / 'foreign key' / 'primary key')",
          "declared list" in notes[("acme_mvm", "party", "customer_segment")]
          and notes[("acme_mvm", "party", "domicile_country_id")].startswith("foreign key")
          and "primary key" in notes[("acme_mvm", "party", "party_id")])


# ── §3 coherence ────────────────────────────────────────────────────────────

def t_coherence():
    print("\n[§3 coherence]")
    src, pm, data = gen(rows=150)
    party = T(data, "party")
    by_city = {p[0]: p for p in vocab.PLACES}
    ok = True
    placed = [r for r in party if r["city"] is not None and r["state_code"] is not None and r["country_code"] is not None]
    check("optional address fields may be NULL but most rows carry a place", len(placed) > 0.8 * len(party))
    for r in placed:
        p = by_city.get(r["city"])
        if p is None or r["state_code"] != p[1] or r["country_code"] != p[4] or not r["phone_number"] or r["currency_code"] not in (p[6],) + tuple(vocab.CURRENCIES):
            ok = False; break
    check("city, state, country code, phone prefix agree within a row", ok)
    check("currency mostly follows the row's country",
          sum(1 for r in placed if r["currency_code"] == by_city[r["city"]][6]) > 0.6 * len(placed))
    em = [r for r in party if r["email_address"]]
    check("e-mail derives from the row's own name",
          all(r["first_name"].lower() in r["email_address"] and r["last_name"].lower().replace("'", "") in r["email_address"] for r in em))
    check("e-mail still matches its declared shape",
          all(re.fullmatch(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", r["email_address"]) for r in em))
    check("names come from the vocabulary",
          all(r["first_name"] in vocab.FIRST_NAMES and r["last_name"] in vocab.LAST_NAMES for r in party))
    check("employer is a company name, not a person",
          all(r["employer_name"] is None or r["employer_name"].split()[-1] in vocab.COMPANY_SUFFIXES for r in party))
    check("product_name reads as a product", all("Deposit Account" in r["product_name"] for r in T(data, "deposit_account") if r["product_name"]))
    check("free text is a curated sentence", all(r["narrative"] in vocab.SENTENCES for r in T(data, "account_transaction") if r["narrative"]))
    ctry = T(data, "country")
    check("a reference table walks countries sequentially (distinct rows)",
          len({r["country_name"] for r in ctry}) == min(len(ctry), len(vocab.COUNTRY_PLACES)))
    check("calling_code is the country's prefix", all(r["calling_code"].startswith("+") for r in ctry))


# ── §4 time ─────────────────────────────────────────────────────────────────

def t_time():
    print("\n[§4 time]")
    src, pm, data = gen(rows=400)
    party, acct, txn = T(data, "party"), T(data, "deposit_account"), T(data, "account_transaction")
    years = Counter(r["transaction_date"].year for r in txn)
    check("anchors trend toward recent (this year > two years ago)", years[TODAY.year] > years[TODAY.year - 2], str(dict(years)))
    check("no date after today for created/anchor columns", all(r["transaction_date"] <= TODAY for r in txn))
    check("created <= updated within a row",
          all(r["created_timestamp"] <= r["updated_timestamp"] for r in party if r["updated_timestamp"]))
    check("opening <= closing within a row",
          all(r["closing_date"] is None or r["opening_date"] <= r["closing_date"] for r in acct))
    check("birth dates 18–90 years before the anchor",
          all(18 * 365 <= (r["created_timestamp"].date() - r["date_of_birth"]).days <= 90 * 365 + 5 for r in party if r["date_of_birth"]))
    by_acct = {r["deposit_account_id"]: r for r in acct}
    by_party = {r["party_id"]: r for r in party}
    check("a transaction is never dated before its account was opened",
          all(r["deposit_account_id"] is None or r["transaction_date"] >= by_acct[r["deposit_account_id"]]["opening_date"] for r in txn))
    check("an account is never opened before its party was created",
          all(r["party_id"] is None or r["opening_date"] >= by_party[r["party_id"]]["created_timestamp"].date() for r in acct))
    check("timestamps are datetimes, dates are dates",
          isinstance(party[0]["created_timestamp"], datetime) and isinstance(txn[0]["transaction_date"], date)
          and not isinstance(txn[0]["transaction_date"], datetime))


# ── §5 keys ─────────────────────────────────────────────────────────────────

def t_keys():
    print("\n[§5 keys]")
    src, pm, data = gen(rows=300)
    party, acct, txn = T(data, "party"), T(data, "deposit_account"), T(data, "account_transaction")
    pids = {r["party_id"] for r in party}
    check("PKs unique", len(pids) == len(party) and len({r["account_transaction_id"] for r in txn}) == len(txn))
    check("FK values always exist in the parent (flat model)",
          all(r["party_id"] is None or r["party_id"] in pids for r in acct)
          and all(r["deposit_account_id"] is None or r["deposit_account_id"] in {a["deposit_account_id"] for a in acct} for r in txn)
          and all(r["status_code_id"] is None or r["status_code_id"] in {s["status_code_id"] for s in T(data, "status_code")} for r in txn))
    check("self-reference samples the table's own rows",
          all(r["parent_party_id"] is None or r["parent_party_id"] in pids for r in party)
          and any(r["parent_party_id"] is not None for r in party))
    share = Counter(r["deposit_account_id"] for r in txn if r["deposit_account_id"]).most_common(1)[0][1]
    check("FK sampling is Zipf-skewed (a hot account holds many transactions)", share > 4 * (len(txn) / len(acct)), f"top={share}")
    check("a few children are unassigned, never orphaned",
          0 < sum(1 for r in txn if r["deposit_account_id"] is None) < 0.1 * len(txn))
    # the pre-existing bug: the BASIC generator on the flat model
    basic = SyntheticDataSource(rows_per_table=40).generate(model())
    bp = {r["party_id"] for r in basic[("acme_mvm", "party")]}
    check("the basic generator's FKs are valid on the flat model too (was: random integers)",
          all(r["party_id"] in bp for r in basic[("acme_mvm", "deposit_account")]))


# ── §6 volume ───────────────────────────────────────────────────────────────

def t_volume():
    print("\n[§6 volume]")
    src = SemanticDataSource(rows_per_table=100)
    pm = model()
    planned = src.plan_rows(pm)
    roles = src.roles()
    k = lambda n: ("acme_mvm", n)  # noqa: E731
    check("roles from the FK graph: reference / master|hub / event",
          roles[k("country")] == "reference" and roles[k("status_code")] == "reference"
          and roles[k("account_transaction")] == "event" and roles[k("party")] in ("master", "hub"),
          str(roles))
    check("event tables get the fact factor", planned[k("account_transaction")] == int(100 * ROLE_ROW_FACTORS["event"]))
    check("reference tables are capped", planned[k("country")] <= LOOKUP_MAX_ROWS and planned[k("status_code")] <= LOOKUP_MAX_ROWS)
    capped = SemanticDataSource(rows_per_table=100, max_rows_per_model=300)
    pc = capped.plan_rows(pm)
    check("per-model cap scales the plan down, keeping the floor",
          sum(pc.values()) <= 300 + MIN_ROWS * len(pc) and all(v >= MIN_ROWS for v in pc.values()), str(pc))
    data = capped.generate(pm)
    check("generation honours the plan", all(len(data[key]) == n for key, n in pc.items()))


# ── §7 hygiene ──────────────────────────────────────────────────────────────

def t_hygiene():
    print("\n[§7 hygiene]")
    a = gen(rows=80)[2]
    b = gen(rows=80)[2]
    dump = lambda d: json.dumps({str(k): v for k, v in d.items()}, default=str, sort_keys=True)  # noqa: E731
    check("byte-identical on a second run (same seed)", dump(a) == dump(b))
    c = gen(rows=80, seed=99)[2]
    check("a different seed → different data", dump(a) != dump(c))
    src, pm, data = gen(rows=200)
    strings = [v for rows in data.values() for r in rows for v in r.values() if isinstance(v, str)]
    check("every string is ASCII", all(v.isascii() for v in strings))
    check("_ascii transliterates authors' typos", _ascii("vmı") == "vmi" and _ascii("Straße – Zürich") == "Strasse - Zurich")
    ok = True
    for t in pm.tables:
        for col in t.columns:
            m = re.search(r"\((\d+),(\d+)\)", col.dbx_type)
            if not m:
                continue
            lim = Decimal(10) ** (int(m.group(1)) - int(m.group(2)))
            for r in data[(t.database_name, t.name)]:
                v = r[col.name]
                if v is not None and (not isinstance(v, Decimal) or v >= lim or v < 0):
                    ok = False
    check("every DECIMAL fits its precision and is non-negative", ok)
    amt = [float(r["amount"]) for r in T(data, "account_transaction") if r["amount"] is not None]
    check("amounts are log-normal (median well below the max, all positive)",
          sorted(amt)[len(amt) // 2] * 8 < max(amt) and min(amt) > 0)
    fee = [float(r["fee_amount"]) for r in T(data, "account_transaction") if r["fee_amount"] is not None]
    check("fees are small money, amounts medium money", sorted(fee)[len(fee) // 2] < sorted(amt)[len(amt) // 2])
    rates = [float(r["interest_rate"]) for r in T(data, "deposit_account")]
    check("a DECIMAL(7,4) rate is a small percentage (0–35)", all(0 <= x <= 35 for x in rates) and max(rates) > 1)
    check("booleans are never NULL (deployed BYTEINT NOT NULL)",
          all(r["is_deleted"] in (0, 1) and r["is_active"] in (0, 1) for r in T(data, "party")))
    pct = [float(r["completion_pct"]) for r in T(data, "account_transaction") if r["completion_pct"] is not None]
    check("a DECIMAL(5,2) pct is 0–100", all(0 <= x <= 100 for x in pct))
    party = T(data, "party")
    check("NULL never on a NOT NULL / key column",
          all(r["party_id"] is not None and r["first_name"] is not None for r in party))
    nn = sum(1 for r in party if r["notes"] is None) / len(party)
    check("optional columns carry a bounded share of NULLs", 0.02 < nn < 0.6, f"{nn:.2f}")
    check("a flag named is_deleted is mostly false, is_active mostly true",
          sum(r["is_deleted"] for r in party) < 0.2 * len(party) and sum(r["is_active"] for r in party) > 0.7 * len(party))
    check("booleans are 0/1 ints", all(r["posted_flag"] in (0, 1) for r in T(data, "account_transaction")))
    check("item_count is a small positive int", all(isinstance(r["item_count"], int) and r["item_count"] >= 1 for r in T(data, "account_transaction") if r["item_count"] is not None))


# ── §8 wiring ───────────────────────────────────────────────────────────────

def t_wiring():
    print("\n[§8 wiring]")
    import ast
    root = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent")
    dm_path = os.path.join(root, "agent", "kg_constructors", "declarative_model.py")
    if os.path.exists(dm_path):  # distribution-aware (the Industry Data Model constructor ships with neither distribution)
        dm = open(dm_path).read()
        check("the deploy seam selects SemanticDataSource unless data_profile == 'basic'",
              "SemanticDataSource(" in dm and '"basic"' in dm and "max_rows_per_model" in dm)
        check("the deploy summary reports the data profile", 'info["data_profile"]' in dm)
    rr = open(os.path.join(root, "api", "rest_routes.py")).read()
    check("the REST payload carries data_profile + max_rows_per_model",
          '"data_profile": (data.get("data_profile") or "semantic")' in rr and '"max_rows_per_model"' in rr)
    modal_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "handlers", "rag", "kgIndustryModelModal.js")
    if os.path.exists(modal_path):
        modal = open(modal_path).read()
        check("the constructor modal exposes profile / base rows / max rows and sends them",
              "kg-im-data-profile" in modal and "payload.data_profile" in modal and "payload.rows_per_table" in modal
              and "payload.max_rows_per_model" in modal)
    # loader chunking, driven through the real _executemany_payloads with a fake cursor
    from trusted_data_agent.dbdeploy.teradata_backend import TeradataDeploymentBackend

    class FakeCur:
        def __init__(self): self.calls = []
        def executemany(self, sql, rows): self.calls.append(len(rows))

    class FakeConn:
        def __init__(self, cur): self.c = cur
        def cursor(self): return self.c
        def commit(self): pass
        def close(self): pass

    be = TeradataDeploymentBackend.__new__(TeradataDeploymentBackend)
    cur = FakeCur()
    be._open_load_connection = lambda: FakeConn(cur)
    be._q = staticmethod(lambda x: f'"{x}"')
    be.LOAD_BATCH_ROWS = 1000
    rows_by, errs = be._executemany_payloads([("db.t", "db", "t", ["a"], [[i] for i in range(2500)], False)])
    check("executemany is chunked (2500 rows → 1000+1000+500)", cur.calls == [1000, 1000, 500] and rows_by["db.t"] == 2500 and not errs, str(cur.calls))
    # a chunk failure is recorded, rows loaded so far are counted
    class BadCur(FakeCur):
        def executemany(self, sql, rows):
            super().executemany(sql, rows)
            if len(self.calls) == 2:
                raise RuntimeError("boom")
    bad = BadCur()
    be._open_load_connection = lambda: FakeConn(bad)
    be._extract_td_error = lambda c, e: str(e)
    rows_by, errs = be._executemany_payloads([("db.t", "db", "t", ["a"], [[i] for i in range(2500)], False)])
    check("a failing chunk is reported and the loaded count is honest", rows_by["db.t"] == 1000 and errs and "boom" in errs[0][1])
    check("PhysColumn carries value_regex + glossary_term from the model",
          all(c.value_regex == "retail|corporate|institutional|government" for t in model().tables for c in t.columns if c.name == "customer_segment")
          and all(c.glossary_term for t in model().tables for c in t.columns))


if __name__ == "__main__":
    for fn in (t_regex_gen, t_declarations, t_coherence, t_time, t_keys, t_volume, t_hygiene, t_wiring):
        try:
            fn()
        except Exception as exc:  # a raising test is a NAMED failure, never an aborted run
            _failed += 1
            print(f"  ✗ {fn.__name__} raised {type(exc).__name__}: {exc}")
    print("\n" + "=" * 50)
    print(f"Semantic demo data: {_passed} passed, {_failed} failed")
    print("=" * 50)
    sys.exit(1 if _failed else 0)
