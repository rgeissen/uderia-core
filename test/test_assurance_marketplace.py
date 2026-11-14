"""
PROP-003 · Phase 3 — the marketplace, and rubric judges.

The load-bearing assertion is §3: an installed pack arrives BOUND TO NOTHING and still
cannot be bound until it is signed HERE. A marketplace install must grant availability,
never authority — a publisher's signature says "this is what I published", not "your
deployment trusts this inside its answer path".

Drives the real REST stack through Quart's test client. Installs and removes its own
packs; skips cleanly without tda_auth.db.
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


def section(t):
    print(f"\n{'─' * 4} {t} {'─' * max(0, 62 - len(t))}")


PACKS = Path.home() / ".tda" / "assurance_modules"
SRC_PACK, DST_PACK = "mkt_src", "mkt_src"       # install restores the same pack_id


def cleanup():
    for p in (PACKS / SRC_PACK, PACKS / "rubricpack"):
        shutil.rmtree(p, ignore_errors=True)
    try:
        from trusted_data_agent.core import sqlite_util
        c = sqlite_util.connect("tda_auth.db")
        c.execute("DELETE FROM marketplace_assurance_modules WHERE pack_id LIKE 'mkt_%'")
        c.execute("DELETE FROM trust_attestations WHERE kind='assurance_module' "
                  "AND artifact_id LIKE 'mkt_%'")
        c.commit(); c.close()
    except Exception:
        pass
    shutil.rmtree(Path("marketplace_data") / "assurance_modules", ignore_errors=True)


def write(pack_id, manifest):
    d = PACKS / pack_id
    d.mkdir(parents=True, exist_ok=True)
    m = dict(manifest)
    m.setdefault("pack_id", pack_id)
    m.setdefault("display_name", pack_id)
    m.setdefault("version", "1.0.0")
    m.setdefault("description", "a marketplace test pack")
    (d / "manifest.json").write_text(json.dumps(m))
    return d


async def main():
    cleanup()
    write(SRC_PACK, {
        "author": "Test Publisher",
        "properties": [{"key": "tone", "label": "Tone", "question": "Is the tone right?",
                        "lanes": ["POST_HOC"]}],
        "assessors": [{"key": "rule", "property": "tone", "tier": "declarative",
                       "requires": ["answer"],
                       "spec": {"patterns": {"flag": ["\\bawful\\b"]}}}],
    })

    from trusted_data_agent.main import app
    client = app.test_client()
    r = await client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": "admin"})
    tok = (await r.get_json())["token"]
    H = {"Authorization": f"Bearer {tok}"}

    section("1 · the marketplace is never more permissive than the plane")

    await client.post("/api/v1/assurance/modules/settings", headers=H,
                      json={"third_party_assurance_enabled": True,
                            "assurance_module_marketplace_enabled": False})
    r = await client.post(f"/api/v1/assurance-modules/{SRC_PACK}/publish", headers=H,
                          json={"visibility": "public"})
    check("publishing is refused while the marketplace is off", r.status_code == 403,
          str(r.status_code))

    await client.post("/api/v1/assurance/modules/settings", headers=H,
                      json={"third_party_assurance_enabled": False,
                            "assurance_module_marketplace_enabled": True})
    r = await client.get("/api/v1/marketplace/assurance-modules", headers=H)
    check("…and with the PLANE off the marketplace is off too, whatever its own flag says",
          (await r.get_json()).get("disabled") is True)

    section("2 · publish")

    await client.post("/api/v1/assurance/modules/settings", headers=H,
                      json={"third_party_assurance_enabled": True,
                            "assurance_module_marketplace_enabled": True})
    r = await client.post(f"/api/v1/assurance-modules/{SRC_PACK}/publish", headers=H,
                          json={"visibility": "public"})
    pub = await r.get_json()
    check("a user pack publishes", r.status_code == 200 and pub.get("published"),
          f"{r.status_code} {json.dumps(pub)[:200]}")
    mid = pub.get("id")
    check("…recording which tiers it uses, so a browser knows what it is taking on",
          pub.get("tiers") == ["declarative"], str(pub.get("tiers")))

    r = await client.post(f"/api/v1/assurance-modules/{SRC_PACK}/publish", headers=H,
                          json={"visibility": "public"})
    check("…and publishing twice is refused rather than duplicated",
          r.status_code == 409)

    r = await client.post("/api/v1/assurance-modules/no_such_pack/publish", headers=H,
                          json={"visibility": "public"})
    check("a pack that is not installed cannot be published", r.status_code == 404)

    r = await client.get("/api/v1/marketplace/assurance-modules", headers=H)
    listing = [m for m in (await r.get_json())["modules"] if m["id"] == mid]
    check("it appears in the browse list", len(listing) == 1)
    check("…carrying its contribution counts, not just a name",
          listing and listing[0]["property_count"] == 1
          and listing[0]["assessor_count"] == 1, json.dumps(listing[:1]))
    check("…and honestly reports that its publisher had not signed it",
          listing and listing[0]["publisher_signed"] is False)

    section("3 · install grants AVAILABILITY, never AUTHORITY")

    shutil.rmtree(PACKS / SRC_PACK, ignore_errors=True)   # simulate another deployment
    r = await client.post(f"/api/v1/marketplace/assurance-modules/{mid}/install",
                          headers=H)
    inst = await r.get_json()
    check("the pack installs", r.status_code == 200 and inst.get("installed"),
          f"{r.status_code} {json.dumps(inst)[:200]}")
    check("…and is loaded without a restart", inst.get("loaded") is True)
    check("…and the response SAYS it is bound to nothing",
          "bound to nothing" in (inst.get("note") or ""), str(inst.get("note")))

    b = await (await client.get("/api/v1/assurance/bindings", headers=H)).get_json()
    rows = [x for x in b["rows"] if x["property"] == f"{DST_PACK}.tone"]
    check("the installed property is in the binding table", len(rows) == 3)
    check("…BOUND TO NOTHING", not any(x["bound"] for x in rows))

    r = await client.post("/api/v1/assurance/bindings", headers=H, json={
        "property": f"{DST_PACK}.tone", "lane": "POST_HOC",
        "assessor": f"{DST_PACK}.rule"})
    check("…and it CANNOT be bound merely because it came from the marketplace",
          r.status_code == 409, str(r.status_code))
    check("…the refusal naming the local signature that is owed",
          any("sign the pack" in x for x in (await r.get_json()).get("reasons", [])))

    r = await client.post(f"/api/v1/trust/assurance-module/{DST_PACK}/sign-off",
                          headers=H)
    check("signing it HERE is what admits it", (await r.get_json()).get("signed") is True)
    r = await client.post("/api/v1/assurance/bindings", headers=H, json={
        "property": f"{DST_PACK}.tone", "lane": "POST_HOC",
        "assessor": f"{DST_PACK}.rule"})
    check("…and then the bind takes", r.status_code == 200, str(r.status_code))

    section("4 · install integrity")

    blob = Path("marketplace_data") / "assurance_modules" / mid / "pack.json"
    payload = json.loads(blob.read_text())
    payload["manifest"]["assessors"][0]["spec"]["patterns"]["flag"].append("\\bx\\b")
    blob.write_text(json.dumps(payload))
    shutil.rmtree(PACKS / DST_PACK, ignore_errors=True)
    r = await client.post(f"/api/v1/marketplace/assurance-modules/{mid}/install",
                          headers=H)
    check("a payload that no longer matches its recorded hash is REFUSED",
          r.status_code == 409, str(r.status_code))
    check("…and nothing is left on disk from the refused install",
          not (PACKS / DST_PACK).exists())

    section("5 · rating is gated behind a real install")

    r = await client.post(f"/api/v1/marketplace/assurance-modules/{mid}/rate",
                          headers=H, json={"rating": 5})
    # This user DID install earlier, so the gate should let them through.
    check("a user who installed may rate", r.status_code == 200, str(r.status_code))
    r = await client.post(f"/api/v1/marketplace/assurance-modules/{mid}/rate",
                          headers=H, json={"rating": 9})
    check("…and an out-of-range rating is refused", r.status_code == 400)

    section("6 · unpublish")

    r = await client.delete(f"/api/v1/marketplace/assurance-modules/{mid}", headers=H)
    check("the publisher can unpublish", r.status_code == 200)
    r = await client.get("/api/v1/marketplace/assurance-modules", headers=H)
    check("…and the listing is gone",
          not any(m["id"] == mid for m in (await r.get_json())["modules"]))

    section("7 · rubric judges ride the platform's judge layer")

    write("rubricpack", {
        "properties": [{"key": "clarity", "label": "Clarity",
                        "question": "Is the answer clear?",
                        "lanes": ["IN_FLIGHT_TRIGGERED", "POST_HOC"]}],
        "assessors": [{"key": "judge", "property": "clarity", "tier": "declarative",
                       "label": "Clarity rubric", "requires": ["answer"],
                       "spec": {"rubric": {"prompt_template":
                                           "Is this answer clear?\n\n{answer}"}}}],
    })
    await client.post("/api/v1/assurance/modules/settings", headers=H,
                      json={"third_party_assurance_enabled": True})
    from trusted_data_agent.agent.assurance import registry as _reg
    from trusted_data_agent.agent.assurance.capabilities import Lane
    ru = _reg.get("rubricpack.judge")
    check("a rubric assessor loads", ru is not None)
    check("…and CANNOT take the unconditional in-flight lane (it calls a model)",
          ru is not None and Lane.IN_FLIGHT not in ru.lanes(),
          str(sorted(l.name for l in ru.lanes())) if ru else "")
    check("…while the triggered lane remains available, as the ladder intends",
          ru is not None and Lane.IN_FLIGHT_TRIGGERED in ru.lanes())

    from trusted_data_agent.agent.assurance.contract import AssessmentContext
    f = ru.run(AssessmentContext(answer="hello"))
    check("…and it does NOT adjudicate in this lane — it abstains, carrying the prompt",
          bool(f.abstained) and "hello" in ((f.payload or {}).get("rubric_prompt") or ""),
          str(f))

    _reg.bind("rubricpack.clarity", "rubricpack.judge", (Lane.POST_HOC,), validate=False)
    from trusted_data_agent.eval.scorers import plugin_properties as PP
    from trusted_data_agent.eval.types import ScoreKind
    PP.reset_for_tests()
    PP.sync_plugin_scorers([])
    s = PP._INSTALLED.get("rubricpack.clarity")
    check("a bound rubric becomes a JUDGE scorer, not a deterministic one",
          s is not None and s.kind == ScoreKind.JUDGE, str(getattr(s, "kind", None)))
    from trusted_data_agent.eval.scorers.judges import JudgeScorer
    check("…subclassing the platform's own judge, so it inherits the panel AND the "
          "canonical cost sink",
          isinstance(s, JudgeScorer))
    check("…and never gates a turn", s.gating is False)
    check("…while a non-rubric pack property stays deterministic",
          not isinstance(PP.PropertyScorer("x"), JudgeScorer))

    # cleanup
    await client.delete(
        f"/api/v1/assurance/bindings/{DST_PACK}.tone/POST_HOC?restore_default=true",
        headers=H)
    await client.post("/api/v1/assurance/modules/settings", headers=H,
                      json={"third_party_assurance_enabled": False,
                            "assurance_module_marketplace_enabled": False})
    cleanup()

    print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
