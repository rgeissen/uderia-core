#!/usr/bin/env python3
"""Build and verify the `teradata-patterns` progressive skill.

The library sections under `skills/builtin/teradata-patterns/library/` are the source of
truth. Every ```sql block in a section is EXECUTED against a live Teradata before it ships:
`verify` binds each `{{parameter}}` to the example in the section's Parameters table, runs
the statements against a small fixture schema, and records the outcome per statement in
`verification.json`. A statement whose text changed since it was last executed has no
record, and the CI gate (`test/test_teradata_patterns.py`) refuses the library.

Commands
    import   <loom_checkout> <category/name>...   render Loom patterns to draft sections
    fixture                                       (re)create the fixture schema on the target
    verify   [anchor ...] [--write]               execute the statements, report, record
    build                                         regenerate the index and the root list

The target system comes from the environment — never from the repo:
    TDP_HOST, TDP_USER, TDP_PASSWORD   the Teradata to verify on
    TDP_DATABASE                       the scratch database (default: the user's own)

Sections were first derived from teradata-labs/loom `patterns/teradata/` (Apache-2.0); see
NOTICE.md in the skill directory. As imported, about a quarter of the statements did not run
(measured 2026-10-01), which is why nothing here is published unexecuted.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "builtin" / "teradata-patterns"
LIBRARY = SKILL_DIR / "library"
VERIFICATION = SKILL_DIR / "verification.json"
SOURCES = SKILL_DIR / "sources.json"
ROOT_MD = SKILL_DIR / "teradata-patterns.md"
MANIFEST = SKILL_DIR / "skill.json"


def _load_execution_record():
    # Loaded from its file, not through the package: the tool stays light (no app import).
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_tdp_execution_record", ROOT / "src" / "trusted_data_agent" / "skills" / "execution_record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_execution_record = _load_execution_record()

#: The database name every section uses in its examples. `verify` maps it to the scratch
#: database of the target system.
EXAMPLE_DB = "retail_demo"
FIXTURE_TABLES = ("customers", "orders", "order_items", "products", "events", "stores", "regions", "product_vectors")
INDEX_BEGIN, INDEX_END = "<!-- index:begin -->", "<!-- index:end -->"


# ── sections ──────────────────────────────────────────────────────────────────────────────

def anchor_of(path: Path) -> str:
    return path.relative_to(LIBRARY).with_suffix("").as_posix()


def section_files() -> list:
    return sorted(LIBRARY.rglob("*.md")) if LIBRARY.exists() else []


def title_and_summary(text: str) -> tuple:
    """The `# ` heading and the first prose line under it."""
    lines = text.splitlines()
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "")
    summary = ""
    seen_title = False
    for ln in lines:
        if ln.startswith("# "):
            seen_title = True
            continue
        if seen_title and ln.strip():
            summary = ln.strip()
            break
    return title, summary


def parameters(text: str) -> dict:
    """`{name: example}` from the section's Parameters table."""
    out = {}
    m = re.search(r"(?ms)^## Parameters\s*\n(.*?)(?=^## |\Z)", text)
    if not m:
        return out
    for row in m.group(1).splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0].startswith("`") and cells[-1].startswith("`"):
            out[cells[0].strip("`")] = cells[-1][1:-1] if cells[-1].endswith("`") else cells[-1].strip("`")
    return out


# One definition of "a statement", shared with the platform (signature + sign-off check).
statements = _execution_record.statements
statement_hash = _execution_record.statement_hash


