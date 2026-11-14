"""
PROP-003 · Phase 2 end to end, through the REAL stack.

install → master off (invisible) → master on (loaded, bound to NOTHING) → bind refused
because unsigned → sign-off → bind takes → the generic scorer appears and grades a turn
→ edit the pack → the next bind is refused and the existing one reports DRIFT without
being silently removed.

Drives Quart's test client, so the auth decorators, the trust store and the persistence
are the real ones. Installs its pack under ~/.tda/assurance_modules/livedemo and removes
it (and its attestation) afterwards; it also clears any leftover attestation at START,
because a signature surviving from a previous run would make the bind gate look permissive
when it is not.

Needs tda_auth.db with the default admin account; skips cleanly if it is absent.
"""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)

if not os.path.exists("tda_auth.db"):
    print("SKIP: no tda_auth.db — this gate drives the real auth + trust stack")
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


PACK = Path.home() / ".tda" / "assurance_modules" / "livedemo"


def install_pack():
    PACK.mkdir(parents=True, exist_ok=True)
    (PACK / "manifest.json").write_text(json.dumps({
        "pack_id": "livedemo",
        "display_name": "Live demo · disclaimer",
        "version": "1.0.0",
        "description": "A live end-to-end proof pack for PROP-003.",
        "properties": [{
            "key": "disclaimer",
            "label": "Disclaimer present",
            "question": "Does an answer stating money carry the disclaimer?",
            "lanes": ["IN_FLIGHT", "POST_HOC"],
        }],
        "assessors": [{
            "key": "phrase_rule", "property": "disclaimer", "label": "Phrase rule",
            "tier": "declarative", "requires": ["answer"],
            "subject_is_model_output": True,
            "spec": {"patterns": {"flag": ["\\$\\s?[0-9][0-9,.]*"],
                                  "clear": ["not investment advice"]}},
        }],
        "tunables": [{"key": "max_flags", "property": "disclaimer",
                      "metric": "flag_hits", "gate": 0, "healthy": 0,
                      "higher_is_better": False, "stage": "compose"}],
    }))


def _clear_attestation():
    """Start from a known-unsigned state — a leftover signature from a previous run would
    make the bind gate look permissive when it is not."""
    try:
        from trusted_data_agent.core.trust import store as ts
        ts.retire("assurance_module", "livedemo")
    except Exception:
        pass
    try:
        from trusted_data_agent.core import sqlite_util
        c = sqlite_util.connect("tda_auth.db")
        c.execute("DELETE FROM trust_attestations WHERE kind='assurance_module' "
                  "AND artifact_id='livedemo'")
        c.commit(); c.close()
    except Exception:
        pass


