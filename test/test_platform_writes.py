#!/usr/bin/env python3
"""
TDA_Platform writes (phase 3) — an agent may change the platform only as its write mode allows,
and anything that matters waits for a person.

Contract: src/trusted_data_agent/core/platform_operations.py (write_mode · always_reviewed ·
park_write · call · perform_parked · redact), the approval branch of
POST /v1/assurance/reviews/<id>/<decision>, the downgrade guard in profile_config_contract.

  §A  the write mode — off (default) · review · allow, from authorityConfig.platformSurface
  §B  off — a write is refused before any route runs
  §C  review — the exact request is parked in the review queue (the Assurance Inbox) with the
      owner's notice; nothing runs; the agent is told it has NOT run
  §D  allow — an ordinary write runs; a write that governs an agent or deletes is parked anyway;
      a person's act never runs
  §E  describe — the model knows before it calls: refused, or "needs approval"
  §F  approval — the parked request runs for its owner, as the owner, after it is checked
      against the CURRENT catalog; what no longer holds is refused and nothing runs
  §G  credentials never reach a model — withheld at any depth in every result
  §H  the downgrade guard — widening an agent's write mode on an update is flagged
  §I  the real approval route — wired to perform_parked, an item that cannot run stays open
  §J  the Assurance Inbox shows a platform request as what it is

Run: PYTHONPATH=src python3 test/test_platform_writes.py   (no LLM calls; the review queue
runs on a temporary database — nothing is written to the instance's)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "platform-writes-test")

from quart import Blueprint, Quart, jsonify, request  # noqa: E402

from trusted_data_agent.api.operations import Arg, build_catalog, operation  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
from trusted_data_agent.core import review_queue as RQ  # noqa: E402
from trusted_data_agent.core import trust_events as TE  # noqa: E402
from trusted_data_agent.agent import tool_semantics as TS  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


# The review queue on a temporary database — the instance's is never touched.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
_orig_db_path = RQ._db_path
RQ._db_path = lambda db_path=None: _tmp.name
notices = []
_orig_notice = TE.broadcast_notice
TE.broadcast_notice = lambda uid, et, summary, **k: notices.append((uid, et, summary, k))
TS.similarity = lambda *a, **k: None

bp = Blueprint("w", __name__)
seen = []


def _who():
    import jwt
    from trusted_data_agent.auth.security import JWT_SECRET_KEY, JWT_ALGORITHM
    return jwt.decode(request.headers.get("Authorization", "")[7:], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


@bp.route("/v1/gadgets", methods=["POST"])
@operation("gadgets.create", effect="write", scope="own", summary="Create a gadget",
           body={"name": Arg("string", "its name", required=True)})
async def _create():
    seen.append(("create", (await request.get_json()) or {}, _who().get("user_id")))
    return jsonify({"created": True}), 201


@bp.route("/v1/gadgets/<gid>/rules", methods=["PUT"])
@operation("gadgets.rules", effect="write", scope="own", governs=True, summary="Change what a gadget may do",
           path={"gid": "its id"}, body={"rule": "the rule"})
async def _rules(gid):
    seen.append(("rules", gid, None))
    return jsonify({"ok": True}), 200


@bp.route("/v1/gadgets/<gid>", methods=["DELETE"])
@operation("gadgets.delete", effect="write", scope="own", summary="Delete a gadget", path={"gid": "its id"})
async def _delete(gid):
    seen.append(("delete", gid, None))
    return jsonify({"deleted": gid}), 200


@bp.route("/v1/gadgets/<gid>/sign-off", methods=["POST"])
@operation("gadgets.sign_off", effect="human_only", scope="own", governs=True, summary="Sign a gadget off",
           path={"gid": "its id"})
async def _sign(gid):
    seen.append(("sign", gid, None))
    return jsonify({}), 200


@bp.route("/v1/gadgets/secrets", methods=["GET"])
@operation("gadgets.secrets", effect="read", scope="own", summary="A gadget configuration")
async def _secrets():
    return jsonify({"name": "g", "credentials": {"api_key": "sk-123"}, "input_tokens": 42, "total_tokens": 50,
                    "transport": {"headers": {"Authorization": "Bearer xyz", "Accept": "json"}},
                    "client_secret": "s3", "access_token": "t0", "empty_credentials": {}, "credentials_none": None})


@bp.route("/v1/gadgets/broken", methods=["POST"])
@operation("gadgets.broken", effect="write", scope="own", summary="A write the route refuses")
async def _broken():
    return jsonify({"error": "no"}), 400


app = Quart("platform_writes_test")
app.register_blueprint(bp, url_prefix="/api")
cat = build_catalog(app)
check("the synthetic catalog is well-formed", cat.problems == [], cat.problems)
by = cat.by_op
P.user_tier = lambda uid: {"owner-1": "user"}.get(uid)
AGENT = {"id": "p-1", "tag": "BUILDER"}


def call(op, args=None, writes="off", **k):
    return asyncio.run(P.call(op, args or {}, "owner-1", cat=cat, app=app, writes=writes, agent=AGENT,
                              session_id=None, turn_id=None, **k))


def items(status="pending"):
    return RQ.list_items("owner-1", status=status, include_content=True) if "include_content" in RQ.list_items.__code__.co_varnames \
        else RQ.list_items("owner-1", status=status)


try:
    # ── §A the write mode ───────────────────────────────────────────────────────
    print("§A the write mode")
    check("no platformSurface: writes are off", P.write_mode({}) == "off" and P.write_mode(None) == "off")
    check("review and allow are read", P.write_mode({"authorityConfig": {"platformSurface": {"writes": "review"}}}) == "review"
          and P.write_mode({"authorityConfig": {"platformSurface": {"writes": "ALLOW"}}}) == "allow")
    check("anything else is off (fail closed)", P.write_mode({"authorityConfig": {"platformSurface": {"writes": "yes"}}}) == "off")
    check("a write that governs an agent always needs a person", P.always_reviewed(by["gadgets.rules"]) == "it changes what an agent may do")
    check("a delete always needs a person", P.always_reviewed(by["gadgets.delete"]) == "it deletes")
    check("an ordinary write does not", P.always_reviewed(by["gadgets.create"]) is None)

    # ── §B off ──────────────────────────────────────────────────────────────────
    print("§B off")
    seen.clear()
    r = call("gadgets.create", {"name": "a"}, writes="off")
    check("writes off: refused before any route runs, naming the mode", r["ok"] is False and "writes are off" in r["error"] and not seen, r)
    check("…and nothing was submitted", items() == [])

    # ── §C review ───────────────────────────────────────────────────────────────
    print("§C review")
    notices.clear()
    r = call("gadgets.create", {"name": "alpha"}, writes="review")
    check("review: the write is submitted, not run", r.get("status") == "awaiting_approval" and r.get("request_id") and not seen, r)
    check("…the agent is told it has NOT run and where the user decides",
          "has NOT run" in r["message"] and "Assurance Inbox" in r["message"], r.get("message"))
    pend = items()
    check("…one pending item in the review queue, marked as a platform write",
          len(pend) == 1 and pend[0]["source"] == "platform" and pend[0]["status"] == "pending", pend)
    full = RQ.get_item("owner-1", r["request_id"])
    payload = json.loads(full["original_text"])
    check("…holding the exact request: operation, arguments, agent",
          payload["op"] == "gadgets.create" and payload["args"] == {"name": "alpha"} and payload["profile_tag"] == "BUILDER", payload)
    check("…described for a person", "@BUILDER asks to: Create a gadget (gadgets.create) — name=alpha" in full["delivered_text"], full["delivered_text"])
    _rid = P.park_write(by["gadgets.create"], {"path": {}, "query": {}, "body": {"name": "as-it-runs"}},
                        {"name": "@as-sent"}, "owner-1", agent=AGENT)
    check("the parked request holds the arguments as they will run (bound and normalised), not as sent",
          json.loads(RQ.get_item("owner-1", _rid)["original_text"])["args"] == {"name": "as-it-runs"})
    check("…and the owner is told at once", notices and notices[-1][0] == "owner-1" and notices[-1][1] == "platform_write_awaits")

    # ── §D allow ────────────────────────────────────────────────────────────────
    print("§D allow")
    seen.clear()
    r = call("gadgets.create", {"name": "beta"}, writes="allow")
    check("allow: an ordinary write runs, as the user", r["ok"] and r["status"] == 201 and seen == [("create", {"name": "beta"}, "owner-1")], (r, seen))
    seen.clear()
    r = call("gadgets.rules", {"gid": "g1", "rule": "x"}, writes="allow")
    check("allow: a write that governs an agent is still submitted, not run",
          r.get("status") == "awaiting_approval" and not seen and "changes what an agent may do" in r["message"], r)
    r = call("gadgets.delete", {"gid": "g1"}, writes="allow")
    check("allow: a delete is still submitted, not run", r.get("status") == "awaiting_approval" and not seen and "it deletes" in r["message"], r)
    r = call("gadgets.sign_off", {"gid": "g1"}, writes="allow")
    check("allow: a person's act never runs, never is submitted", r["ok"] is False and "person's act" in r["error"] and not seen, r)
    r = call("gadgets.create", {}, writes="review")
    check("a request with a missing argument is refused, not submitted", r["ok"] is False and "needs 'name'" in r["error"], r)

    # ── §D2 open pages are told a change ran ────────────────────────────────────
    print("§D2 open pages are told")
    notices.clear()
    call("gadgets.create", {"name": "eps"}, writes="allow")
    pc = [n for n in notices if n[1] == "platform_change"]
    check("a write that ran tells the user's open pages, naming the operation and its namespace",
          len(pc) == 1 and pc[0][3].get("notice_type") == "platform_change"
          and pc[0][3]["extra"] == {"op": "gadgets.create", "namespace": "gadgets", "method": "POST"}, pc)
    notices.clear()
    call("gadgets.create", {"name": "zeta"}, writes="review")
    check("a submitted (parked) write announces no change — nothing ran", not [n for n in notices if n[1] == "platform_change"])
    notices.clear()
    call("gadgets.secrets")
    call("gadgets.broken", {}, writes="allow")
    check("a read, or a write the route refused, announces no change", not [n for n in notices if n[1] == "platform_change"])

    # ── §E describe ─────────────────────────────────────────────────────────────
    print("§E describe")
    d = P.describe(by["gadgets.create"], None, "off")
    check("writes off: listed, not callable, naming the mode", d["callable"] is False and "writes are off" in d["why_not"])
    d = P.describe(by["gadgets.create"], None, "review")
    check("review: callable, needs approval", d["callable"] is True and d.get("needs_approval") is True)
    d = P.describe(by["gadgets.create"], None, "allow")
    check("allow: callable, no approval for an ordinary write", d["callable"] is True and "needs_approval" not in d)
    d = P.describe(by["gadgets.rules"], None, "allow")
    check("allow: a write that governs still needs approval", d["callable"] is True and d.get("needs_approval") is True)
    f = P.find("create a gadget", "user", cat=cat, writes="review")
    check("find carries the same marks", f["found"][0]["op"] == "gadgets.create" and f["found"][0].get("needs_approval") is True, f["found"][:1])

    # ── §F approval ─────────────────────────────────────────────────────────────
    print("§F approval")
    parked = RQ.get_item("owner-1", call("gadgets.create", {"name": "gamma"}, writes="review")["request_id"])
    seen.clear()
    notices.clear()
    rec = asyncio.run(P.perform_parked(parked, "owner-1", approved_by="user:owner-1", cat=cat, app=app))
    check("approved: the request runs, for its owner, as its owner", rec["outcome"] == "performed" and rec["status"] == 201
          and seen == [("create", {"name": "gamma"}, "owner-1")], (rec, seen))
    check("…and the owner's open pages are told it ran", any(n[1] == "platform_change" and n[0] == "owner-1" for n in notices))
    gone = dict(parked, original_text=json.dumps({"op": "gadgets.vanished", "args": {}}))
    seen.clear()
    rec = asyncio.run(P.perform_parked(gone, "owner-1", cat=cat, app=app))
    check("an operation no longer in the catalog is refused, nothing runs", rec["outcome"] == "refused" and "no longer" in rec["message"] and not seen, rec)
    human = dict(parked, original_text=json.dumps({"op": "gadgets.sign_off", "args": {"gid": "g"}}))
    rec = asyncio.run(P.perform_parked(human, "owner-1", cat=cat, app=app))
    check("a person's act is never run by an approval (a forged item)", rec["outcome"] == "refused" and not seen, rec)
    bad = dict(parked, original_text=json.dumps({"op": "gadgets.create", "args": {}}))
    rec = asyncio.run(P.perform_parked(bad, "owner-1", cat=cat, app=app))
    check("arguments that no longer hold are refused, nothing runs", rec["outcome"] == "refused" and "no longer holds" in rec["message"] and not seen, rec)
    rec = asyncio.run(P.perform_parked(dict(parked, original_text=json.dumps({"op": "gadgets.broken", "args": {}})), "owner-1", cat=cat, app=app))
    check("a route that refuses the write: refused, said as such", rec["outcome"] == "refused" and "400" in rec["message"], rec)

    # ── §G credentials never reach a model ──────────────────────────────────────
    print("§G credentials")
    r = call("gadgets.secrets")
    res = r["result"]
    check("credentials are withheld at any depth", res["credentials"] == P.WITHHELD and res["client_secret"] == P.WITHHELD
          and res["access_token"] == P.WITHHELD and res["transport"]["headers"]["Authorization"] == P.WITHHELD, res)
    check("…usage figures are not credentials (tokens ≠ token)", res["input_tokens"] == 42 and res["total_tokens"] == 50)
    check("…what is not a credential is untouched", res["name"] == "g" and res["transport"]["headers"]["Accept"] == "json")
    check("…an empty or absent value is left as it is", res["empty_credentials"] == {} and res["credentials_none"] is None)

    # ── §H the downgrade guard ──────────────────────────────────────────────────
    print("§H the downgrade guard")
    from trusted_data_agent.core.profile_config_contract import _authority_downgrades
    ws = lambda m: {"platformSurface": {"writes": m}} if m else {}
    check("off → review on an update is flagged", any(d.path == "platformSurface.writes" for d in _authority_downgrades(ws(None), ws("review"))))
    check("review → allow is flagged", any(d.path == "platformSurface.writes" for d in _authority_downgrades(ws("review"), ws("allow"))))
    check("allow → review (narrowing) is not", not any(d.path == "platformSurface.writes" for d in _authority_downgrades(ws("allow"), ws("review"))))

    # ── §H2 references and tags, seen wrong live ────────────────────────────────
    print("§H2 references and tags")
    import trusted_data_agent.core.config_manager as _CM

    class _FakeCM:
        def get_llm_configurations(self, uid):
            return [{"id": "llm-1", "name": "Google - Gemini 3.1 Flash Lite"}, {"id": "llm-2", "name": "Nova"}]

        def get_mcp_servers(self, uid):
            return [{"id": "srv-1", "name": "Sales DB"}]

        def get_profiles(self, uid):
            return [{"id": "profile-default-chat", "tag": "IDEAT"}]
    _orig_gcm = _CM.get_config_manager
    _CM.get_config_manager = lambda: _FakeCM()
    try:
        check("an LLM configuration by id resolves to itself", P.resolve_llm_config("llm-2", "o") == ("llm-2", None))
        check("…by its name too", P.resolve_llm_config("nova", "o") == ("llm-2", None))
        _v, _e = P.resolve_llm_config("profile-default-chat", "o")
        check("an AGENT's id given as a model is refused, naming the user's models (seen live)",
              _v is None and "no LLM configuration of yours" in _e and "Nova (llm-2)" in _e, _e)
        check("an MCP server by name resolves", P.resolve_mcp_server("sales db", "o") == ("srv-1", None))
        check("…an unknown one is refused", P.resolve_mcp_server("crm", "o")[0] is None)
        check("a tag is written without '@' (seen live: '@PLAYGROUND')", P.NORMALIZERS["tag"]("@PLAYGROUND ") == "PLAYGROUND")
    finally:
        _CM.get_config_manager = _orig_gcm

    # ── §I the real approval route ──────────────────────────────────────────────
    print("§I the real approval route")
    logging.disable(logging.CRITICAL)
    from trusted_data_agent.main import app as real_app
    logging.disable(logging.NOTSET)
    from trusted_data_agent.auth.database import get_db_session
    from trusted_data_agent.auth.models import User
    from trusted_data_agent.auth.security import create_internal_token
    with get_db_session() as _s:
        _u = _s.query(User).filter_by(is_active=True).first()
        real_uid = _u.id if _u else None
    check("a real active user exists for the route checks", real_uid is not None)
    if real_uid:
        # a request whose operation no longer exists — the route must try it, refuse, keep it open
        rid = RQ.park(real_uid, source="platform", original_text=json.dumps({"op": "nothing.here", "args": {}}),
                      delivered_text="@X asks to: nothing", reason="test")
        tok = create_internal_token(real_uid)

        async def _post(path):
            r = await real_app.test_client().post(path, headers={"Authorization": f"Bearer {tok}"}, json={})
            return r.status_code, await r.get_json()
        st, body = asyncio.run(_post(f"/api/v1/assurance/reviews/{rid}/approve"))
        check("approving a request that can no longer run: 409, the reason, nothing recorded as approved",
              st == 409 and body.get("error") == "platform_write_not_performed" and "no longer" in (body.get("message") or ""), (st, body))
        check("…the item stays open", (RQ.get_item(real_uid, rid) or {}).get("status") == "pending")
        st, body = asyncio.run(_post(f"/api/v1/assurance/reviews/{rid}/reject"))
        check("rejecting it: recorded, nothing runs", st == 200 and (RQ.get_item(real_uid, rid) or {}).get("status") == "rejected", (st, body))

    # ── §J the Assurance Inbox ──────────────────────────────────────────────────
    print("§J the Assurance Inbox")
    js = (ROOT / "static/js/handlers/agentPerformanceHandler.js").read_text()
    check("a platform request has its own row", "function _platformReviewRow(" in js and "r.source === 'platform'" in js)
    check("…approving it runs it (the same decision route)", "Approve — run it" in js and 'data-agent-action="review-approve"' in js)
    check("…a decided platform request says what happened", "a platform change" in js)
    nj = (ROOT / "static/js/notifications.js").read_text()
    check("the relay reloads the agent list when a platform change touches agents, and says so on the bus",
          "case 'platform_change':" in nj and "['profiles', 'llm', 'mcp'].includes(pc.namespace)" in nj
          and "window.configState.loadProfiles()" in nj and "UderiaBus.emit('uderia:profile-modified', { source: 'platform'" in nj)
    aj = (ROOT / "static/js/handlers/agentAnatomy.js").read_text()
    check("Agent Anatomy drops its cache and rebuilds when the agent list changes (a deleted agent stays selectable otherwise)",
          "window.UderiaBus.on('uderia:profile-modified'" in aj and "_data = null; _perProfile = {};" in aj)
    ix = (ROOT / "templates/index.html").read_text()
    import re as _re
    check("the changed scripts are loaded with a version stamp (browsers refetch on a bump)",
          all(_re.search(rf"{f}'\) }}}}\?v=\d+\.\d+", ix) for f in ("notifications.js", "agentAnatomy.js", "main.js")))
finally:
    RQ._db_path = _orig_db_path
    TE.broadcast_notice = _orig_notice
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass

print(f"\n{'ALL PASS' if not failed else 'FAILED'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