def called_identifiers(sql: str) -> set:
    """Upper-cased identifiers written as calls (``NAME(``) in code — never in quotes or comments.
    The ones that exist in the system's function databases are what the statement requires."""
    sys.path.insert(0, str(ROOT / "src"))
    from trusted_data_agent.agent.sql_dialect import _sql_code_mask
    return {m.group(1).upper() for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", _sql_code_mask(sql))}


def function_objects(cur, names) -> set:
    """Which of ``names`` exist as function objects on the connected system — the same
    dictionary read the platform uses at runtime (``dbcatalog.teradata_types``)."""
    sys.path.insert(0, str(ROOT / "src"))
    from trusted_data_agent.dbcatalog.teradata_types import existing_functions_sql
    sql = existing_functions_sql(names)
    if not sql:
        return set()
    cur.execute(sql)
    return {str(r[0]).strip().upper() for r in cur.fetchall()}


def bind(sql: str, params: dict, database: str) -> str:
    def sub(m):
        key = m.group(1).strip()
        if key not in params:
            raise KeyError(key)
        return params[key]
    bound = re.sub(r"\{\{\s*([^}]+?)\s*\}\}", sub, sql)
    return re.sub(rf"\b{EXAMPLE_DB}\b", database, bound)


def split_statements(sql: str) -> list:
    """Split on semicolons outside quotes and comments; comment-only pieces are dropped."""
    out, buf, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in ("'", '"'):
            j = i + 1
            while j < n and not (sql[j] == c and not (j + 1 < n and sql[j + 1] == c)):
                j += 2 if sql[j] == c else 1
            buf.append(sql[i:j + 1]); i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i); j = n if j == -1 else j
            buf.append(sql[i:j]); i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2); j = n if j == -1 else j + 2
            buf.append(sql[i:j]); i = j
        elif c == ";":
            out.append("".join(buf)); buf = []; i += 1
        else:
            buf.append(c); i += 1
    out.append("".join(buf))
    keep = []
    for s in out:
        body = re.sub(r"/\*.*?\*/", "", re.sub(r"--[^\n]*", "", s), flags=re.S)
        if body.strip():
            keep.append(s.strip())
    return keep


# ── import: a Loom pattern → a draft section ──────────────────────────────────────────────

def render_section(pattern: dict) -> str:
    desc = " ".join(str(pattern.get("description") or "").split())
    first, _, rest = desc.partition(". ")
    out = [f"# {pattern.get('title') or pattern.get('name')}", "", (first + ".") if rest else first, ""]
    if rest:
        out += [rest, ""]
    uses = [u for u in (pattern.get("use_cases") or []) if isinstance(u, str)]
    if uses:
        out += ["**Use for:** " + "; ".join(u.rstrip(".") for u in uses) + ".", ""]
    out += ["## Parameters", "", "| Parameter | Meaning | Example |", "|---|---|---|"]
    for p in pattern.get("parameters") or []:
        out.append(f"| `{p.get('name')}` | {' '.join(str(p.get('description') or '').split())} | `{p.get('example')}` |")
    out += ["", "## Statements", ""]
    for name, t in (pattern.get("templates") or {}).items():
        if not isinstance(t, dict):
            continue
        body = (t.get("sql") or t.get("content") or "").rstrip()
        out += [f"### {name} — {' '.join(str(t.get('description') or '').split())}", "", "```sql", body, "```", ""]
    errors = [e for e in (pattern.get("common_errors") or []) if isinstance(e, dict)]
    if errors:
        out += ["## Common errors", ""]
        for e in errors:
            out.append(f"- **{e.get('error')}** — {str(e.get('cause') or '').strip()} Fix: {str(e.get('solution') or '').strip()}")
        out.append("")
    if pattern.get("best_practices"):
        out += ["## Practices", "", str(pattern["best_practices"]).strip(), ""]
    return "\n".join(out).rstrip() + "\n"


