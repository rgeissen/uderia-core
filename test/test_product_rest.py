"""
LIVE gate — OBDA-READ data-product REST surface (Phase A4) + the Phase-A acceptance path.

Drives the REAL running server end-to-end, in the exact order the scope's Phase-A
acceptance demands (§12): master switch OFF refuses everything (the additive
guarantee) → ON → author on a REAL ontology → M-rule rejection carries its code →
validate (V1–V6, resolver-backed) → sign refused until the ontology is signed (the
bottom-up rule) → sign ontology → product signs 🟢 → the definition + catalog carry
the trust state → switch restored OFF (the baseline default) and the surface refuses
again.

Needs: the server on :5050, admin/admin, and one ontology-conformant KG.
Run: python3 test/test_product_rest.py
"""

import os
import sys

import httpx

BASE = os.environ.get("UDERIA_BASE", "http://localhost:5050/api")

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def main():
    c = httpx.Client(base_url=BASE, timeout=30)
    tok = c.post("/v1/auth/login",
                 json={"username": "admin", "password": "admin"}).json().get("token")
    if not tok:
        print("cannot authenticate — is the server running?")
        sys.exit(1)
    H = {"Authorization": f"Bearer {tok}"}

    def set_obda(on: bool):
        r = c.post("/v1/admin/app-config", headers=H,
                   json={"agent_eval": {"obda_enabled": on}})
        assert r.status_code == 200, f"app-config POST failed: {r.status_code}"

    # a real, ontology-conformant KG to build on (largest = most realistic)
    kgs = c.get("/v1/knowledge-graph/list", headers=H).json().get("knowledge_graphs", [])
    kg = next((g for g in kgs if "Fitness" in (g.get("kg_name") or "")), None) or kgs[0]
    kg_id = kg["kg_id"]
    print(f"target ontology: {kg.get('kg_name')} ({kg_id})")

    print("\n[1] master switch OFF (the default): the whole family refuses, fail-closed")
    set_obda(False)
    r = c.get("/v1/data-products", headers=H, params={"kg_id": kg_id})
    check("catalog refuses with 403 obda_disabled",
          r.status_code == 403 and r.json().get("error") == "obda_disabled")
    r = c.post("/v1/data-products", headers=H, json={"kg_id": kg_id, "manifest": {}})
    check("author refuses when off", r.status_code == 403)
    r = c.post(f"/v1/trust/data-product/{kg_id}/x/sign-off", headers=H)
    check("sign-off refuses when off (signing is part of the family)",
          r.status_code == 403 and r.json().get("error") == "obda_disabled")

    print("\n[2] switch ON: author a product on the real ontology")
    set_obda(True)
    try:
        # pick a real logical entity that RESOLVES to a deployed table
        ents = []
        spec = c.get("/v1/knowledge-graph/graph-spec", headers=H,
                     params={"kg_id": kg_id, "types": "entity", "max_nodes": 60}).json()
        for n in (spec.get("spec") or spec).get("nodes", []):
            if n.get("entity_type") == "entity" or n.get("type") == "entity":
                ents.append(n.get("name") or n.get("id"))
        check("the ontology exposes logical entities to build on", len(ents) > 0,
              f"got {len(ents)}")
        entity = None
        for cand in ents:
            # author probes below will confirm; prefer a simple name
            entity = cand
            if isinstance(cand, str) and cand.islower():
                break
        print(f"  building on entity: {entity!r}")

        manifest = {
            "product": "phase_a_acceptance", "version": 1, "backend": "teradata",
            "description": "OBDA-READ Phase A acceptance product",
            "entities": [{"name": entity, "deployed": "placeholder.tbd",
                          "key": ["id"], "attributes": []}],
            "measures": [{"name": "row_count", "anchor": entity, "expr": "COUNT(*)"}],
        }

        print("\n[3] an M-rule violation is a 400 carrying its machine-readable code")
        bad = dict(manifest)
        bad["entities"] = [dict(manifest["entities"][0], key=[])]
        r = c.post("/v1/data-products", headers=H,
                   json={"kg_id": kg_id, "manifest": bad})
        check("missing grain → 400 missing_key",
              r.status_code == 400 and r.json().get("error") == "missing_key",
              f"{r.status_code} {r.text[:120]}")

        r = c.post("/v1/data-products", headers=H,
                   json={"kg_id": kg_id, "manifest": manifest})
        check("a valid manifest authors (201)", r.status_code in (200, 201),
              f"{r.status_code} {r.text[:200]}")
        check("the product node links its member entity",
              entity in (r.json().get("members_linked") or []),
              str(r.json())[:200])

        print("\n[4] validate: the resolver freezes the deployed identifier (V1)")
        r = c.post("/v1/data-products/phase_a_acceptance/validate", headers=H,
                   json={"kg_id": kg_id})
        check("validate runs and stamps a report", r.status_code == 200,
              f"{r.status_code} {r.text[:200]}")
        rep = r.json().get("report") or {}
        v1 = next((ck for ck in rep.get("checks", []) if ck["id"] == "V1"), {})
        # the placeholder `deployed` will NOT match the resolver → V1 fails honestly,
        # OR the entity doesn't resolve → also a fail. Either way: not signable yet,
        # and the report says exactly why. Then we freeze the REAL identifier.
        resolved = None
        if v1.get("status") == "fail" and "resolver now says" in v1.get("detail", ""):
            resolved = v1["detail"].split("resolver now says ")[-1].split("'")[1]
        if resolved:
            check("V1 caught the placeholder and NAMED the real deployed table",
                  True)
            manifest["entities"][0]["deployed"] = resolved
            r = c.post("/v1/data-products", headers=H,
                       json={"kg_id": kg_id, "manifest": manifest})
            check("re-author with the frozen identifier", r.status_code in (200, 201))
            check("…which DROPPED the stale report (edited manifest = invalid proof)",
                  "re-validate" in (r.json().get("note") or ""))
            r = c.post("/v1/data-products/phase_a_acceptance/validate", headers=H,
                       json={"kg_id": kg_id})
            rep = r.json().get("report") or {}
        signable = bool(rep.get("signable"))
        check("the gate reaches a signable report on the real ontology", signable,
              f"blockers={rep.get('blockers')}")

        print("\n[5] the earned signature: bottom-up, then 🟢")
        r = c.post(f"/v1/trust/data-product/{kg_id}/phase_a_acceptance/sign-off",
                   headers=H)
        ontology_presigned = False
        if r.status_code == 422:
            check("sign refused while the ontology is unsigned (bottom-up rule)",
                  r.json().get("validation", {}).get("status") == "unsigned_ingredient",
                  r.text[:200])
            r2 = c.post(f"/v1/trust/ontology/{kg_id}/sign-off", headers=H)
            check("the ontology signs (conformant)", r2.status_code == 200,
                  f"{r2.status_code} {r2.text[:160]}")
            r = c.post(f"/v1/trust/data-product/{kg_id}/phase_a_acceptance/sign-off",
                       headers=H)
        else:
            ontology_presigned = True
            print("  (ontology already signed on this instance — bottom-up branch "
                  "covered by test_product_trust)")
        check("the product SIGNS", r.status_code == 200 and r.json().get("signed"),
              f"{r.status_code} {r.text[:200]}")
        check("the verdict is verified_deterministic (not mere attestation)",
              r.json().get("validation", {}).get("status") == "verified_deterministic")

        print("\n[6] catalog + definition carry the trust state")
        r = c.get("/v1/data-products", headers=H, params={"kg_id": kg_id})
        prod = next((p for p in r.json().get("products", [])
                     if p["product"] == "phase_a_acceptance"), None)
        check("the catalog lists the product", prod is not None)
        check("…as validated + signable + TRUSTED",
              prod and prod["validated"] and prod["signable"]
              and prod["trust"].get("state") == "trusted", str(prod))
        r = c.get("/v1/data-products/phase_a_acceptance", headers=H,
                  params={"kg_id": kg_id})
        check("the definition serves manifest + report + trust",
              r.status_code == 200 and r.json().get("manifest")
              and r.json().get("validation_report")
              and r.json().get("trust", {}).get("state") == "trusted")

        print("\n[6b] compile + query (Phase B3): governed SQL, REJECTs, the provenance stamp")
        r = c.post("/v1/data-products/phase_a_acceptance/compile", headers=H,
                   json={"kg_id": kg_id,
                         "request": {"measures": ["row_count"], "dimensions": []}})
        check("a valid request compiles to governed SQL", r.status_code == 200
              and (r.json().get("sql") or "").upper().startswith("SELECT"),
              f"{r.status_code} {r.text[:160]}")
        check("…with the request hash (R5) and the trust state",
              len(r.json().get("request_hash") or "") == 64
              and r.json().get("trust", {}).get("state") in
              ("trusted", "changed", "unsigned"))
        r = c.post("/v1/data-products/phase_a_acceptance/compile", headers=H,
                   json={"kg_id": kg_id, "request": {"measures": ["row_cnt"]}})
        check("an unknown measure is a 422 unknown_member with the closest named",
              r.status_code == 422 and r.json().get("error") == "unknown_member"
              and "row_count" in (r.json().get("message") or ""), r.text[:160])
        r = c.get("/v1/data-products/phase_a_acceptance", headers=H,
                  params={"kg_id": kg_id})
        check("the definition serves the admissible map (§3.2)",
              isinstance(r.json().get("admissible"), dict)
              and "grain_matrix" in r.json()["admissible"])
        r = c.post("/v1/data-products/phase_a_acceptance/query", headers=H,
                   json={"kg_id": kg_id,
                         "request": {"measures": ["row_count"], "dimensions": []}})
        if r.status_code == 200:
            check("query returns rows + the governed provenance stamp",
                  r.json().get("columns") and
                  r.json().get("provenance", {}).get("lane") == "governed"
                  and len(r.json()["provenance"].get("request_hash") or "") == 64,
                  r.text[:200])
        else:
            check("query without a connection degrades honestly: 409 WITH the SQL",
                  r.status_code == 409 and (r.json().get("sql") or "")
                  .upper().startswith("SELECT"), f"{r.status_code} {r.text[:160]}")

        print("\n[7] tamper: an edited manifest flips CHANGED and cannot re-sign")
        t = {**manifest, "version": 2}
        r = c.post("/v1/data-products", headers=H, json={"kg_id": kg_id, "manifest": t})
        check("the edit authors (and drops the report)", r.status_code == 200)
        r = c.get("/v1/data-products/phase_a_acceptance", headers=H,
                  params={"kg_id": kg_id})
        check("the badge reads CHANGED against the attestation",
              r.json().get("trust", {}).get("state") == "changed")
        r = c.post(f"/v1/trust/data-product/{kg_id}/phase_a_acceptance/sign-off",
                   headers=H)
        check("re-sign refused: unvalidated (the dropped report — never a rubber stamp)",
              r.status_code == 422
              and r.json().get("validation", {}).get("status") == "unvalidated",
              r.text[:200])

        print("\n[7b] DELETE — the product is removed and its attestation revoked")
        r = c.delete("/v1/data-products/phase_a_acceptance", headers=H,
                     params={"kg_id": kg_id})
        check("DELETE succeeds", r.status_code == 200 and r.json().get("deleted") is True,
              r.text[:120])
        r = c.get("/v1/data-products", headers=H, params={"kg_id": kg_id})
        check("the product is gone from the catalog",
              "phase_a_acceptance" not in [p_["product"] for p_ in r.json().get("products", [])])
        r = c.delete("/v1/data-products/phase_a_acceptance", headers=H,
                     params={"kg_id": kg_id})
        check("a second DELETE is an honest 404", r.status_code == 404)
    finally:
        print("\n[8] the switch restores OFF — the baseline default")
        set_obda(False)
        r = c.get("/v1/data-products", headers=H, params={"kg_id": kg_id})
        check("with the switch back off, the surface refuses again",
              r.status_code == 403)

    print(f"\n{'=' * 68}\n  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
    for f in FAILURES:
        print("  FAIL:", f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
