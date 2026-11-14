#!/usr/bin/env python3
"""
TDA_Platform — an agent finds and calls the described API (platform operations, phase 2: reads).

Contract: src/trusted_data_agent/core/platform_operations.py (the policy, find, call) and
components/builtin/platform/ (the tool). Gates:

  §A  the one policy — from the descriptor alone: never unlisted, above-tier unlisted, a
      person's act listed and refused, writes and other users' data listed and not offered
  §B  find — ranks by rare shared words and by meaning, never lists what may not be seen
  §C  arguments — unknown and missing named, types coerced, enums held, the URL built safely
  §D  the answer — projected to `fields`, bounded with a note that says what was cut
  §E  call — the REAL route runs in-process, as the user (an internal token for that user),
      every refusal a sentence, never an exception
  §F  the component — off for every agent until enabled, refuses a call where it is not,
      headless, decodes JSON-string arguments, has its companion skill
  §G  wiring — the served app is registered for dispatch; find over the real catalog
  §H  the call's token — good for one request (method, decoded path, a minute): refused on any
      other route or method, outside a request, expired or forged; never cached by the
      middleware; end to end through the real app as a real user

Run: PYTHONPATH=src python3 test/test_platform_tool.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "platform-tool-test")

from quart import Blueprint, Quart, jsonify, request  # noqa: E402

from trusted_data_agent.api.operations import Arg, build_catalog, operation  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
from trusted_data_agent.agent import tool_semantics as TS  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


# Words only, unless a test injects meaning (no model in this suite).
_real_similarity = TS.similarity
TS.similarity = lambda *a, **k: None

# ── a synthetic app: one route per policy case ─────────────────────────────────
bp = Blueprint("t", __name__)
seen = {}


def _who():
    import jwt
    from trusted_data_agent.auth.security import JWT_SECRET_KEY, JWT_ALGORITHM
    tok = request.headers.get("Authorization", "")[7:]
    return jwt.decode(tok, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


@bp.route("/v1/widgets", methods=["GET"])
@operation("widgets.list", effect="read", scope="own", summary="List the caller's widgets",
           query={"limit": Arg("integer", "the most to return"), "colour": Arg("string", "only this colour", enum=("red", "blue")),
                  "archived": Arg("boolean", "include archived ones")})
async def _w_list():
    seen["list"] = {"args": request.args.to_dict(flat=False), "who": _who(), "op": request.headers.get("X-Uderia-Operation")}
    n = int(request.args.get("limit", 3))
    return jsonify({"status": "success", "widgets": [{"id": f"w{i}", "name": f"Widget {i}", "colour": "red",
                                                      "detail": {"a": 1, "b": [1, 2, 3]}} for i in range(n)]})


@bp.route("/v1/widgets/<path:widget_path>/inspect", methods=["POST"])
@operation("widgets.inspect", effect="read", scope="own", summary="Inspect a widget's internals (nothing changes)",
           path={"widget_path": "where the widget lives"}, body={"depth": Arg("integer", "how deep", required=True)})
async def _w_inspect(widget_path):
    seen["inspect"] = {"path": widget_path, "body": await request.get_json(), "raw_path": request.path}
    return jsonify({"widget": widget_path, "depth": (await request.get_json())["depth"]})


@bp.route("/v1/widgets/<widget_id>", methods=["GET"])
@operation("widgets.get", effect="read", scope="own", summary="One widget", path={"widget_id": "its id"})
async def _w_get(widget_id):
    return jsonify({"error": "not found"}), 404


@bp.route("/v1/widgets", methods=["POST"])
@operation("widgets.create", effect="write", scope="own", summary="Create a widget")
async def _w_create():
    seen["create"] = True
    return jsonify({}), 201


@bp.route("/v1/widgets/<widget_id>/sign-off", methods=["POST"])
@operation("widgets.sign_off", effect="human_only", scope="own", governs=True, summary="Sign a widget off",
           path={"widget_id": "its id"})
async def _w_sign(widget_id):
    seen["sign"] = True
    return jsonify({}), 200


@bp.route("/v1/widgets/stream", methods=["GET"])
@operation("widgets.stream", effect="never", scope="own", summary="The widget event stream")
async def _w_stream():
    return "stream"


@bp.route("/v1/admin/widgets", methods=["GET"])
@operation("admin_widgets.list", effect="read", scope="instance", tier="admin", summary="Every widget setting of the deployment")
async def _w_admin():
    seen["admin"] = True
    return jsonify({"settings": {}})


@bp.route("/v1/admin/widget-owners", methods=["GET"])
@operation("admin_widgets.owners", effect="read", scope="cross_user", tier="admin", summary="Every user's widgets")
async def _w_owners():
    seen["owners"] = True
    return jsonify({})


@bp.route("/v1/widgets/huge", methods=["GET"])
@operation("widgets.huge", effect="read", scope="own", summary="An enormous widget report")
async def _w_huge():
    return jsonify({"rows": [{"id": i, "name": f"row {i}", "blob": "x" * 400, "nested": {"k": list(range(30))}}
                             for i in range(200)]})


@bp.route("/v1/widgets/catalog", methods=["GET"])
@operation("widgets.catalog", effect="read", scope="own", summary="Every widget with its full specification",
           summary_fields=("id", "name"))
async def _w_catalog():
    n = int(request.args.get("n", 200)) if request.args.get("n") else 200
    return jsonify({"default_id": "w3", "ids": [f"w{i}" for i in range(n)],
                    "widgets": [{"id": f"w{i}", "name": f"row {i}", "blob": "x" * 400, "nested": {"k": list(range(30))}}
                                for i in range(n)]})


@bp.route("/v1/agents/<profile_id>/things", methods=["GET"])
@operation("agents.things", effect="read", scope="own", summary="An agent's things", path={"profile_id": "the agent's id"})
async def _a_things(profile_id):
    seen["things"] = profile_id
    return jsonify({"profile_id": profile_id, "things": [{"id": "t1"}]})


app = Quart("platform_tool_test")
app.register_blueprint(bp, url_prefix="/api")
cat = build_catalog(app)
check("the synthetic catalog is well-formed", cat.problems == [], cat.problems)
by = cat.by_op

# ── §A the one policy ───────────────────────────────────────────────────────────
print("§A the one policy")
check("a read is callable", P.availability(by["widgets.list"]) == (True, ""))
check("a POST read is callable", P.availability(by["widgets.inspect"])[0])
ok, why = P.availability(by["widgets.sign_off"])
check("a person's act is not callable, and says so", not ok and "person's act" in why, why)
check("a write is callable in principle — the agent's write mode decides", P.availability(by["widgets.create"]) == (True, ""))
d = P.describe(by["widgets.create"])
check("…with writes off (the default) it is listed and refused, naming the mode",
      d["callable"] is False and "writes are off" in d["why_not"], d)
ok, why = P.availability(by["admin_widgets.owners"])
check("other users' data is not offered yet, and says so", not ok and "other users" in why, why)
check("an instance read is callable at its tier", P.availability(by["admin_widgets.list"])[0])
listed_user = {o.op for o in P._listed(cat, "user")}
check("`never` is never listed", "widgets.stream" not in listed_user and "widgets.stream" not in {o.op for o in P._listed(cat, "admin")})
check("an admin operation is not listed to a user", "admin_widgets.list" not in listed_user)
check("…and is listed to an admin", "admin_widgets.list" in {o.op for o in P._listed(cat, "admin")})
check("no tier (no active user) lists nothing", P._listed(cat, None) == [])
check("a developer does not reach admin", not P._reaches("developer", by["admin_widgets.list"]))
d = P.describe(by["widgets.inspect"])
check("describe names every argument and marks the required ones",
      d["args"] == {"widget_path": "required: string — where the widget lives", "depth": "required: integer — how deep"}, d)
d = P.describe(by["widgets.sign_off"])
check("describe carries why it cannot be called", d["callable"] is False and "person's act" in d["why_not"], d)
check("describe writes an enum's values", "(red|blue)" in P.describe(by["widgets.list"])["args"]["colour"])

# ── §B find ─────────────────────────────────────────────────────────────────────
print("§B find")
r = P.find("list my widgets", "user", cat=cat)
check("the operation sharing the most specific words ranks first", r["found"][0]["op"] == "widgets.list", r)
check("find reports how many it searched", r["searched"] == len(listed_user))
r = P.find("sign off the widget", "user", cat=cat)
check("a person's act is found — so the agent can name it", r["found"][0]["op"] == "widgets.sign_off" and r["found"][0]["callable"] is False, r)
r = P.find("widget event stream", "admin", cat=cat)
check("find never returns `never`", all(f["op"] != "widgets.stream" for f in r["found"]), r)
r = P.find("deployment settings", "user", cat=cat)
check("find never returns an operation above the caller's tier", all(f["op"] != "admin_widgets.list" for f in r["found"]), r)
check("…which an admin finds", P.find("deployment settings", "admin", cat=cat)["found"][0]["op"] == "admin_widgets.list")
r = P.find("the of and", "user", cat=cat)
check("a query of only stop words finds nothing (no guessing)", r["found"] == [], r)
r = P.find("zebra quantum", "user", cat=cat)
check("no match says so and suggests how to ask", r["found"] == [] and "no operation matches" in r.get("note", ""), r)
check("ties go to what can be called with nothing missing before what needs an id",
      [f["op"] for f in P.find("widget", "user", cat=cat)["found"]].index("widgets.list")
      < [f["op"] for f in P.find("widget", "user", cat=cat)["found"]].index("widgets.get"))
check("ties go to what can be called", [f["op"] for f in P.find("widget", "user", cat=cat)["found"]].index("widgets.sign_off")
      > [f["op"] for f in P.find("widget", "user", cat=cat)["found"]].index("widgets.list"))
check("find returns at most its limit", len(P.find("widget", "admin", cat=cat, limit=2)["found"]) == 2)


class _TS:
    FLOOR, RATIO, CAP = TS.FLOOR, TS.RATIO, TS.CAP


TS.similarity = lambda q, entries, **k: {e.name: (0.6 if e.name == "widgets.huge" else 0.05) for e in entries}
r = P.find("a big summary of everything", "user", cat=cat)
check("meaning finds what no word shares (the tool surface's semantic rule)", [f["op"] for f in r["found"]] == ["widgets.huge"], r)
TS.similarity = lambda q, entries, **k: {e.name: 0.1 for e in entries}
check("below the semantic floor nothing is added", P.find("a big summary of everything", "user", cat=cat)["found"] == [])
TS.similarity = lambda *a, **k: None

# ── §C arguments ────────────────────────────────────────────────────────────────
print("§C arguments")
placed, err = P.bind(by["widgets.list"], {"limit": "5", "archived": "true", "colour": "red"})
check("strings are coerced to the declared types", err is None and placed["query"] == {"limit": 5, "archived": True, "colour": "red"}, (placed, err))
_, err = P.bind(by["widgets.list"], {"colour": "green"})
check("an enum is held", err and "one of ['red', 'blue']" in err, err)
_, err = P.bind(by["widgets.list"], {"limit": "many"})
check("a wrong type is named", err and "'limit' must be of type integer" in err, err)
_, err = P.bind(by["widgets.list"], {"limit": True})
check("a boolean is not an integer", err and "'limit' must be an integer" in err, err)
_, err = P.bind(by["widgets.list"], {"size": 3})
check("an unknown argument is named with the accepted ones", err and "does not take ['size']" in err and "limit" in err, err)
_, err = P.bind(by["widgets.inspect"], {"widget_path": "a/b"})
check("a missing required argument is named with its type", err and "needs 'depth'" in err and "integer" in err, err)
_, err = P.bind(by["widgets.inspect"], {"depth": 1})
check("a path parameter is always required", err and "needs 'widget_path'" in err, err)
placed, err = P.bind(by["widgets.list"], {"limit": None, "colour": ""})
check("an empty optional argument is left out", err is None and placed["query"] == {}, (placed, err))
check("a path argument is quoted — no injection into the route",
      P.url_for(by["widgets.get"], {"widget_id": "../admin?x=1"}) == "/api/v1/widgets/..%2Fadmin%3Fx%3D1/".rstrip("/"))
check("a path converter keeps its slashes", P.url_for(by["widgets.inspect"], {"widget_path": "a/b c"}) == "/api/v1/widgets/a/b%20c/inspect")
check("query values are encoded (booleans lowercase, lists repeated)",
      P._query_string({"a": True, "b": [1, 2]}) == [("a", "true"), ("b", "1"), ("b", "2")])

# ── §D the answer ───────────────────────────────────────────────────────────────
print("§D the answer")
check("project keeps only the requested fields of every record, and walks wrappers",
      P.project({"status": "ok", "widgets": [{"id": 1, "name": "a", "x": 9}, {"id": 2, "x": 8}]}, ["id", "name"])
      == {"status": "ok", "widgets": [{"id": 1, "name": "a"}, {"id": 2}]})
check("project with no fields changes nothing", P.project({"a": 1}, []) == {"a": 1})
small = {"a": [1, 2, 3]}
check("a small answer is untouched and says nothing", P.bound(small) == (small, None))
big = {"rows": [{"id": i, "name": f"r{i}", "nested": {"k": list(range(50))}, "text": "y" * 3000} for i in range(60)]}
b, note = P.bound(big, limit=8000)
check("a large answer fits its limit", len(json.dumps(b)) <= 8000, len(json.dumps(b)))
check("nested detail goes first: a record keeps its plain fields", isinstance(b["rows"][0], dict) and b["rows"][0]["id"] == 0 and b["rows"][0]["name"] == "r0", b["rows"][0])
check("a nested part says how big it was", b["rows"][0]["nested"] in ("{1 fields}",) or "items" in str(b["rows"][0]["nested"]), b["rows"][0])
check("the note says what was cut and how to get everything", note and "fields" in note, note)
check("a cut list says how many it holds back", any(isinstance(x, str) and "more" in x for x in b["rows"]) == ("not shown" in note), note)

# ── §E call — the real route, as the user ───────────────────────────────────────
print("§E call")
_real_user_tier = P.user_tier
P.user_tier = lambda uid: {"u-1": "user", "a-1": "admin"}.get(uid)


def run(*a, **k):
    return asyncio.run(P.call(*a, cat=cat, app=app, **k))


r = run("widgets.list", {"limit": 2, "archived": False}, "u-1")
check("a read runs the real route and returns its answer", r["ok"] and r["status"] == 200 and len(r["result"]["widgets"]) == 2, r)
check("…as the user: an internal token naming that user", seen["list"]["who"].get("user_id") == "u-1" and seen["list"]["who"].get("internal") is True, seen.get("list"))
check("…naming the operation", seen["list"]["op"] == "widgets.list")
check("…with a token bound to this one request (method and decoded path)",
      seen["list"]["who"].get("op_scope") == {"op": "widgets.list", "method": "GET", "path": "/api/v1/widgets"},
      seen["list"]["who"].get("op_scope"))
_exp = seen["list"]["who"]["exp"] - seen["list"]["who"]["iat"]
check("…that lives a minute", 55 <= _exp <= 65, _exp)
check("…with the query encoded", seen["list"]["args"] == {"limit": ["2"], "archived": ["false"]}, seen["list"]["args"])
r = run("widgets.inspect", {"widget_path": "shelf/3", "depth": "2"}, "u-1")
check("a POST read sends its body as JSON with coerced types", r["ok"] and seen["inspect"]["body"] == {"depth": 2} and seen["inspect"]["path"] == "shelf/3", (r, seen.get("inspect")))
r = run("widgets.get", {"widget_id": "nope"}, "u-1")
check("a route's refusal comes back as it is (status and body), not as success", r["ok"] is False and r["status"] == 404 and r["result"] == {"error": "not found"}, r)
seen.pop("create", None); seen.pop("sign", None)
r = run("widgets.create", {}, "u-1")
check("a write is refused before any route runs (writes off by default)", r["ok"] is False and "writes are off" in r["error"] and "create" not in seen, r)
r = run("widgets.sign_off", {"widget_id": "w1"}, "u-1")
check("a person's act is refused before any route runs", r["ok"] is False and "person's act" in r["error"] and "sign" not in seen, r)
r = run("widgets.stream", {}, "u-1")
check("`never` is unknown to a call", r["ok"] is False and "unknown operation" in r["error"], r)
r = run("admin_widgets.list", {}, "u-1")
check("an operation above the user's tier is unknown to them, and does not run", r["ok"] is False and "unknown operation" in r["error"] and "admin" not in seen, r)
r = run("admin_widgets.list", {}, "a-1")
check("…and runs for an admin", r["ok"] and seen.get("admin"), r)
r = run("admin_widgets.owners", {}, "a-1")
check("other users' data does not run, even for an admin (containment comes first)", r["ok"] is False and "owners" not in seen, r)
r = run("widgets.lst", {}, "u-1")
check("a near-miss op id is refused with the closest ids", r["ok"] is False and r["did_you_mean"][0]["op"] == "widgets.list", r)
r = run("widgets.list", {}, "nobody")
check("no active user calls nothing", r["ok"] is False and "no active user" in r["error"], r)
r = run("widgets.list", {"bogus": 1}, "u-1")
check("an argument error comes back as a sentence", r["ok"] is False and "does not take" in r["error"], r)
r = run("widgets.huge", {}, "u-1")
check("a huge answer is shortened with a note", r["ok"] and r.get("shortened") and len(json.dumps(r["result"])) <= P.RESULT_CHARS + 200, (r.get("shortened"), len(json.dumps(r["result"]))))
r = run("widgets.huge", {}, "u-1", fields=["id", "name"])
check("…and `fields` returns every record, whole", r["ok"] and not r.get("shortened") and len(r["result"]["rows"]) == 200 and r["result"]["rows"][7] == {"id": 7, "name": "row 7"}, r.get("shortened"))
r = run("widgets.get", {"widget_id": "x"}, "u-1", fields=["id"])
check("fields never reshape a refusal", r["result"] == {"error": "not found"}, r)

# ── §E2 the live probe's three findings, pinned ─────────────────────────────────
print("§E2 live-probe findings")
import trusted_data_agent.core.config_manager as _CM  # noqa: E402


class _FakeCM:
    def get_profiles(self, uid):
        return [{"id": "profile-aaa", "tag": "SALES"}, {"id": "profile-bbb", "tag": "TCIDE"}] if uid == "u-1" else []


_orig_gcm = _CM.get_config_manager
_CM.get_config_manager = lambda: _FakeCM()
try:
    check("an agent's id resolves to itself", P.resolve_agent("profile-bbb", "u-1") == ("profile-bbb", None))
    check("an @TAG resolves to the agent's id", P.resolve_agent("@TCIDE", "u-1") == ("profile-bbb", None))
    check("…case-insensitive, with or without @", P.resolve_agent("sales", "u-1") == ("profile-aaa", None))
    _v, _e = P.resolve_agent("profile-zzz", "u-1")
    check("an id or tag the user does not have is refused, listing their agents' tags",
          _v is None and "no agent of yours" in _e and "@SALES" in _e and "@TCIDE" in _e, _e)
    check("…and another user's agent is not theirs", P.resolve_agent("profile-aaa", "u-other")[0] is None)
    check("describe tells the model a tag works", "@TAG works too" in P.describe(by["agents.things"])["args"]["profile_id"])
    r = run("agents.things", {"profile_id": "@TCIDE"}, "u-1")
    check("a call with an @TAG reaches the route with the agent's id", r["ok"] and seen.get("things") == "profile-bbb", (r, seen.get("things")))
    seen.pop("things", None)
    r = run("agents.things", {"profile_id": "profile-1764006444002-z0hdduce9"}, "u-1")
    check("an id that is not one of the user's agents is refused before any route runs",
          r["ok"] is False and "no agent of yours" in r["error"] and "things" not in seen, r)
finally:
    _CM.get_config_manager = _orig_gcm

r = run("widgets.list", {"limit": 2}, "u-1", fields=["name", "profile_id"])
check("a requested field that matches nothing is named, never silently dropped",
      r["ok"] and r.get("fields_not_found") == ["profile_id"], r)
check("…with the fields the records do carry", r.get("record_fields") == ["colour", "detail", "id", "name"], r.get("record_fields"))
check("…and the fields that matched are still projected", r["result"]["widgets"] == [{"name": "Widget 0"}, {"name": "Widget 1"}], r["result"])
r = run("widgets.list", {"limit": 1}, "u-1", fields=["name"])
check("all fields found: no note", "fields_not_found" not in r and "record_fields" not in r, r)
check("record_fields reads the records inside lists", P.record_fields({"a": 1, "rows": [{"x": 1}, {"y": 2}]}) == ["x", "y"])

big = {"records": [{"id": i, "name": f"agent {i}", "blob": "z" * 500, "nested": {"k": list(range(40))}} for i in range(36)],
       "active_ids": [f"profile-{i:08d}-0000-0000-0000-000000000000" for i in range(17)]}
b, note = P.bound(big, limit=6000)
check("bounding keeps a list of plain values whole while records shrink (the 17-id list the probe lost)",
      note and b["active_ids"] == big["active_ids"] and len(b["records"]) < 36, (len(b.get("active_ids", [])), note))
huge_ids = {"ids": [f"id-{i:06d}" for i in range(5000)]}
b, note = P.bound(huge_ids, limit=6000)
check("…but a plain list too large to fit is still cut, and says so", note and len(json.dumps(b)) <= 6100 and any("more" in str(x) for x in b["ids"]), note)

seen.pop("list", None)
r = run("list_widgets", {}, "u-1")
check("an invented op id is answered with what its words find (did_you_mean), with their arguments",
      r["ok"] is False and r.get("did_you_mean") and r["did_you_mean"][0]["op"] == "widgets.list"
      and "args" in r["did_you_mean"][0], r)
check("…and never run in its place", "list" not in seen)
check("the suggestions are in the error's own text (a lane that keeps only the text still sees them)",
      "Did you mean: widgets.list (List the caller's widgets; args: limit, colour, archived)" in r["error"], r["error"])
r = run("zzz_qqq", {}, "u-1")
check("an id whose words find nothing just says find first", r["ok"] is False and "did_you_mean" not in r, r)

r = run("widgets.catalog", {}, "u-1")
check("too large and no fields named: every record with its summary fields, nothing cut",
      r["ok"] and len(r["result"]["widgets"]) == 200 and r["result"]["widgets"][7] == {"id": "w7", "name": "row 7"}
      and "shortened" not in r, (r.get("shortened"), r.get("summarised")))
check("…the wrapper's plain fields stay (the id list, the default)",
      r["result"]["default_id"] == "w3" and len(r["result"]["ids"]) == 200, list(r["result"]))
check("…and the answer says it was summarised and how to get more", "summary fields (id, name)" in (r.get("summarised") or ""), r.get("summarised"))
_cat = build_catalog(app)
check("the catalog carries an operation's summary fields", _cat.by_op["widgets.catalog"].summary_fields == ("id", "name"))
r = run("widgets.catalog", {}, "u-1", fields=["blob"])
check("named fields win over summary fields", "summarised" not in r and set(r["result"]["widgets"][0]) == {"blob"}, r.get("summarised"))

# ── §F the component ────────────────────────────────────────────────────────────
print("§F the component")
man = json.loads((ROOT / "components/builtin/platform/manifest.json").read_text())
check("off for every agent until its owner enables it (enabled_for is empty)", man["profile_defaults"]["enabled_for"] == [])
check("the tool is TDA_Platform with find and call", man["tool_definition"]["name"] == "TDA_Platform"
      and "'find' | 'call'" in man["tool_definition"]["args"]["action"]["description"])
check("arguments are an object, fields a list of names", man["tool_definition"]["args"]["args"]["type"] == "dict"
      and man["tool_definition"]["args"]["fields"]["type"] == "list[str]")
check("headless, holding nothing", "frontend" not in man and man["instances"] == "none")
reg = json.loads((ROOT / "skills/skill_registry.json").read_text())
comp_skill = [s for s in reg["skills"] if s.get("component_id") == "platform"]
check("its companion skill is registered and linked", len(comp_skill) == 1 and comp_skill[0]["skill_id"] == "platform-expert"
      and (ROOT / "skills/builtin/platform-expert/platform-expert.md").exists())
inst = json.loads((ROOT / "components/builtin/platform/instructions.json").read_text())
check("the instructions alone carry correctness: find first, never invent, callable:false is not retried",
      all(k in inst["medium"] for k in ("'find'", "Never invent", "callable:false", "never retry")), inst["medium"])
skill_md = (ROOT / "skills/builtin/platform-expert/platform-expert.md").read_text()
check("the skill names the classes the way the platform does",
      all(w in skill_md for w in ("**Ideate**", "**Focus**", "**Optimize**", "**Coordinate**")))

from components.builtin.platform.handler import PlatformHandler  # noqa: E402

from components.builtin.platform.handler import normalize  # noqa: E402

h = PlatformHandler()
check("off the Optimize component fast path (a call depends on an earlier find; only the tactical planner sees it)",
      h.is_deterministic is False)
# the argument shapes the Optimize planner wrote live, blind
check("normalize: operation='find' + search_terms → find with a query",
      normalize({"operation": "find", "search_terms": ["my agents"]}) == {"action": "find", "query": "my agents"})
check("normalize: operation='call' + op_id + arguments{fields} → call, op, fields lifted out",
      normalize({"operation": "call", "op_id": "profiles.list", "arguments": {"fields": ["tag"]}})
      == {"action": "call", "op": "profiles.list", "fields": ["tag"]})
check("normalize: a top-level operation argument moves into args on a call",
      normalize({"op": "profiles.components", "profile_id": "@X"}) == {"action": "call", "op": "profiles.components", "args": {"profile_id": "@X"}})
check("normalize: an op id given as 'operation' is a call",
      normalize({"operation": "profiles.components"}) == {"action": "call", "op": "profiles.components"})
check("normalize: words given as 'operation' are a find — never a guessed call",
      normalize({"operation": "agent components", "profile_id": "@X"}) == {"action": "find", "query": "agent components"})
check("normalize: the contract's own names pass unchanged",
      normalize({"action": "call", "op": "a.b", "args": {"x": 1}, "fields": ["y"]}) == {"action": "call", "op": "a.b", "args": {"x": 1}, "fields": ["y"]})
check("normalize: a call's arguments written into query as JSON are the arguments",
      normalize({"action": "call", "op": "trust.agent", "query": '{"profile_id": "@X"}'})
      == {"action": "call", "op": "trust.agent", "args": {"profile_id": "@X"}})
check("normalize: a find's query stays a query even when it looks like JSON",
      normalize({"action": "find", "query": '{"x": 1}'})["query"] == '{"x": 1}')
check("normalize: args as a JSON string are read for fields",
      normalize({"action": "call", "op": "a.b", "args": '{"x": 1, "fields": ["y"]}'}) == {"action": "call", "op": "a.b", "args": {"x": 1}, "fields": ["y"]})
check("validation: action is find or call", h.validate_arguments({"action": "delete"})[0] is False)
check("validation: find needs a query", h.validate_arguments({"action": "find"})[0] is False)
check("validation: call needs an op", h.validate_arguments({"action": "call"})[0] is False)
check("validation: a well-formed call passes", h.validate_arguments({"action": "call", "op": "widgets.list"}) == (True, ""))
check("args as a JSON string is decoded", h._as_object('{"a": 1}') == ({"a": 1}, ""))
check("args that are not an object are named", h._as_object("[1]")[1] != "" and h._as_object("nope")[1] != "")
r = asyncio.run(h.process({"action": "find", "query": "widgets"}, {"user_uuid": "u-1", "profile_id": None}))
check("a call where the tool is not enabled is refused, telling the agent not to retry",
      "not turned it on" in (r.spec.get("error") or "") and r.metadata.get("error"), r.spec)
PlatformHandler._enabled_here = staticmethod(lambda c: True)
# No role context for the synthetic widget app (roles are gated in test_platform_roles.py) —
# and never a profile read for a synthetic user: the config manager would bootstrap a row.
PlatformHandler._roles = staticmethod(lambda c: None)
PlatformHandler._playbook = lambda self, *a, **k: None   # playbook delivery: test_platform_playbooks.py
PlatformHandler._writes_and_agent = staticmethod(lambda c: ("off", None))   # writes: test_platform_writes.py
_orig_cat, _orig_app = P.catalog, P._app
P.catalog = lambda app=None: cat
P._app = lambda: app
try:
    r = asyncio.run(h.process({"action": "find", "query": "list widgets"}, {"user_uuid": "u-1", "profile_id": "p"}))
    check("find through the tool", r.spec["found"][0]["op"] == "widgets.list" and r.metadata["suppress_inline"] is True, r.spec)
    r = asyncio.run(h.process({"action": "call", "op": "widgets.list", "args": '{"limit": 1}', "fields": "id, name"},
                              {"user_uuid": "u-1", "profile_id": "p"}))
    check("call through the tool: JSON-string args, comma fields", r.spec["ok"] and r.spec["result"]["widgets"] == [{"id": "w0", "name": "Widget 0"}], r.spec)
    r = asyncio.run(h.process({"action": "call", "op": "widgets.create"}, {"user_uuid": "u-1", "profile_id": "p"}))
    check("a refusal through the tool is an error the turn records", r.spec["ok"] is False and r.metadata.get("error"), r.spec)
    r = asyncio.run(h.process({"profile_id": "@X"}, {"user_uuid": "u-1", "profile_id": "p"}))
    check("process validates itself on every lane: no action, op or query names the contract (not \"unknown operation ''\")",
          "takes action" in (r.spec.get("error") or "") and "unknown operation" not in (r.spec.get("error") or ""), r.spec)
    r = asyncio.run(h.process({"operation": "call", "op_id": "widgets.list", "arguments": {"limit": 1, "fields": ["id"]}},
                              {"user_uuid": "u-1", "profile_id": "p"}))
    check("the planner's own names run end to end through the tool", r.spec.get("ok") and r.spec["result"]["widgets"] == [{"id": "w0"}], r.spec)
finally:
    P.catalog, P._app = _orig_cat, _orig_app

# ── §G wiring ───────────────────────────────────────────────────────────────────
print("§G wiring")
logging.disable(logging.CRITICAL)
from trusted_data_agent.main import app as real_app  # noqa: E402
logging.disable(logging.NOTSET)
from trusted_data_agent.core.config import APP_STATE  # noqa: E402
check("the served app is registered for in-process dispatch", APP_STATE.get("quart_app") is real_app)
P.reset()
real = P.catalog()
check("the runtime catalog is the served app's", real is not None and "profiles.list" in real.by_op)
check("…built once per app", P.catalog() is real)
r = P.find("which agents do I have", "user")
check("over the real catalog, 'which agents do I have' first finds an operation that lists the agents (nothing missing)",
      r["found"] and r["found"][0]["op"] in ("profiles.list", "trust.agents") and "args" not in r["found"][0], r["found"][:3])
r = P.find("make this agent my default", "user")
check("…and a person's act is found and marked", r["found"][0]["op"] == "profiles.set_default" and r["found"][0]["callable"] is False, r["found"][:2])

# ── §H the call's token — good for one request only ──────────────────────────────
print("§H the call's token")
from trusted_data_agent.auth import security as SEC  # noqa: E402
from trusted_data_agent.auth import middleware as MW  # noqa: E402


async def _verify_in(the_app, method, path, token):
    async with the_app.test_request_context(path, method=method, headers={"Authorization": f"Bearer {token}"}):
        return SEC.verify_auth_token(token)


tok = SEC.create_operation_token("u-x", method="GET", path="/api/v1/widgets", op="widgets.list")
v = asyncio.run(_verify_in(app, "GET", "/api/v1/widgets", tok))
check("accepted on the request it was minted for", v and v["user_id"] == "u-x" and v["op_scope"]["op"] == "widgets.list", v)
check("refused on another path", asyncio.run(_verify_in(app, "GET", "/api/v1/widgets/huge", tok)) is None)
check("refused on another method", asyncio.run(_verify_in(app, "POST", "/api/v1/widgets", tok)) is None)
check("refused outside a request (fails closed)", SEC.verify_auth_token(tok) is None)
tok_sp = SEC.create_operation_token("u-x", method="POST", path="/api/v1/widgets/a b/inspect", op="widgets.inspect")
check("bound to the decoded path the app routes on",
      asyncio.run(_verify_in(app, "POST", "/api/v1/widgets/a%20b/inspect", tok_sp)) is not None)
check("an expired one is refused",
      asyncio.run(_verify_in(app, "GET", "/api/v1/widgets", SEC.create_operation_token(
          "u-x", method="GET", path="/api/v1/widgets", op="widgets.list", seconds=-5))) is None)
import jwt as _jwt  # noqa: E402
_forged = _jwt.encode({**_jwt.decode(tok, SEC.JWT_SECRET_KEY, algorithms=[SEC.JWT_ALGORITHM]),
                       "op_scope": {"op": "x", "method": "GET", "path": "/api/v1/widgets/huge"}},
                      "not-the-secret", algorithm=SEC.JWT_ALGORITHM)
check("a scope re-signed with another key is refused", asyncio.run(_verify_in(app, "GET", "/api/v1/widgets/huge", _forged)) is None)
_bad = SEC.create_operation_token("u-x", method="GET", path="/api/v1/widgets", op="w")
_p = _jwt.decode(_bad, SEC.JWT_SECRET_KEY, algorithms=[SEC.JWT_ALGORITHM])
_p["op_scope"] = "anything"
check("a malformed scope is refused", asyncio.run(_verify_in(app, "GET", "/api/v1/widgets",
      _jwt.encode(_p, SEC.JWT_SECRET_KEY, algorithm=SEC.JWT_ALGORITHM))) is None)
_plain = SEC.create_internal_token("u-x")
check("an unbound internal token is unchanged (the coordinator and flows keep working)",
      asyncio.run(_verify_in(app, "GET", "/api/v1/anything", _plain)) is not None)

# Against the real app and a real user: the middleware, its cache, the dispatch end to end.
from trusted_data_agent.auth.database import get_db_session  # noqa: E402
from trusted_data_agent.auth.models import User  # noqa: E402
with get_db_session() as _s:
    _u = _s.query(User).filter_by(is_active=True).first()
    real_uid = _u.id if _u else None
check("a real active user exists for the end-to-end checks", real_uid is not None)
if real_uid:
    import hashlib  # noqa: E402
    P.user_tier = _real_user_tier

    async def _who_in(path, token, method="GET"):
        async with real_app.test_request_context(path, method=method, headers={"Authorization": f"Bearer {token}"}):
            return MW.get_current_user()
    t = SEC.create_operation_token(real_uid, method="GET", path="/api/v1/profiles/default", op="profiles.default")
    check("the middleware accepts it on its request", getattr(asyncio.run(_who_in("/api/v1/profiles/default", t)), "id", None) == real_uid)
    check("…and never caches it (the cache is read before verification)",
          hashlib.sha256(t.encode()).hexdigest() not in MW._auth_cache)
    check("…so the same token on another route is refused", asyncio.run(_who_in("/api/v1/profiles", t)) is None)

    async def _raw(path, token):
        r = await real_app.test_client().get(path, headers={"Authorization": f"Bearer {token}"})
        return r.status_code
    check("end to end: the token for one route opens nothing else (401 on another route)",
          asyncio.run(_raw("/api/v1/profiles/active_for_consumption", t)) == 401)
    r = asyncio.run(P.call("profiles.default", {}, real_uid))
    check("end to end: a call through the real app and middleware succeeds as the user", r["ok"] and r["status"] == 200, r)
    # The overview is composed from the components, skills and trust routes — one call, one truth.
    from trusted_data_agent.core.config_manager import get_config_manager as _gcm_ov
    _tags = [p.get("tag") for p in (_gcm_ov().get_profiles(real_uid) or []) if p.get("tag")]
    if _tags:
        _ov = asyncio.run(P.call("profiles.overview", {"profile_id": "@" + _tags[0]}, real_uid))
        _res = _ov.get("result") or {}
        check("end to end: the overview answers one agent in one call",
              _ov.get("ok") and _res.get("tag") == _tags[0]
              and {"class", "model", "data_source", "components", "skills", "trust", "default", "open_to_rest"} <= set(_res)
              and _res.get("class") in ("Ideate", "Focus", "Optimize", "Coordinate") and "not_read" not in _res,
              {k: _res.get(k) for k in ("tag", "class", "not_read")})
    # An EMPTY list asked for fields is "nothing matched", never "fields not found" (which a
    # model reads as a wrong field name and then guesses another).
    _fl = P.catalog().by_op["files.list"]
    _e = P._shape(_fl, 200, {"files": [], "total": 0}, ["id", "filename"])
    check("an empty list asked for fields reads as empty, not as missing fields",
          _e.get("empty") and not _e.get("fields_not_found"), _e)
    _m = P._shape(_fl, 200, {"files": [{"id": "a", "filename": "x"}]}, ["id", "owner"])
    check("…records without a field asked for still name it", _m.get("fields_not_found") == ["owner"] and not _m.get("empty"), _m)
    _d = P._shape(_fl, 200, {"id": "a"}, ["owner"])
    check("…and so does a single record", _d.get("fields_not_found") == ["owner"] and not _d.get("empty"), _d)
    # A summary field is a promise about the route's records: it must be a key they carry
    # (a guessed `trust_state` was caught by hand once — now by this check, on the real routes).
    _real_cat = P.catalog()
    for _o in _real_cat.operations:
        if not _o.summary_fields or not P.availability(_o)[0] or _o.tier != "user" or any(
                a.required for a in list(_o.query.values()) + list(_o.body.values())) or _o.path_params:
            continue
        _r = asyncio.run(P.call(_o.op, {}, real_uid, fields=list(_o.summary_fields)))
        check(f"{_o.op}: every summary field is a key its records carry", _r.get("ok") and not _r.get("fields_not_found"),
              (_r.get("fields_not_found"), _r.get("record_fields")))

TS.similarity = _real_similarity
print(f"\n{'ALL PASS' if not failed else 'FAILED'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