def cmd_import(args) -> int:
    import subprocess
    import yaml
    loom = Path(args.loom).resolve()
    commit = subprocess.run(["git", "-C", str(loom), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    sources = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    for anchor in args.anchors:
        src = loom / "patterns" / "teradata" / (anchor + ".yaml")
        dest = LIBRARY / (anchor + ".md")
        if dest.exists() and not args.force:
            print(f"skip   {anchor}: the section exists (it is the source of truth now; --force to overwrite)")
            continue
        raw = src.read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_section(yaml.safe_load(raw)), encoding="utf-8")
        sources[anchor] = {"upstream": f"patterns/teradata/{anchor}.yaml", "commit": commit,
                           "sha256": hashlib.sha256(raw).hexdigest()}
        print(f"import {anchor}")
    SOURCES.write_text(json.dumps(dict(sorted(sources.items())), indent=2) + "\n")
    return 0


# ── the fixture schema ────────────────────────────────────────────────────────────────────

FIXTURE_DDL = {
    "customers": """(customer_id INTEGER NOT NULL, first_name VARCHAR(50), last_name VARCHAR(50),
        email VARCHAR(100), phone VARCHAR(30), city VARCHAR(50), state CHAR(2), age INTEGER,
        income DECIMAL(12,2), signup_date DATE, segment VARCHAR(20)) UNIQUE PRIMARY INDEX (customer_id)""",
    "products": """(product_id INTEGER NOT NULL, product_name VARCHAR(100), category VARCHAR(40),
        price DECIMAL(10,2)) UNIQUE PRIMARY INDEX (product_id)""",
    "orders": """(order_id INTEGER NOT NULL, customer_id INTEGER, order_date DATE, order_ts TIMESTAMP(0),
        status VARCHAR(20), amount DECIMAL(12,2), channel VARCHAR(20), order_date_text VARCHAR(10))
        UNIQUE PRIMARY INDEX (order_id)""",
    "order_items": """(order_id INTEGER NOT NULL, line_no INTEGER NOT NULL, product_id INTEGER,
        quantity INTEGER, unit_price DECIMAL(10,2)) PRIMARY INDEX (order_id)""",
    "events": """(event_id INTEGER NOT NULL, customer_id INTEGER, event_ts TIMESTAMP(0),
        event_type VARCHAR(30), page VARCHAR(50)) PRIMARY INDEX (customer_id)""",
    "stores": """(store_id INTEGER NOT NULL, store_name VARCHAR(50), city VARCHAR(50),
        location ST_Geometry) UNIQUE PRIMARY INDEX (store_id)""",
    "regions": """(region_id INTEGER NOT NULL, region_name VARCHAR(50),
        boundary ST_Geometry) UNIQUE PRIMARY INDEX (region_id)""",
    "product_vectors": """(product_id INTEGER NOT NULL, category VARCHAR(40), e1 FLOAT, e2 FLOAT,
        e3 FLOAT, e4 FLOAT, e5 FLOAT, e6 FLOAT) UNIQUE PRIMARY INDEX (product_id)""",
}


def fixture_rows() -> dict:
    """Deterministic rows. Small on purpose, with the irregularities the patterns look for:
    missing values, duplicate emails, a few outliers, one skewed key."""
    rnd = random.Random(7)
    first = ["Ana", "Ben", "Cara", "Dan", "Eva", "Finn", "Gia", "Hugo", "Ida", "Jon", "Kim", "Leo"]
    last = ["Abel", "Baker", "Cruz", "Diaz", "Evans", "Fox", "Gray", "Hill", "Ito", "Jones", "Kaur", "Lang"]
    cities = [("Austin", "TX"), ("Boston", "MA"), ("Chicago", "IL"), ("Denver", "CO"), ("Miami", "FL"), ("Seattle", "WA")]
    day0 = datetime.date(2024, 1, 1)
    customers = []
    for i in range(1, 201):
        f, l = rnd.choice(first), rnd.choice(last)
        city, state = rnd.choice(cities)
        email = f"{f}.{l}{i}@example.com".lower()
        if i % 25 == 0:
            email = customers[i - 6][3]                       # a duplicate email
        if i % 17 == 0:
            email = None
        phone = None if i % 9 == 0 else f"555-{1000 + i:04d}"
        age = None if i % 31 == 0 else rnd.randint(18, 80)
        income = None if i % 13 == 0 else round(rnd.gauss(60000, 15000), 2)
        if i in (40, 120, 177):
            income = 950000.00                                 # outliers
        customers.append((i, f, l, email, phone, city, state, age, income,
                          day0 + datetime.timedelta(days=rnd.randint(0, 500)),
                          rnd.choice(["consumer", "consumer", "business", "premium"])))
    products = [(p, f"Product {p:02d}", rnd.choice(["apparel", "footwear", "equipment", "nutrition"]),
                 round(rnd.uniform(5, 250), 2)) for p in range(1, 41)]
    orders, items, events = [], [], []
    for o in range(1, 1001):
        cust = 7 if o % 10 == 0 else rnd.randint(1, 200)       # customer 7 is the skewed key
        d = day0 + datetime.timedelta(days=rnd.randint(0, 600))
        ts = datetime.datetime.combine(d, datetime.time(rnd.randint(0, 23), rnd.randint(0, 59), rnd.randint(0, 59)))
        n_lines = rnd.randint(1, 4)
        total = 0.0
        for ln in range(1, n_lines + 1):
            p = rnd.choice(products)
            q = rnd.randint(1, 5)
            total += q * p[3]
            items.append((o, ln, p[0], q, p[3]))
        orders.append((o, cust, d, ts, rnd.choice(["completed", "completed", "completed", "shipped", "cancelled", "returned"]),
                       round(total, 2), rnd.choice(["web", "store", "mobile"]), d.isoformat()))
    eid = 0
    for cust in range(1, 201):
        t = datetime.datetime(2025, 1, 1, 8, 0, 0) + datetime.timedelta(hours=rnd.randint(0, 2000))
        for _ in range(rnd.randint(5, 25)):
            eid += 1
            t += datetime.timedelta(minutes=rnd.choice([1, 2, 3, 5, 8, 90, 600]))
            kind = rnd.choice(["view", "view", "view", "search", "add_to_cart", "checkout", "purchase"])
            events.append((eid, cust, t, kind, rnd.choice(["home", "catalog", "product", "cart", "checkout"])))
    out = {"customers": customers, "products": products, "orders": orders, "order_items": items, "events": events}
    out.update(_spatial_and_vector_rows(products))
    return out


def _spatial_and_vector_rows(products) -> dict:
    """Stores as longitude/latitude points (plain WKT, SRID 0) at the customer cities, regions as
    longitude/latitude boxes: Central and Southwest share the edge x = -103, Gulf overlaps Central,
    Pacific holds no store and Seattle lies in no region. Product vectors cluster by category.
    Its own random stream, so the rows above stay exactly as they were."""
    sites = [("Austin", -97.7431, 30.2672), ("Boston", -71.0589, 42.3601), ("Chicago", -87.6298, 41.8781),
             ("Denver", -104.9903, 39.7392), ("Miami", -80.1918, 25.7617), ("Seattle", -122.3321, 47.6062)]
    stores, sid = [], 0
    for city, x, y in sites:
        for k, (dx, dy) in enumerate(((0.0, 0.0), (0.0412, -0.0275))):
            sid += 1
            stores.append((sid, f"{city} {'Downtown' if k == 0 else 'East'}", city, f"POINT ({x + dx:.4f} {y + dy:.4f})"))
    stores.append((13, "Roadside Outlet", None, "POINT (-100.0000 35.0000)"))
    regions = [
        (1, "Central", "POLYGON ((-103 29, -86 29, -86 43, -103 43, -103 29))"),
        (2, "Southwest", "POLYGON ((-115 29, -103 29, -103 43, -115 43, -115 29))"),
        (3, "Northeast", "POLYGON ((-80 38, -66 38, -66 47, -80 47, -80 38))"),
        (4, "Gulf", "POLYGON ((-98 24, -80 24, -80 31, -98 31, -98 24))"),
        (5, "Pacific", "POLYGON ((-125 32, -117 32, -117 38, -125 38, -125 32))"),
    ]
    rnd = random.Random(11)
    centers = {"apparel": (0.9, 0.1, 0.1, 0.0, 0.2, 0.1), "footwear": (0.7, 0.6, 0.1, 0.1, 0.1, 0.0),
               "equipment": (0.1, 0.2, 0.9, 0.5, 0.0, 0.1), "nutrition": (0.0, 0.1, 0.1, 0.2, 0.9, 0.6)}
    vectors = [(pid, cat, *[round(x + rnd.gauss(0, 0.08), 4) for x in centers[cat]])
               for pid, _name, cat, _price in products]
    return {"stores": stores, "regions": regions, "product_vectors": vectors}


def connect():
    import teradatasql
    missing = [k for k in ("TDP_HOST", "TDP_USER", "TDP_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise SystemExit("set " + ", ".join(missing) + " to name the Teradata to verify on")
    return teradatasql.connect(host=os.environ["TDP_HOST"], user=os.environ["TDP_USER"], password=os.environ["TDP_PASSWORD"])


def target_database(cur) -> str:
    if os.environ.get("TDP_DATABASE"):
        return os.environ["TDP_DATABASE"]
    cur.execute("SELECT USER")
    return str(cur.fetchall()[0][0]).strip()


def _drop(cur, kind: str, name: str) -> None:
    try:
        cur.execute(f"DROP {kind} {name}")
    except Exception as exc:  # noqa: BLE001 — absent is fine
        if "3807" not in str(exc) and "3802" not in str(exc) and "5495" not in str(exc):
            raise


def cmd_fixture(args) -> int:
    con = connect()
    cur = con.cursor()
    db = target_database(cur)
    rows = fixture_rows()
    for t in FIXTURE_TABLES:
        for name in (f"{db}.{t}", f"{db}.{t}__src"):
            _drop(cur, "TABLE", name)
        cur.execute(f"CREATE MULTISET TABLE {db}.{t}__src {FIXTURE_DDL[t]}")
        marks = ", ".join("?" for _ in rows[t][0])
        cur.executemany(f"INSERT INTO {db}.{t}__src VALUES ({marks})", [list(r) for r in rows[t]])
        cur.execute(f"CREATE MULTISET TABLE {db}.{t} AS {db}.{t}__src WITH DATA")
        print(f"fixture {db}.{t}: {len(rows[t])} rows")
    con.close()
    return 0


# ── verify: execute every statement ───────────────────────────────────────────────────────

_CREATES = re.compile(r"(?is)^\s*(?:CREATE|REPLACE)\s+(?:(?:MULTISET|SET|VOLATILE|GLOBAL\s+TEMPORARY)\s+)*"
                      r"(TABLE|VIEW|MACRO|PROCEDURE|FUNCTION)\s+([\w.\"]+)")
_MUTATES = re.compile(r"(?is)^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|DELETE|MERGE\s+INTO|DROP\s+TABLE|ALTER\s+TABLE)\s+([\w.\"]+)")


def _error(exc: Exception) -> str:
    m = re.search(r"\[Error (\d+)\] ([^\n]*)", str(exc))
    return f"{m.group(1)} {m.group(2)[:200]}" if m else str(exc).splitlines()[0][:200]


def verify_section(path: Path, system: str) -> dict:
    text = path.read_text(encoding="utf-8")
    params = parameters(text)
    con = connect()
    cur = con.cursor()
    db = target_database(cur)
    fixtures = {f"{db}.{t}".lower() for t in FIXTURE_TABLES} | {t for t in FIXTURE_TABLES}
    created, touched, results = [], set(), []
    stmts = statements(text)
    calls = {name: called_identifiers(sql) for name, sql in stmts}
    known = function_objects(cur, set().union(*calls.values()) if calls else set())
    for name, sql in stmts:
        rec = {"name": name, "sha256": statement_hash(sql), "status": "ok", "executed": 0,
               "requires": sorted(calls[name] & known)}
        try:
            bound = bind(sql, params, db)
        except KeyError as exc:
            rec.update(status="failed", error=f"unbound parameter {exc}")
            results.append(rec)
            continue
        for stmt in split_statements(bound):
            body = re.sub(r"/\*.*?\*/", "", re.sub(r"--[^\n]*", "", stmt), flags=re.S).strip()
            c = _CREATES.match(body)
            if c:
                obj = c.group(2).strip('"')
                if obj.lower() in fixtures:
                    rec.update(status="failed", error=f"the statement would replace the fixture table {obj}")
                    break
                _drop(cur, c.group(1).upper(), obj)
                created.append((c.group(1).upper(), obj))
            m = _MUTATES.match(body)
            if m and m.group(1).strip('"').lower() in fixtures:
                touched.add(m.group(1).strip('"').lower().split(".")[-1])
            try:
                cur.execute(stmt)
                try:
                    rows = cur.fetchall()
                    rec["rows"] = rec.get("rows", 0) + len(rows)
                except Exception:  # noqa: BLE001 — a statement with no result set
                    pass
                rec["executed"] += 1
            except Exception as exc:  # noqa: BLE001
                rec.update(status="failed", error=_error(exc), statement=" ".join(body.split())[:160])
                break
        results.append(rec)
    for kind, obj in reversed(created):
        try:
            _drop(cur, kind, obj)
        except Exception:  # noqa: BLE001
            pass
    for t in sorted(touched):                      # a section that changed a fixture table restores it
        _drop(cur, "TABLE", f"{db}.{t}")
        cur.execute(f"CREATE MULTISET TABLE {db}.{t} AS {db}.{t}__src WITH DATA")
    con.close()
    return {"system": system, "verified_on": datetime.date.today().isoformat(), "statements": results}


def cmd_verify(args) -> int:
    con = connect()
    cur = con.cursor()
    cur.execute("SELECT InfoData FROM DBC.DBCInfoV WHERE InfoKey = 'VERSION'")
    system = f"Teradata {str(cur.fetchall()[0][0]).strip()}"
    con.close()
    record = json.loads(VERIFICATION.read_text()) if VERIFICATION.exists() else {"sections": {}}
    wanted = set(args.anchors)
    failed = 0
    for path in section_files():
        anchor = anchor_of(path)
        if wanted and anchor not in wanted:
            continue
        res = verify_section(path, system)
        bad = [s for s in res["statements"] if s["status"] != "ok"]
        failed += len(bad)
        print(f"{'FAIL' if bad else 'ok  '} {anchor}: {len(res['statements']) - len(bad)}/{len(res['statements'])} statements ran")
        for s in bad:
            print(f"       {s['name']}: {s['error']}")
            if s.get("statement"):
                print(f"         in: {s['statement']}")
        if args.write:
            res["statements"] = [{k: v for k, v in s.items() if k in ("name", "sha256", "status", "executed", "rows", "requires")}
                                 for s in res["statements"] if s["status"] == "ok"]
            record["sections"][anchor] = res
    if args.write:
        record["sections"] = dict(sorted(record["sections"].items()))
        VERIFICATION.write_text(json.dumps(record, indent=1) + "\n")
    print(f"\n{system}: {failed} statement(s) did not run")
    return 1 if failed else 0


# ── build: index, root list ───────────────────────────────────────────────────────

def unverified(path: Path, record: dict) -> list:
    """Names of the statements in `path` with no `ok` record for their current text."""
    done = {s["sha256"] for s in (record.get("sections", {}).get(anchor_of(path), {}).get("statements") or [])
            if s.get("status") == "ok"}
    return [name for name, sql in statements(path.read_text(encoding="utf-8")) if statement_hash(sql) not in done]


def build_index(record: dict = None) -> list:
    index = []
    for path in section_files():
        text = path.read_text(encoding="utf-8")
        title, summary = title_and_summary(text)
        entry = {"anchor": anchor_of(path), "title": title, "summary": summary,
                 "file": "library/" + path.relative_to(LIBRARY).as_posix(),
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if record is not None:
            recs = (record.get("sections", {}).get(anchor_of(path), {}).get("statements") or [])
            entry["statements"] = len(statements(text))
            req = {s["name"]: s["requires"] for s in recs if s.get("requires")}
            if req:
                entry["requires"] = req
            executed = _execution_record.executed_entry(record.get("sections", {}).get(anchor_of(path)))
            if executed:
                entry["executed"] = executed
        index.append(entry)
    return index


def render_root_index(index: list) -> str:
    lines, group = [], None
    for e in index:
        cat = e["anchor"].split("/")[0]
        if cat != group:
            lines += ["", f"**{cat}**"] if lines else [f"**{cat}**"]
            group = cat
        # Titles only: the root page is injected on every turn, so it grows by one short line
        # per section. The one-line summaries are served on demand by `list_sections`.
        lines.append(f"- `{e['anchor']}` — {e['title']}")
    return "\n".join(lines)


def cmd_build(args) -> int:
    record = json.loads(VERIFICATION.read_text()) if VERIFICATION.exists() else {"sections": {}}
    pending = {anchor_of(p): unverified(p, record) for p in section_files()}
    pending = {a: n for a, n in pending.items() if n}
    if pending:
        for a, names in pending.items():
            print(f"not executed since its last edit — {a}: {', '.join(names)}")
        print("run `verify --write` against a Teradata first; nothing was built")
        return 1
    index = build_index(record)
    manifest = json.loads(MANIFEST.read_text())
    manifest.setdefault("uderia", {})["progressive"] = {"enabled": True, "index": index}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    root = ROOT_MD.read_text(encoding="utf-8")
    a, b = root.index(INDEX_BEGIN) + len(INDEX_BEGIN), root.index(INDEX_END)
    ROOT_MD.write_text(root[:a] + "\n" + render_root_index(index) + "\n" + root[b:], encoding="utf-8")
    print(f"built: {len(index)} sections")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import"); p.add_argument("loom"); p.add_argument("anchors", nargs="+"); p.add_argument("--force", action="store_true")
    sub.add_parser("fixture")
    p = sub.add_parser("verify"); p.add_argument("anchors", nargs="*"); p.add_argument("--write", action="store_true")
    sub.add_parser("build")
    args = ap.parse_args()
    return {"import": cmd_import, "fixture": cmd_fixture, "verify": cmd_verify, "build": cmd_build}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
