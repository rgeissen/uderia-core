"""
PROP-003 · the binding REST surface, driven end to end.

Uses Quart's TEST CLIENT rather than a port: the routes, the auth decorators and the
persistence run exactly as a browser would drive them, without needing (or disturbing) a
running server. That distinction earned its keep immediately — the first run caught
`@require_admin` NOT injecting `current_user` the way `@require_auth` does, a 500 that no
amount of pure-layer testing would have found because the pure layers were correct.

Needs `tda_auth.db` with the default admin account; skips cleanly if it is absent.
"""
import asyncio
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
os.chdir(_ROOT)

if not os.path.exists("tda_auth.db"):
    print("SKIP: no tda_auth.db — this gate drives the real auth stack")
    sys.exit(0)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


async def main():
    from trusted_data_agent.main import app

    client = app.test_client()

    # A real login through the real endpoint — the faithful path, so the auth decorators
    # under test are the ones a browser would hit.
    r = await client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": "admin"})
    body = await r.get_json()
    tok = (body or {}).get("token")
    if not tok:
        print(f"login failed: {r.status_code} {str(body)[:200]}")
        return 1
    H = {"Authorization": f"Bearer {tok}"}
    import sqlite3
    conn = sqlite3.connect("tda_auth.db")
    uid = conn.execute(
        "SELECT id FROM users WHERE username='admin' LIMIT 1").fetchone()[0]
    conn.close()
    print("logged in as admin")

    # ── GET the table ────────────────────────────────────────────────
    r = await client.get("/api/v1/assurance/bindings", headers=H)
    check("GET /v1/assurance/bindings → 200", r.status_code == 200, str(r.status_code))
    d = await r.get_json()
    rows = d.get("rows", [])
    bound = [x for x in rows if x.get("bound")]
    # Pins updated 2026-08-25: 79fb4a2f (citation_fidelity, 2026-08-18) added two
    # assessors (`cited_but_unread`, `fabricated_identifier`) and their properties
    # (+6 lane rows, +4 shipped bindings, +2 modules) without moving these pins —
    # this suite was not in the trust battery, so it sat red for a week unseen.
    # It is in the battery now; a pin that moves must name its commit here.
    check("…serves the whole table incl. unbound rows", len(rows) == 36, str(len(rows)))
    check("…with every shipped binding bound (11 modules across 21 lane rows)",
          len(bound) == 21, str(len(bound)))
    check("…and lists every registered module", len(d.get("modules", [])) == 11,
          str(len(d.get("modules", []))))
    check("…each carrying its builtin flag and trust state",
          all("builtin" in m and "trust" in m for m in d["modules"]))
    check("…a builtin module reads state 'builtin' (trusted by shipping)",
          d["modules"][0]["trust"]["state"] == "builtin")
    # The PROPERTY, not the contents: pinning the exact set froze the map at two entries
    # and broke the moment the in-flight seam made more properties genuinely consulted —
    # while the thing that matters (every declared consumer names a real gate, and the
    # seam-consulted subset is distinguished) held perfectly. `test_assurance_substrate`
    # pins the map against the gates' own source; this asserts it reaches the studio.
    _cons = d.get("inflight_consumers", {})
    check("…the in-flight consumption map is served, and is not empty",
          isinstance(_cons, dict) and len(_cons) >= 2, str(_cons))
    check("…naming a real gate for each",
          all(v in {"grounding_gate", "honesty_gate", "outbound_guard", "inbound_guard"}
              for v in _cons.values()), str(_cons))
    check("…and the seam-consulted subset is served apart from it",
          set(d.get("seam_consulted", [])) <= set(_cons),
          f"seam={d.get('seam_consulted')} consumers={sorted(_cons)}")
    check("…symmetry is served per property",
          d["symmetry"]["answer_support"]["state"] == "shared")
    check("…and nothing is drifting on a clean install", d.get("drift") == [])
    check("…an unbound-but-meaningful lane is PRESENT, not omitted",
          any(not x["bound"] and x["meaningful"] for x in rows))
    check("…and a by-design-absent lane carries its own note",
          any(x.get("note") for x in rows if not x["meaningful"]))

    # ── candidates ───────────────────────────────────────────────────
    r = await client.get(
        "/api/v1/assurance/bindings/candidates?property=answer_support&lane=POST_HOC",
        headers=H)
    check("GET candidates → 200", r.status_code == 200)
    c = (await r.get_json()).get("candidates", [])
    check("…offers the property's own modules", len(c) >= 1)
    check("…each marked eligible or blocked with reasons",
          all("eligible" in x and "blocked_by" in x for x in c))

    r = await client.get(
        "/api/v1/assurance/bindings/candidates?property=tool_authority&lane=IN_FLIGHT",
        headers=H)
    c2 = (await r.get_json()).get("candidates", [])
    blocked = [x for x in c2 if not x["eligible"]]
    check("a lane a property does not have yields BLOCKED candidates, not an empty list",
          len(blocked) >= 1, json.dumps(c2)[:200])
    check("…naming the reason verbatim from conformance",
          any("no IN_FLIGHT lane" in " ".join(x["blocked_by"]) for x in blocked),
          json.dumps([x["blocked_by"] for x in blocked])[:200])

    r = await client.get(
        "/api/v1/assurance/bindings/candidates?property=nope&lane=POST_HOC", headers=H)
    check("an unknown property is a 400, not a silent empty list", r.status_code == 400)

    # ── write: refuse an invalid bind ────────────────────────────────
    r = await client.post("/api/v1/assurance/bindings", headers=H, json={
        "property": "answer_support", "lane": "POST_HOC", "assessor": "obda_lanes"})
    check("binding a module for a DIFFERENT property → 409", r.status_code == 409,
          str(r.status_code))
    body = await r.get_json()
    check("…with the refusal reasons named",
          any("assesses" in x for x in body.get("reasons", [])),
          json.dumps(body)[:200])

    r = await client.post("/api/v1/assurance/bindings", headers=H, json={
        "property": "answer_support", "lane": "NOPE", "assessor": "corpus_support"})
    check("an unknown lane → 400", r.status_code == 400)

    # ── write: an explicit unbind, then restore ──────────────────────
    r = await client.delete(
        "/api/v1/assurance/bindings/context_use/POST_HOC?restore_default=false", headers=H)
    check("DELETE (explicit unbind) → 200", r.status_code == 200, str(r.status_code))
    d2 = await r.get_json()
    check("…the row now reads NOT ASSESSED", d2["row"]["bound"] is False,
          json.dumps(d2["row"]))
    r = await client.get("/api/v1/assurance/bindings", headers=H)
    d3 = await r.get_json()
    check("…and the override is visible in the served overrides",
          any(o["property"] == "context_use" and o["assessor_key"] is None
              for o in d3["overrides"]))
    check("…and symmetry now reports the gap rather than 'shared'",
          d3["symmetry"]["context_use"]["state"] != "shared",
          d3["symmetry"]["context_use"]["state"])

    r = await client.delete(
        "/api/v1/assurance/bindings/context_use/POST_HOC?restore_default=true", headers=H)
    check("DELETE (restore default) → 200", r.status_code == 200)
    d4 = await r.get_json()
    check("…the shipped module is back",
          d4["row"]["assessor"] == "content_word_overlap", json.dumps(d4["row"]))
    r = await client.get("/api/v1/assurance/bindings", headers=H)
    d5 = await r.get_json()
    check("…and no override row remains",
          not any(o["property"] == "context_use" for o in d5["overrides"]))
    check("…and symmetry is restored", d5["symmetry"]["context_use"]["state"] == "shared")

    # ── audit trail ──────────────────────────────────────────────────
    from trusted_data_agent.core import trust_events as te
    evs = [e for e in te.list_events(uid, include_acked=True, limit=50)
           if e.get("event_type") == "assurance_binding_changed"]
    check("every binding change left an audit event", len(evs) >= 2, str(len(evs)))
    check("…recorded as a RECEIPT (an act taken, not work owed)",
          te.is_receipt("assurance_binding_changed"))

    # ── controls: the machinery that ACTS ────────────────────────────
    from trusted_data_agent.agent.assurance import controls as _ctl
    from trusted_data_agent.agent.assurance import store as _cstore
    _ctl.register(_ctl.Control(
        key="restpack.guard", label="Rest guard", station="guarded",
        attaches_to="outbound_answer", fires_on="restpack.thing", act="alter"))
    # This gate writes to the REAL auth DB, so it must arrive at a known state rather than
    # inherit its own last run's — the first run of this section passed and the second
    # failed on a posture it had persisted itself.
    _cstore.clear_control_posture("restpack.guard")

    r = await client.get("/api/v1/assurance/bindings", headers=H)
    d = await r.get_json()
    row = next((c for c in d.get("controls") or [] if c["key"] == "restpack.guard"), None)
    check("the control is served with the bindings", row is not None,
          json.dumps([c.get("key") for c in d.get("controls") or []]))
    check("…DORMANT, and marked as nobody's decision yet",
          row and row["posture"] == "observe" and row["configured"] is False, json.dumps(row))
    check("…so it acts on nothing", row and row["acts_now"] is None)
    check("…and it says what it WOULD do, in words",
          row and "alter" in str(row.get("act_words")), json.dumps(row))

    r = await client.post("/api/v1/assurance/controls/restpack.guard/posture",
                          headers=H, json={"posture": "enforce"})
    check("POST posture → 200", r.status_code == 200, await r.get_data(as_text=True))
    d = await r.get_json()
    check("…the control now acts", d.get("acts_now") == "alter", json.dumps(d))
    check("…and the response says what CHANGED, not what was picked",
          "alter the answer" in str(d.get("effect")), json.dumps(d))

    r = await client.get("/api/v1/assurance/bindings", headers=H)
    d = await r.get_json()
    row = next(c for c in d["controls"] if c["key"] == "restpack.guard")
    check("…the reading persists, and is now a recorded decision",
          row["posture"] == "enforce" and row["configured"] is True, json.dumps(row))

    r = await client.post("/api/v1/assurance/controls/restpack.guard/posture",
                          headers=H, json={"posture": "ENFORSE"})
    check("an unrecognised posture is REFUSED rather than stored", r.status_code == 400,
          await r.get_data(as_text=True))
    check("…and the refusal names the real ladder",
          "enforce" in (await r.get_json()).get("message", ""))
    r = await client.get("/api/v1/assurance/bindings", headers=H)
    row = next(c for c in (await r.get_json())["controls"] if c["key"] == "restpack.guard")
    check("…leaving the previous decision intact", row["posture"] == "enforce")

    r = await client.post("/api/v1/assurance/controls/nope.nope/posture",
                          headers=H, json={"posture": "enforce"})
    check("an unknown control is a 404, not a silent no-op", r.status_code == 404)

    _cstore.clear_control_posture("restpack.guard")
    _ctl.unregister("restpack")

    print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
