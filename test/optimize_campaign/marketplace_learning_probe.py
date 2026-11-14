#!/usr/bin/env python3
"""Live probe: an agent's OWN learning repository through the marketplace (OC-F5, option B).

What it proves, on a running server, with throwaway artifacts only (every one is removed at
the end, also on failure):

  M1  a NEW publisher agent gets its own repository; one real turn is learned into it
  M2  packing the agent ships its configuration, NOT its learning repository
  M3  a consumer who SUBSCRIBES gets an agent with its OWN repository (consumer-owned, empty);
      the publisher's lessons are not visible to the consumer
  M4  a subscription SYNC keeps the consumer agent's id, so its repository (and what it
      learned) survives
  M5  a consumer who FORKS gets another agent with its own repository, distinct from the
      subscribed one
  M6  publishing the learning repository ITSELF is an explicit act; a consumer's FORK of it is
      an ordinary repository (no agent link)
  M7  unsubscribing / uninstalling removes the consumer agents AND their repositories; the
      publisher's repository is never touched by anything the consumer does
  M8  no agent's trust state moved because of learning

Run:  python3 test/optimize_campaign/marketplace_learning_probe.py      (server on :5050)
"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agents as OC  # noqa: E402  (the campaign's transport + state)

req, login = OC.req, OC.login
ROOT = OC.ROOT
DB = os.path.join(ROOT, "tda_auth.db")
STAMP = str(int(time.time()))
PASS = FAIL = 0
CLEANUP = []
REPOS_BEFORE = set()


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def sql(q, args=()):
    c = sqlite3.connect(DB)
    try:
        rows = c.execute(q, args).fetchall()
        c.commit()          # an UPDATE that is not committed is silently discarded
        return rows
    finally:
        c.close()


def learning(token, pid):
    return req("GET", f"/api/v1/profiles/{pid}/learning-repository", token)


def ask(token, pid, prompt):
    sid = req("POST", "/api/v1/sessions", token, {}).get("session_id")
    tid = req("POST", f"/api/v1/sessions/{sid}/query", token, {"prompt": prompt, "profile_id": pid}).get("task_id")
    for _ in range(100):
        d = req("GET", f"/api/v1/tasks/{tid}", token)
        if d.get("status") in ("complete", "failed", "error"):
            return d.get("status")
        time.sleep(3)
    return "timeout"


def case_count(token, cid):
    d = req("GET", f"/api/v1/rag/collections/{cid}/rows?limit=50", token)
    return d.get("total", len(d.get("rows") or []))


def trust_state(token, tag):
    for a in req("GET", "/api/v1/trust/agents", token).get("agents") or []:
        if a.get("tag") == tag:
            return a.get("state")
    return None


def activate_first(token, pid):
    r = req("POST", f"/api/v1/profiles/{pid}/activate", token, {}, timeout=600)
    if r.get("needs_reclassification"):
        req("POST", f"/api/v1/profiles/{pid}/reclassify", token, {}, timeout=900)
        r = req("POST", f"/api/v1/profiles/{pid}/activate", token, {}, timeout=600)
    active = (req("GET", "/api/v1/profiles", token).get("active_for_consumption_profile_ids") or [])
    want = [pid] + [a for a in active if a != pid]
    return req("POST", "/api/v1/profiles/set_active_for_consumption", token, {"profile_ids": want}, timeout=600)


def main():
    admin = login()
    st = OC.load_state()
    base_pid = (st.get("profiles") or {}).get("OCFL")
    if not base_pid:
        sys.exit("run agents.py ensure first (the probe clones @OCFL's spec)")
    profiles = req("GET", "/api/v1/profiles", admin).get("profiles") or []
    base = next(p for p in profiles if p.get("id") == base_pid)
    REPOS_BEFORE.update(r[0] for r in sql("SELECT id FROM collections"))

    print("\n── M1 a new publisher agent learns into its own repository")
    pub_tag = f"OCMKT{STAMP[-5:]}"
    pub = {k: v for k, v in base.items() if k not in ("id", "classification_results", "needs_reclassification",
                                                      "componentsEnabled", "skillsApplied", "is_default")}
    pub.update({"id": f"profile-ocmkt-{STAMP}", "tag": pub_tag, "name": "Marketplace learning probe · publisher",
                "ragCollections": [], "autocompleteCollections": []})
    r = req("POST", "/api/v1/profiles", admin, pub)
    check("publisher agent created", r.get("status") == "success", r)
    CLEANUP.append(("profile", admin, pub["id"]))
    lr = learning(admin, pub["id"])
    rp = (lr.get("repository") or {}).get("id")
    check("…with its own repository at creation", lr.get("mode") == "own" and rp, lr)
    activate_first(admin, pub["id"])
    req("POST", f"/api/v1/trust/agent/{pub['id']}/sign-off", admin, {"note": "marketplace probe"})
    req("POST", f"/api/v1/trust/agent/{pub['id']}/attest-deployment", admin, {})
    signed = trust_state(admin, pub_tag)
    status = ask(admin, pub["id"], "How many products are there in fitness_db?")
    for _ in range(30):
        if case_count(admin, rp) >= 1:
            break
        time.sleep(2)
    check("one real turn is learned into it", status == "complete" and case_count(admin, rp) >= 1,
          (status, case_count(admin, rp)))
    check("M8 …and the publisher stayed signed through the lesson",
          signed == "trusted" and trust_state(admin, pub_tag) == "trusted", (signed, trust_state(admin, pub_tag)))

    print("\n── M2 the pack ships the agent, not what it learned")
    pk = req("POST", "/api/v1/agent-packs/create-and-install", admin,
             {"profile_ids": [pub["id"]], "name": f"Learning probe pack {STAMP}", "description": "throwaway"}, timeout=600)
    inst = pk.get("installation_id") or (pk.get("installation") or {}).get("id")
    check("pack created and installed", bool(inst), json.dumps(pk)[:300])
    if inst:
        CLEANUP.insert(0, ("pack", admin, inst))
    shipped = sql("SELECT resource_type, resource_id FROM agent_pack_resources WHERE pack_installation_id = ?", (inst,)) if inst else []
    check("the learning repository is not among the pack's resources",
          inst and not any(t == "collection" and str(i) == str(rp) for t, i in shipped), shipped)
    pub_listing = req("POST", f"/api/v1/agent-packs/{inst}/publish", admin, {"visibility": "public"}) if inst else {}
    mkt = pub_listing.get("marketplace_pack_id") or pub_listing.get("id")
    if not mkt and inst:
        row = sql("SELECT id FROM marketplace_agent_packs WHERE source_installation_id = ?", (inst,))
        mkt = row[0][0] if row else None
    check("pack published", bool(mkt), json.dumps(pub_listing)[:200])
    if mkt:
        CLEANUP.insert(0, ("listing", admin, mkt))

    print("\n── M3 a subscriber gets its own, empty repository — never the publisher's lessons")
    uname = f"ocmkt_{STAMP}"
    u = req("POST", "/api/v1/admin/users", admin, {"username": uname, "email": f"{uname}@test.local",
                                                   "password": "ocmkt-probe-1", "profile_tier": "user"})
    uid = (u.get("user") or {}).get("id")
    check("consumer user created", bool(uid), json.dumps(u)[:200])
    CLEANUP.append(("user", admin, uid))
    sql("UPDATE users SET email_verified = 1 WHERE id = ?", (uid,))
    con, why = None, None
    for wait in (0, 5, 15, 30):          # the login rate limiter trips fast
        time.sleep(wait)
        lg = req("POST", "/api/v1/auth/login", body={"username": uname, "password": "ocmkt-probe-1"})
        con, why = lg.get("token"), lg
        if con:
            break
    check("consumer logged in", bool(con), json.dumps(why)[:200])
    req("POST", "/api/v1/llm/configurations", con, {"id": f"ocmkt-llm-{STAMP}", "provider": "anthropic",
                                                    "name": "probe LLM", "model": "claude-sonnet-4-6", "credentials": {}})
    host, port = OC.DEFAULT_MCP.split(":")
    ms = req("POST", "/api/v1/mcp/servers", con, {"name": "probe MCP", "host": host, "port": int(port),
                                                   "path": "/mcp", "transport": {"type": "http"}})
    con_mcp = ms.get("id") or (ms.get("server") or {}).get("id")
    if not con_mcp:
        con_mcp = next((s.get("id") for s in req("GET", "/api/v1/mcp/servers", con).get("servers") or []), None)
    sub = req("POST", f"/api/v1/marketplace/agent-packs/{mkt}/install", con,
              {"llm_configuration_id": f"ocmkt-llm-{STAMP}", "mcp_server_id": con_mcp}, timeout=600)
    check("consumer subscribed", sub.get("status") == "success" or sub.get("profiles_added"), json.dumps(sub)[:300])
    cprofs = [p for p in req("GET", "/api/v1/profiles", con).get("profiles") or [] if p.get("tag") == pub_tag]
    sub_pid = cprofs[0]["id"] if cprofs else None
    check("…the subscribed agent exists in the consumer's account", bool(sub_pid))
    sl = learning(con, sub_pid) if sub_pid else {}
    rs = (sl.get("repository") or {}).get("id")
    owner = sql("SELECT owner_user_id, learning_profile_id FROM collections WHERE id = ?", (rs,)) if rs else []
    check("…with its OWN repository, owned by the consumer, linked to the consumer's agent",
          sl.get("mode") == "own" and rs and rs != rp and owner and owner[0] == (uid, sub_pid), (sl, owner))
    check("…empty: the publisher's lessons do not travel", rs and case_count(con, rs) == 0)
    cvis = {c.get("id") for c in req("GET", "/api/v1/rag/collections?light=true", con).get("collections") or []}
    check("the publisher's learning repository is not visible to the consumer", rp not in cvis)

    print("\n── M4 a subscription sync keeps the agent — and its repository")
    sy = req("POST", f"/api/v1/marketplace/agent-packs/{mkt}/sync", con, {}, timeout=600)
    after = [p for p in req("GET", "/api/v1/profiles", con).get("profiles") or [] if p.get("tag") == pub_tag]
    check("sync ran", sy.get("status") in ("success", "unchanged", None) or "synced" in json.dumps(sy), json.dumps(sy)[:200])
    check("…same agent id after sync", after and after[0]["id"] == sub_pid, [p["id"] for p in after])
    check("…same repository after sync", (learning(con, sub_pid).get("repository") or {}).get("id") == rs)

    print("\n── M5 a fork is a second agent with its own repository")
    fk = req("POST", f"/api/v1/marketplace/agent-packs/{mkt}/fork", con,
             {"llm_configuration_id": f"ocmkt-llm-{STAMP}", "mcp_server_id": con_mcp, "conflict_strategy": "expand"},
             timeout=600)
    fprofs = [p for p in req("GET", "/api/v1/profiles", con).get("profiles") or []
              if (p.get("tag") or "").startswith(pub_tag) and p["id"] != sub_pid]
    fk_pid = fprofs[0]["id"] if fprofs else None
    fl = learning(con, fk_pid) if fk_pid else {}
    rf = (fl.get("repository") or {}).get("id")
    check("forked agent created", bool(fk_pid), json.dumps(fk)[:300])
    check("…with its own repository, distinct from the subscribed one and the publisher's",
          fl.get("mode") == "own" and rf and rf not in (rs, rp), fl)
    fork_inst = fk.get("installation_id") or (fk.get("installation") or {}).get("id")

    print("\n── M6 publishing the learning repository itself is explicit; a fork of it is ordinary")
    pr = req("POST", f"/api/v1/rag/collections/{rp}/publish", admin, {"visibility": "public"})
    check("the publisher can publish its agent's repository on purpose", pr.get("status") == "success", json.dumps(pr)[:200])
    fc = req("POST", f"/api/v1/marketplace/collections/{rp}/fork", con,
             {"name": f"forked lessons {STAMP}", "mcp_server_id": con_mcp}, timeout=600)
    fcid = fc.get("collection_id") or fc.get("forked_collection_id") or fc.get("id")
    link = sql("SELECT learning_profile_id, owner_user_id FROM collections WHERE id = ?", (fcid,)) if fcid else []
    check("…the consumer's fork is an ordinary repository (no agent link), owned by the consumer",
          link and link[0][0] in (None, "") and link[0][1] == uid, (fc, link))
    unpub = req("DELETE", f"/api/v1/marketplace/collections/{rp}", admin)  # best effort; may not exist as a route
    sql("UPDATE collections SET is_marketplace_listed = 0, visibility = 'private' WHERE id = ?", (rp,))

    print("\n── M7 removing the consumer's agents removes their repositories, never the publisher's")
    us = req("DELETE", f"/api/v1/marketplace/agent-packs/{mkt}/subscribe", con, timeout=600)
    check("unsubscribed", us.get("status") == "success" or "unsubscribed" in json.dumps(us).lower(), json.dumps(us)[:200])
    check("…the subscribed agent's repository is gone", not sql("SELECT id FROM collections WHERE id = ?", (rs,)))
    if fork_inst:
        req("DELETE", f"/api/v1/agent-packs/{fork_inst}", con, timeout=600)
    else:
        req("DELETE", f"/api/v1/profiles/{fk_pid}", con)
    check("…the forked agent's repository is gone", not sql("SELECT id FROM collections WHERE id = ?", (rf,)))
    check("the publisher's repository and lessons are untouched",
          sql("SELECT id FROM collections WHERE id = ?", (rp,)) and case_count(admin, rp) >= 1)
    check("M8 the publisher is still signed", trust_state(admin, pub_tag) == "trusted")


def _remove_owned_by(user_id):
    if not user_id or not str(user_id):
        return
    name = sql("SELECT username FROM users WHERE id = ?", (user_id,))
    if not (name and str(name[0][0]).startswith("ocmkt_")):   # never anyone but the probe's user
        return
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import shutil
    import chromadb
    from trusted_data_agent.core.collection_db import CollectionDatabase
    db = CollectionDatabase(DB)
    cl = chromadb.PersistentClient(path=os.path.join(ROOT, ".chromadb_rag_cache"))
    for cid, cname in sql("SELECT id, collection_name FROM collections WHERE owner_user_id = ?", (user_id,)):
        try:
            cl.delete_collection(name=cname)
        except Exception:  # noqa: BLE001 — never created in the vector store
            pass
        d = os.path.join(ROOT, "rag", "tda_rag_cases", f"collection_{cid}")
        if os.path.isdir(d):
            shutil.rmtree(d)
        db.delete_collection(int(cid))


def cleanup():
    print("\n── cleanup")
    admin = login()
    for kind, tok, ident in CLEANUP:
        if not ident:
            continue
        if kind == "listing":
            req("DELETE", f"/api/v1/marketplace/agent-packs/{ident}", tok)
        elif kind == "pack":
            req("DELETE", f"/api/v1/agent-packs/{ident}", tok, timeout=600)
        elif kind == "profile":
            req("DELETE", f"/api/v1/profiles/{ident}", tok)
        elif kind == "user":
            # Only THIS probe user's repositories (its default ones cannot be deleted over REST by
            # anyone, and the admin user-delete only deactivates the account): vectors, row, cases.
            _remove_owned_by(ident)
            req("DELETE", f"/api/v1/admin/users/{ident}", admin)
    left = {r[0] for r in sql("SELECT id FROM collections")} - REPOS_BEFORE
    check("nothing the probe created is left behind", not left, left)


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
