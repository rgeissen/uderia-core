#!/usr/bin/env python3
"""
Platform operations — the REST API described for agents (phase 1: descriptors, catalog, gate).

Contract: src/trusted_data_agent/api/operations.py. Four things, each with its gate here:

  §A  the descriptor and the catalog — on a synthetic app: what a descriptor records, how the
      catalog resolves it, and every malformed descriptor reported AND left out (fail closed)
  §B  the auth guards record the tier they enforce, so a descriptor can never offer a route
      below it
  §C  the real app — no problems; every served route is described or in the committed backlog,
      the backlog holds nothing stale, a finished namespace stays finished, the generated
      catalog doc is current, no descriptor still carries the drafting mark
  §D  the drafter — op ids, first-guess classification, argument extraction

Run: PYTHONPATH=src python3 test/test_platform_operations.py
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "maintenance"))
os.environ.setdefault("SECRET_KEY", "platform-operations-test")

from quart import Blueprint, Quart  # noqa: E402

from trusted_data_agent.api.operations import (  # noqa: E402
    Arg, build_catalog, operation, path_params,
)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


def app_with(*routes):
    """A throwaway app: each route is (rule, methods, handler)."""
    app = Quart(f"t{id(routes)}")
    bp = Blueprint(f"bp{id(routes)}", __name__)
    for rule, methods, fn in routes:
        bp.add_url_rule(rule, view_func=fn, methods=methods)
    app.register_blueprint(bp, url_prefix="/api")
    return app


def has(problems, needle):
    return any(needle in p for p in problems)


# ── §A the descriptor and the catalog ─────────────────────────────────────────
print("§A descriptor and catalog")


async def _list_things():
    return "ok"


described = operation("things.list", effect="read", scope="own", summary="List things",
                      query={"limit": Arg("integer", "the most to return"), "q": "a filter"})(_list_things)
check("the decorator returns the handler itself (metadata only — the route runs unchanged)",
      described is _list_things)
check("it records one descriptor", len(_list_things.__operations__) == 1)


async def _thing():
    return "ok"


operation("things.get", effect="read", scope="own", summary="One thing",
          path={"thing_id": "the thing's id"})(_thing)


async def _undescribed():
    return "ok"


cat = build_catalog(app_with(("/v1/things", ["GET"], _list_things),
                             ("/v1/things/<thing_id>", ["GET"], _thing),
                             ("/v1/other/<int:n>", ["POST"], _undescribed)))
check("no problems on well-formed descriptors", cat.problems == [], cat.problems)
check("two operations, one undescribed route", len(cat.operations) == 2 and len(cat.undescribed) == 1)
check("the undescribed route is named by method and full path",
      (cat.undescribed[0].method, cat.undescribed[0].path) == ("POST", "/api/v1/other/<int:n>"))
o = cat.by_op["things.get"]
check("the path is the served rule, with its prefix", o.path == "/api/v1/things/<thing_id>" and o.method == "GET")
check("path parameters come from the route, documented by the descriptor",
      list(o.path_params) == ["thing_id"] and o.path_params["thing_id"].doc == "the thing's id")
sch = cat.by_op["things.list"].input_schema()
check("the input schema types and places each argument",
      sch["properties"]["limit"] == {"type": "integer", "description": "the most to return", "x-in": "query"}
      and sch["properties"]["q"]["type"] == "string", sch)
check("an optional argument is not required", "required" not in sch)
check("a path parameter is always required", cat.by_op["things.get"].input_schema()["required"] == ["thing_id"])
check("an operation knows where its handler is", o.source.endswith(".py:" + str(_thing.__code__.co_firstlineno)), o.source)
check("path_params reads converters", path_params("/a/<int:n>/<path:p>/<id>") == {"n": "int", "p": "path", "id": "default"})


async def _multi():
    return "ok"


operation("multi.get", effect="read", scope="own", summary="Read", methods=("GET",))(_multi)
operation("multi.set", effect="write", scope="own", summary="Write", methods=("POST",))(_multi)
cat = build_catalog(app_with(("/v1/multi", ["GET", "POST"], _multi)))
check("stacked descriptors describe one method each",
      cat.problems == [] and {(x.op, x.method) for x in cat.operations} == {("multi.get", "GET"), ("multi.set", "POST")},
      cat.problems)


async def _never():
    return "ok"


async def _human():
    return "ok"


operation("x.stream", effect="never", scope="own", summary="A stream")(_never)
operation("x.sign", effect="human_only", scope="own", governs=True, summary="Sign")(_human)
cat = build_catalog(app_with(("/v1/x/stream", ["GET"], _never), ("/v1/x/sign", ["POST"], _human)))
check("`never` is not offered; `human_only` is listed so an agent can name what a person owes",
      [x.op for x in cat.offered()] == ["x.sign"])
check("…and neither is callable by an agent",
      not any(x.callable_by_agent for x in cat.operations))


def problems_of(rule, methods, *descs, guard=None):
    async def h():
        return "ok"
    fn = h
    for d in descs:
        fn = operation(*d[0], **d[1])(fn)
    if guard:
        fn.__auth_tier__ = guard
    c = build_catalog(app_with((rule, methods, fn)))
    return c.problems, c.operations


R = dict(effect="read", scope="own", summary="s")
cases = [
    ("an op id that is not dotted", ("/v1/a", ["GET"], (("flat",), R)), "not dotted"),
    ("an unknown effect", ("/v1/a", ["GET"], (("a.b",), {**R, "effect": "delete"})), "effect 'delete'"),
    ("an unknown scope", ("/v1/a", ["GET"], (("a.b",), {**R, "scope": "everyone"})), "scope 'everyone'"),
    ("an unknown tier", ("/v1/a", ["GET"], (("a.b",), {**R, "tier": "root"})), "tier 'root'"),
    ("an empty summary", ("/v1/a", ["GET"], (("a.b",), {**R, "summary": "  "})), "summary is empty"),
    ("governs on a read", ("/v1/a", ["GET"], (("a.b",), {**R, "governs": True})), "governs=True on a 'read'"),
    ("other users' data below admin", ("/v1/a", ["GET"], (("a.b",), {**R, "scope": "cross_user"})), "cross_user data offered"),
    ("an unknown argument type", ("/v1/a", ["GET"], (("a.b",), {**R, "query": {"n": Arg("int", "")}})), "type 'int'"),
    ("one argument in two places", ("/v1/a", ["POST"], (("a.b",), {**R, "query": {"n": "x"}, "body": {"n": "x"}})), "more than one place"),
    ("a dry_run that names nothing", ("/v1/a", ["POST"], (("a.b",), {**R, "effect": "write", "dry_run": "plan"})), "dry_run names 'plan'"),
    ("a dry_run on a read", ("/v1/a", ["POST"], (("a.b",), {**R, "query": {"plan": "x"}, "dry_run": "plan"})), "only a write has a plan"),
    ("a path parameter documented that the route lacks", ("/v1/a", ["GET"], (("a.b",), {**R, "path": {"id": "x"}})), "does not have"),
    ("a path parameter declared as a body argument", ("/v1/a/<id>", ["PUT"], (("a.b",), {**R, "effect": "write", "body": {"id": "x"}})), "is a path parameter"),
    ("an ambiguous descriptor on a multi-method handler", ("/v1/a", ["GET", "POST"], (("a.b",), R)), "say which with methods"),
    ("a method the route does not serve", ("/v1/a", ["GET"], (("a.b",), {**R, "methods": ("DELETE",)})), "does not serve"),
    ("a method described twice", ("/v1/a", ["GET"], (("a.b",), R), (("a.c",), R)), "described twice"),
    ("an empty summary field name", ("/v1/a", ["GET"], (("a.b",), {**R, "summary_fields": ("id", " ")})), "summary_fields must be"),
]
for label, (rule, methods, *descs), needle in cases:
    probs, ops = problems_of(rule, methods, *descs)
    check(f"reported: {label}", has(probs, needle), probs)
    if needle not in ("described twice",):
        check(f"left out (fail closed): {label}", ops == [], [x.op for x in ops])

probs, ops = problems_of("/v1/a", ["GET"], (("a.b",), R), guard="admin")
check("a route guarded for admin cannot be offered at user tier",
      has(probs, "the route demands 'admin'") and ops == [], probs)
probs, ops = problems_of("/v1/a", ["GET"], (("a.b",), {**R, "tier": "admin", "scope": "cross_user"}), guard="admin")
check("…and is offered at admin", probs == [] and len(ops) == 1, probs)


async def _one():
    return "ok"


async def _two():
    return "ok"


operation("dup.op", **R)(_one)
operation("dup.op", **R)(_two)
c = build_catalog(app_with(("/v1/one", ["GET"], _one), ("/v1/two", ["GET"], _two)))
check("an op id names one route — the second claim is reported and left out",
      has(c.problems, "already names") and [x.path for x in c.operations] == ["/api/v1/one"], c.problems)


# ── §B the auth guards record their tier ──────────────────────────────────────
print("§B auth guard tiers")
from trusted_data_agent.auth.middleware import optional_auth, require_admin, require_auth  # noqa: E402


async def _h(current_user):
    return "ok"


check("require_auth records 'user'", getattr(require_auth(_h), "__auth_tier__", None) == "user")
check("require_admin records 'admin'", getattr(require_admin(_h), "__auth_tier__", None) == "admin")
check("require_auth around require_admin keeps 'admin' (the stricter guard wins)",
      getattr(require_auth(require_admin(_h)), "__auth_tier__", None) == "admin")
check("optional_auth records nothing", getattr(optional_auth(_h), "__auth_tier__", None) is None)


# ── §C the real app ────────────────────────────────────────────────────────────
print("§C the served API")
import platform_operations as PO  # noqa: E402

logging.disable(logging.CRITICAL)
from trusted_data_agent.main import app as real_app  # noqa: E402
logging.disable(logging.NOTSET)
real = build_catalog(real_app)
check("no descriptor in the served API is malformed", real.problems == [], "\n".join(real.problems))

backlog = PO.read_backlog()
undescribed = {f"{r.method} {r.path}" for r in real.undescribed}
new = sorted(undescribed - set(backlog))
stale = sorted(set(backlog) - undescribed)
check("every served route is described or in the backlog (a new route must be described)", not new,
      "describe: " + ", ".join(new[:10]))
check("the backlog lists no route that is described or gone (regenerate it: backlog --write)", not stale,
      ", ".join(stale[:10]))
check("the backlog lists each route once", len(backlog) == len(set(backlog)))

# A namespace once fully described stays so — the backlog cannot quietly take a route back.
COMPLETE = ("/api/v1/profiles", "/api/v1/trust", "/api/v1/consumption")
for prefix in COMPLETE:
    left = sorted(r for r in undescribed if r.split(" ", 1)[1] == prefix or r.split(" ", 1)[1].startswith(prefix + "/"))
    check(f"{prefix} is fully described", not left, left)
check("the profiles namespace offers the agent lifecycle",
      {"profiles.create", "profiles.update", "profiles.test", "profiles.delete"} <= set(real.by_op))
check("the person's acts stay a person's: signing-adjacent profile acts are human_only",
      all(real.by_op[o].effect == "human_only" for o in ("profiles.set_default", "profiles.set_active_for_consumption")))
check("the agent list declares the fields that identify an agent",
      real.by_op["profiles.list"].summary_fields == ("id", "tag", "name", "profile_type"))
check("creating or changing an agent governs what an agent may do",
      real.by_op["profiles.create"].governs and real.by_op["profiles.update"].governs)

check("the generated catalog doc is current (regenerate: docs --write)",
      PO.DOCS.exists() and PO.DOCS.read_text(encoding="utf-8") == PO.render_docs(real))

# The roles manual (docs/manuals/PLATFORM_ROLES.md): its per-role sections (with each role's
# examples and boundaries, held per role by test_platform_roles.py §I) and its playbook appendix
# are generated from what the platform enforces; every operation its hand-written parts name
# must exist.
import fnmatch as _fn  # noqa: E402
import re as _re  # noqa: E402
_manual = PO.MANUAL.read_text(encoding="utf-8") if PO.MANUAL.exists() else ""
check("the roles manual's generated parts are current (regenerate: manual --write)",
      bool(_manual) and PO.render_manual(_manual, real) == _manual)
_links = _re.findall(r"\]\(#([a-z0-9-]+)\)", _manual)
_ids = set(_re.findall(r'<a id="([a-z0-9-]+)"></a>', _manual))
check("every link in the roles manual (its contents above all) lands on an anchor in it",
      bool(_links) and all(l in _ids for l in _links), sorted({l for l in _links if l not in _ids})[:8])
_toc = _manual.split("<!-- generated:toc -->", 1)[-1].split("<!-- /generated:toc -->", 1)[0]
check("the contents list every role, every hand-written section and every playbook",
      all(f"(#role-{r})" in _toc for r in ("guide", "agent-builder", "knowledge-curator", "automation-builder",
                                            "quality-analyst", "insight-builder"))
      and all(f"(#{a})" in _toc for a in ("idea", "giving-roles", "roles", "appendix-playbooks", "appendix-operations", "about"))
      and _toc.count("(#playbook-") >= 20)
_s3 = _manual.split('<a id="roles"></a>', 1)[-1].split('<a id="appendix-playbooks"></a>', 1)[0]
check("the role sections speak to people — no operation ids there (they are in Appendix B)",
      bool(_s3) and not _re.search(r"`[a-z_]+\.[a-z_]+`", _s3), _re.findall(r"`[a-z_]+\.[a-z_]+`", _s3)[:5])
check("each active role links to its operations in Appendix B, and each has its section there",
      all(f"(#ops-{r})" in _manual and f'<a id="ops-{r}"></a>' in _manual
          for r in ("guide", "agent-builder", "knowledge-curator", "automation-builder", "quality-analyst", "insight-builder")))
check("the roles manual carries no tables — a table of sentences overflows a narrow reading column",
      not _re.search(r"^\|.*\|\s*$", _manual, flags=_re.M))
_hand = _re.sub(r"<!-- generated:([a-z-]+) -->.*?<!-- /generated:\1 -->", "", _manual, flags=_re.S)
_ops = {o.op for o in real.operations}
_dangling = sorted(x for x in {y.replace("…", "*") for y in _re.findall(r"`([a-z_]+\.[a-z_*…]+)`", _hand)}
                   if not any(_fn.fnmatchcase(o, x) for o in _ops))
check("every operation the manual names by hand exists in the catalog", not _dangling, _dangling)

drafts = []
for f in sorted((ROOT / "src" / "trusted_data_agent" / "api").glob("*.py")):
    for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if "@operation(" in line and PO.DRAFT_MARK in line:
            drafts.append(f"{f.name}:{n}")
check("no descriptor still carries the drafting mark (review it, then remove the mark)", not drafts, drafts)


# ── §D the drafter ─────────────────────────────────────────────────────────────
print("§D the drafter")
taken = set()
ids = [PO.draft_op_id(m, p, taken) for m, p in (
    ("GET", "/api/v1/widgets"), ("POST", "/api/v1/widgets"), ("GET", "/api/v1/widgets/<id>"),
    ("PUT", "/api/v1/widgets/<id>"), ("DELETE", "/api/v1/widgets/<id>"),
    ("POST", "/api/v1/widgets/<id>/archive"), ("GET", "/api/v1/widgets/<id>/archive"))]
check("op ids follow the namespace and the verb",
      ids == ["widgets.list", "widgets.create", "widgets.get", "widgets.update", "widgets.delete",
              "widgets.archive", "widgets.archive_get"], ids)
check("a GET reads", PO.classify("GET", "/api/v1/widgets", "user")[0] == "read")
check("a stream is never an operation", PO.classify("GET", "/api/v1/widgets/stream", "user")[0] == "never")
check("a sign-off is a person's act", PO.classify("POST", "/api/v1/trust/agent/<id>/sign-off", "user")[0] == "human_only")
check("a POST that queries reads", PO.classify("POST", "/api/v1/data-products/<p>/query", "user")[0] == "read")
check("an admin GET over users is cross_user at admin",
      PO.classify("GET", "/api/v1/admin/users", "admin")[1:3] == ("cross_user", "admin"))
q, b = PO.draft_args(
    'n = request.args.get("limit", type=int)\nd = await request.get_json()\n'
    'x = d.get("name")\ny = d["kind"]\nd["id"] = 1\nz = d.get("flag", False)\n', set())
check("query and body arguments are read from the handler",
      q == {"limit": ("integer", False)} and b.get("name") == ("string", False)
      and b.get("kind") == ("string", True) and b.get("flag") == ("boolean", False), (q, b))
check("a field the handler fills in is not demanded of the caller", "id" not in b, b)

print(f"\n{'ALL PASS' if not failed else 'FAILED'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