async def main():
    install_pack()
    _clear_attestation()
    from trusted_data_agent.main import app
    client = app.test_client()
    r = await client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": "admin"})
    tok = (await r.get_json())["token"]
    H = {"Authorization": f"Bearer {tok}"}

    # 1 · master OFF ⇒ invisible
    await client.post("/api/v1/assurance/modules/settings", headers=H,
                      json={"third_party_assurance_enabled": False})
    d = await (await client.get("/api/v1/assurance/modules", headers=H)).get_json()
    check("with the master OFF, the installed pack is not loaded",
          not any(p["pack_id"] == "livedemo" for p in d.get("packs", [])))
    b = await (await client.get("/api/v1/assurance/bindings", headers=H)).get_json()
    check("…and its property is absent from the binding table",
          not any(r["property"].startswith("livedemo.") for r in b["rows"]))

    # 2 · master ON ⇒ discovered, bound to nothing
    d = await (await client.post("/api/v1/assurance/modules/settings", headers=H,
                                 json={"third_party_assurance_enabled": True})).get_json()
    check("turning the master ON loads the pack without a restart",
          any(p["pack_id"] == "livedemo" for p in d.get("packs", [])),
          json.dumps(d)[:200])
    pack = next(p for p in d["packs"] if p["pack_id"] == "livedemo")
    check("…contributing a namespaced property",
          pack["properties"] == ["livedemo.disclaimer"], str(pack["properties"]))
    check("…and loading with no problems", pack["problems"] == [], str(pack["problems"]))

    b = await (await client.get("/api/v1/assurance/bindings", headers=H)).get_json()
    rows = [r for r in b["rows"] if r["property"] == "livedemo.disclaimer"]
    check("…appearing in the binding table", len(rows) == 3, str(len(rows)))
    check("…BOUND TO NOTHING — installing is not assigning",
          not any(r["bound"] for r in rows))
    mod = next((m for m in b["modules"] if m["key"] == "livedemo.phrase_rule"), None)
    check("…and the module is listed as third-party", mod and mod["builtin"] is False)
    check("…with an unsigned trust state", mod and mod["trust"]["state"] == "unsigned",
          json.dumps(mod.get("trust") if mod else None))

    # 3 · the bind gate refuses an unsigned module
    r = await client.post("/api/v1/assurance/bindings", headers=H, json={
        "property": "livedemo.disclaimer", "lane": "POST_HOC",
        "assessor": "livedemo.phrase_rule"})
    check("binding an UNSIGNED third-party module is refused", r.status_code == 409,
          str(r.status_code))
    reasons = (await r.get_json()).get("reasons", [])
    check("…naming the act that is owed", any("sign the pack" in x for x in reasons),
          json.dumps(reasons))

    # 4 · sign it for real
    r = await client.post("/api/v1/trust/assurance-module/livedemo/sign-off", headers=H)
    sign = await r.get_json()
    check("the pack signs", r.status_code == 200 and sign.get("signed"),
          f"{r.status_code} {json.dumps(sign)[:200]}")
    check("…as a declarative pack, with no third-party code at all",
          (sign.get("validation") or {}).get("status") == "declarative",
          json.dumps(sign.get("validation")))

    # 5 · now the bind takes
    r = await client.post("/api/v1/assurance/bindings", headers=H, json={
        "property": "livedemo.disclaimer", "lane": "POST_HOC",
        "assessor": "livedemo.phrase_rule"})
    check("a SIGNED module binds", r.status_code == 200, str(r.status_code))
    body = await r.get_json()
    check("…and the row now names it",
          (body.get("row") or {}).get("assessor") == "livedemo.phrase_rule",
          json.dumps(body.get("row")))

    # 6 · the scorer exists, with no pack-supplied eval code
    from trusted_data_agent.eval.scorer import scorers_for
    dims = [s.dimension for s in scorers_for("rag_focused")]
    check("the bound property now has a scorer, written by nobody",
          "livedemo.disclaimer" in dims, str([d for d in dims if "." in d]))

    # …and it grades a turn
    class _Run:
        _t = {"user_query": "q"}

        def final_answer_text(self):
            return "Revenue was $4,200 last quarter."

        def retrieved_documents(self):
            return ()

        def tool_calls(self):
            return ()

        def platform_authored_answer(self):
            return ""

    s = next(x for x in scorers_for("rag_focused")
             if x.dimension == "livedemo.disclaimer")
    v = s.score(None, _Run())
    check("…and it grades a real turn shape", v.passed is False, str(v))
    check("…banding against the pack's own tunable", v.band == "danger", str(v.band))

    # 7 · in-flight is honest: bound, but no gate consults it
    b = await (await client.get("/api/v1/assurance/bindings", headers=H)).get_json()
    check("the studio knows which properties a gate really consults in flight",
          "livedemo.disclaimer" not in b["inflight_consumers"])

    # 8 · editing the pack after signing closes the gate again
    m = json.loads((PACK / "manifest.json").read_text())
    m["assessors"][0]["spec"]["patterns"]["flag"].append("\\bEUR\\b")
    (PACK / "manifest.json").write_text(json.dumps(m))
    r = await client.post("/api/v1/assurance/bindings", headers=H, json={
        "property": "livedemo.disclaimer", "lane": "IN_FLIGHT",
        "assessor": "livedemo.phrase_rule"})
    check("an EDIT after signing refuses the NEXT bind", r.status_code == 409,
          str(r.status_code))
    b = await (await client.get("/api/v1/assurance/bindings", headers=H)).get_json()
    check("…and the existing binding is reported as DRIFTING, not silently removed",
          any(x["property"] == "livedemo.disclaimer" for x in b.get("drift", [])),
          json.dumps(b.get("drift"))[:200])
    still = [r for r in b["rows"]
             if r["property"] == "livedemo.disclaimer" and r["lane"] == "POST_HOC"]
    check("…the property is still being assessed (never auto-unbound)",
          still and still[0]["bound"])

    # cleanup
    await client.delete(
        "/api/v1/assurance/bindings/livedemo.disclaimer/POST_HOC?restore_default=true",
        headers=H)
    await client.post("/api/v1/assurance/modules/settings", headers=H,
                      json={"third_party_assurance_enabled": False})
    shutil.rmtree(PACK, ignore_errors=True)
    try:
        from trusted_data_agent.core.trust import store as ts
        ts.retire("assurance_module", "livedemo")
    except Exception:
        pass

    print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
