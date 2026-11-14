"""PROP-009 increment 3 — the evidence index, the read model and its REST seam.

CI-pure: a temp SQLite store, the real index / read model / chain code, fakes only for the
live "now" resolution (the agent's current composition and per-kind content hashes). The REST
section runs the real blueprint in a Quart test app with the user and session seams faked.

  §A  The index: one content-free record per turn with a BOM, one row per part it used (incl.
      data products read and actions performed); a re-save replaces, never duplicates; a turn
      without a BOM is not indexed; junk never raises; usage counts answers and "since its
      current version"; another owner's turns are never counted in a caller's view.
  §B  The read model: "when answered" comes ONLY from the sealed BOM — a later re-sign or edit
      does not move it — while "now" is resolved live; the status word follows the hashes
      (unchanged / changed since / not found now / no longer used); absence is said (a turn
      before evidence recording reads "parts not recorded"; a deleted session reads from the
      index record); the summary is honest and worst-first.
  §C  The chain: the view verifies the turn's own chain and content; an edited BOM stamp makes
      the turn read invalid.
  §D  The wiring: the persistence chokepoint indexes the turn; Evidence registers only beside
      the Trust Membrane; every route reads with the caller's own uuid.
  §E  REST: 401 without a user, 404 for a turn the caller does not have, 200 for its own,
      scope=system refused without VIEW_ALL_SESSIONS.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.core.trust import store as S  # noqa: E402
from trusted_data_agent.core.trust import evidence as EV  # noqa: E402
from trusted_data_agent.core.trust import evidence_index as IX  # noqa: E402
from trusted_data_agent.core.trust import evidence_view as V  # noqa: E402
from trusted_data_agent.core.trust import content as CT  # noqa: E402
from trusted_data_agent.core.trust.attestation import IngredientRef, TrustState  # noqa: E402
from trusted_data_agent.core import provenance as P  # noqa: E402

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    S._MIGRATED_PATHS.discard(path)
    IX._READY.discard(path)
    return path


OWNER = "user-a"
OTHER = "user-b"


def _bom(skill_hash="hs1", onto_hash="ho1", skill_state="trusted"):
    return {
        "version": 1, "profile_id": "p1", "profile_tag": "AG", "owner_uuid": OWNER,
        "resolved": "turn_start", "resolved_at": "2026-10-08T10:00:00+00:00", "badge": "trusted",
        "agent": {"kind": "agent", "artifact_id": "p1", "label": "AG", "live_hash": "ha1",
                  "attested_hash": "ha1", "history_id": None, "state": "trusted",
                  "badge": "trusted"},
        "deployment": {"kind": "agent_deployment", "artifact_id": "p1", "label": "deployment",
                       "live_hash": "hd1", "attested_hash": "hd1", "history_id": None,
                       "state": "trusted", "required": True,
                       "summary": {"provider": "Google", "model": "m", "tools": 3}},
        "parts": [
            {"kind": "ontology", "artifact_id": "kg1", "label": "Fitness", "live_hash": onto_hash,
             "attested_hash": onto_hash, "history_id": None, "state": "trusted"},
            {"kind": "skill", "artifact_id": "sk1", "label": "sql", "live_hash": skill_hash,
             "attested_hash": skill_hash if skill_state == "trusted" else None,
             "history_id": None, "state": skill_state},
        ],
    }


def _turn(turn=1, bom=None, obda=None, actions=None):
    t = {"turn": turn, "timestamp": f"2026-10-08T10:0{turn}:00+00:00",
         "provenance_meta": {"chain_tip_hash": f"tip{turn}", "key_fingerprint": "fp"}}
    if bom is not None:
        t["bom"] = bom
    if obda is not None:
        t["obda"] = obda
    if actions is not None:
        t["actions"] = actions
    return t


def _session(*turns):
    return {"last_turn_data": {"workflow_history": list(turns)}}


# ── §A the index ──────────────────────────────────────────────────────────────

def t_index():
    db = _tmp_db()
    obda = {"operations": [{"product": "sales", "version": "1", "request_hash": "r",
                            "sql_hash": "q", "row_count": 4, "trust_state": "trusted"}]}
    acts = [{"action": "close_lead", "version": "1", "kg_id": "kg1", "manifest_hash": "mh",
             "outcome": "performed", "rows_affected": 1, "posture": "enforce"}]
    t1 = _turn(1, _bom(), obda=obda, actions=acts)
    check("A1 a turn with a BOM is indexed", IX.record_turn(OWNER, "s1", t1, db_path=db))
    rec = IX.turn_record(OWNER, "s1", 1, db_path=db)
    check("A2 the record carries chain tip, key and the BOM", rec and rec["chain_tip_hash"] == "tip1"
          and rec["key_fingerprint"] == "fp" and rec["bom"]["profile_tag"] == "AG", rec)
    import sqlite3
    con = sqlite3.connect(db)
    rows = con.execute("SELECT role, kind, artifact_id FROM evidence_parts WHERE session_id='s1'").fetchall()
    con.close()
    roles = sorted(r[0] for r in rows)
    check("A3 one row per part: agent, deployment, ingredients, data, act",
          roles == ["act", "agent", "data", "deployment", "ingredient", "ingredient"], roles)
    check("A4 a data product is identified by the agent's graph",
          ("data", "data_product", "kg1::sales") in rows, rows)
    check("A5 an action is identified by its graph", ("act", "action_type", "kg1::close_lead") in rows)
    check("A6 the record holds no answer text",
          "final_summary_text" not in (rec.get("bom") or {}) and "answer" not in str(rec["bom"]))
    IX.record_turn(OWNER, "s1", t1, db_path=db)
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM evidence_parts WHERE session_id='s1'").fetchone()[0]
    nt = con.execute("SELECT COUNT(*) FROM evidence_turns WHERE session_id='s1'").fetchone()[0]
    con.close()
    check("A7 a re-save replaces, never duplicates", n == 6 and nt == 1, (n, nt))
    check("A8 a turn without a BOM is not indexed",
          IX.record_turn(OWNER, "s2", _turn(1), db_path=db) is False
          and IX.turn_record(OWNER, "s2", 1, db_path=db) is None)
    check("A9 junk never raises", IX.record_turn(OWNER, "s3", "junk", db_path=db) is False
          and IX.record_turn("", "s3", t1, db_path=db) is False
          and IX.record_turn(OWNER, "s3", {"bom": {}}, db_path=db) is False)

    # usage: two owner turns on the skill (two versions), one other-owner turn
    IX.record_turn(OWNER, "s4", _turn(1, _bom(skill_hash="hs2")), db_path=db)
    IX.record_turn(OTHER, "s5", _turn(1, _bom(skill_hash="hs2")), db_path=db)
    u = IX.usage("skill", "sk1", owner_uuid=OWNER, current_hash="hs2", db_path=db)
    check("A10 usage counts the owner's answers", u["answers"] == 2, u)
    check("A11 'since its current version' counts only the current hash", u["since_current"] == 1, u)
    check("A12 another owner's turns are never in the caller's view",
          all(t["session_id"] != "s5" for t in u["turns"]))
    ua = IX.usage("skill", "sk1", owner_uuid=None, current_hash="hs2", db_path=db)
    check("A13 the system view counts every owner", ua["answers"] == 3 and ua["since_current"] == 2, ua)
    check("A14 usage lists newest first",
          [t["session_id"] for t in u["turns"]] == ["s4", "s1"], u["turns"])
    check("A15 an unknown artefact reads zero, never raises",
          IX.usage("skill", "nope", owner_uuid=OWNER, db_path=db)["answers"] == 0)


# ── §B the read model ─────────────────────────────────────────────────────────

class _Now:
    """Fake the live side: the agent's current composition and per-kind content hashes."""

    def __init__(self, refs, agent_live="ha1", agent_att="ha1", dep_hash="hd1",
                 leaf=None, agent_missing=False):
        self.refs, self.agent_live, self.agent_att = refs, agent_live, agent_att
        self.dep_hash, self.leaf, self.agent_missing = dep_hash, leaf or {}, agent_missing

    def __enter__(self):
        self.s_cur, self.s_leaf = V._current_agent, CT.live_hash

        def cur(pid, owner, db):
            if self.agent_missing:
                return None
            return {"trust": SimpleNamespace(composition_state=TrustState(
                        "trusted" if self.agent_live == self.agent_att else "changed")),
                    "refs": self.refs, "agent_live": self.agent_live,
                    "agent_attested": self.agent_att,
                    "deployment": {"state": "trusted", "hash": self.dep_hash, "drift": []}}
        V._current_agent = cur
        CT.live_hash = lambda kind, aid: self.leaf.get((kind, aid))
        return self

    def __exit__(self, *a):
        V._current_agent, CT.live_hash = self.s_cur, self.s_leaf


def _item(view, label):
    for g in view["groups"]:
        for e in g["items"]:
            if e["label"] == label:
                return e
    return None


def t_view():
    db = _tmp_db()
    refs_same = [IngredientRef("ontology", "kg1", "ho1", "ho1", label="Fitness"),
                 IngredientRef("skill", "sk1", "hs1", "hs1", label="sql")]
    sess = _session(_turn(1, _bom()))
    with _Now(refs_same):
        v = V.turn_view(OWNER, "s1", 1, sess, db_path=db)
    check("B1 a recorded turn reads recorded", v and v["recorded"] is True)
    check("B2 groups in tree order",
          [g["key"] for g in v["groups"]] == ["agent", "deployment", "ingredients"],
          [g["key"] for g in v["groups"]])
    check("B3 an untouched part reads unchanged", _item(v, "sql")["status"] == "unchanged")
    check("B4 the summary says all signed", v["summary"]["words"] == "4 parts · all signed when answered"
          and v["summary"]["attention"] == "pass", v["summary"])
    check("B5 the deployment carries its summary",
          _item(v, "deployment").get("summary", {}).get("model") == "m")

    # the skill was edited (and re-signed) AFTER the answer: then stays, now moves
    refs_moved = [IngredientRef("ontology", "kg1", "ho1", "ho1", label="Fitness"),
                  IngredientRef("skill", "sk1", "hs9", "hs9", label="sql")]
    with _Now(refs_moved):
        v2 = V.turn_view(OWNER, "s1", 1, sess, db_path=db)
    e = _item(v2, "sql")
    check("B6 'when answered' is the sealed BOM, never re-resolved",
          e["then"]["live_hash"] == "hs1" and e["then"]["state"] == "trusted", e["then"])
    check("B7 'now' is resolved live", e["now"]["live_hash"] == "hs9")
    check("B8 a part edited since reads 'changed since'", e["status"] == "changed_since")
    check("B9 the summary names the changed part",
          v2["summary"]["changed_since"] == ["sql"] and "1 changed since" in v2["summary"]["words"]
          and v2["summary"]["attention"] == "warn", v2["summary"])

    # the skill was removed from the agent: still found via its own content hash
    with _Now([refs_same[0]], leaf={("skill", "sk1"): "hs1"}):
        v3 = V.turn_view(OWNER, "s1", 1, sess, db_path=db)
    check("B10 a part the agent no longer uses says so", _item(v3, "sql")["status"] == "no_longer_used",
          _item(v3, "sql"))
    # the skill was deleted
    with _Now([refs_same[0]], leaf={}):
        v4 = V.turn_view(OWNER, "s1", 1, sess, db_path=db)
    check("B11 a part that cannot be found now says 'not found now', never unsigned",
          _item(v4, "sql")["status"] == "not_found" and _item(v4, "sql")["now"]["state"] == "not_found")
    check("B12 the summary counts parts not found",
          v4["summary"]["not_found_now"] == ["sql"], v4["summary"])
    # the agent profile was deleted
    with _Now([], agent_missing=True, leaf={("ontology", "kg1"): "ho1", ("skill", "sk1"): "hs1"}):
        v5 = V.turn_view(OWNER, "s1", 1, sess, db_path=db)
    check("B13 a deleted agent reads 'not found now'", _item(v5, "AG")["status"] == "not_found")
    check("B14 its ingredients are still resolved directly",
          _item(v5, "sql")["status"] == "unchanged", _item(v5, "sql"))

    # a part that was not signed when answered
    sess_u = _session(_turn(1, _bom(skill_state="unsigned")))
    with _Now([refs_same[0], IngredientRef("skill", "sk1", "hs1", None, label="sql")]):
        v6 = V.turn_view(OWNER, "s1", 1, sess_u, db_path=db)
    check("B15 an unsigned part is named in the summary",
          v6["summary"]["not_signed_when_answered"] == ["sql"]
          and "1 not signed-and-intact when answered" in v6["summary"]["words"], v6["summary"])

    sess_dep = _session(_turn(1, {**_bom(), "deployment": {**_bom()["deployment"], "state": "unsigned",
                                                              "attested_hash": None}}))
    with _Now(refs_same):
        vd = V.turn_view(OWNER, "s1", 1, sess_dep, db_path=db)
    check("B15b the deployment is named in words, never the bare word",
          vd["summary"]["not_signed_when_answered"] == ["the deployment surface"], vd["summary"])

    # absence is said
    with _Now(refs_same):
        v7 = V.turn_view(OWNER, "s1", 1, _session(_turn(1)), db_path=db)
    check("B16 a turn before evidence recording reads 'parts not recorded'",
          v7["recorded"] is False and v7["note"] == V.NOT_RECORDED and v7["groups"] == []
          and v7["summary"]["attention"] == "na", v7)
    check("B17 no turn and no record → None (the route's 404)",
          V.turn_view(OWNER, "nope", 1, None, db_path=db) is None)

    # session deleted: read from the content-free index record
    IX.record_turn(OWNER, "gone", _turn(2, _bom()), db_path=db)
    with _Now(refs_same):
        v8 = V.turn_view(OWNER, "gone", 2, None, db_path=db)
    check("B18 a deleted session reads from the index record",
          v8 and v8["recorded"] and v8["session"]["available"] is False
          and v8["session"]["note"] == V.SESSION_GONE, v8 and v8.get("session"))
    check("B19 its chain tip is still reported", v8["chain"]["chain_tip_hash"] == "tip2")
    check("B20 another owner cannot read it from the index",
          V.turn_view(OTHER, "gone", 2, None, db_path=db) is None)

    # data products and actions join from their own receipts
    obda = {"operations": [{"product": "sales", "version": "1", "row_count": 4,
                            "trust_state": "trusted"}]}
    acts = [{"action": "close_lead", "version": "1", "kg_id": "kg1", "manifest_hash": "mh",
             "outcome": "performed", "rows_affected": 1, "posture": "enforce"}]
    sess_d = _session(_turn(1, _bom(), obda=obda, actions=acts))
    with _Now(refs_same, leaf={("data_product", "kg1::sales"): "dp1",
                               ("action_type", "kg1::close_lead"): "mh"}):
        v9 = V.turn_view(OWNER, "s1", 1, sess_d, db_path=db)
    check("B21 data read and acts performed join the tree",
          [g["key"] for g in v9["groups"]][-2:] == ["data", "acts"], [g["key"] for g in v9["groups"]])
    check("B22 a data product reports what it returned", _item(v9, "sales")["then"]["row_count"] == 4)
    check("B23 an act reports the rows it wrote", _item(v9, "close_lead")["then"]["rows_affected"] == 1)


# ── §C the chain ─────────────────────────────────────────────────────────────

def t_chain():
    db = _tmp_db()
    bom = _bom()
    tok = EV._TURN_BOM.set(copy.deepcopy(bom))
    try:
        ch = P.ProvenanceChain(session_id="s1", turn_number=1, user_uuid=OWNER,
                               profile_type="llm_only")
        ch.add_step("query_intake", "hello", "q")
        ch.add_step("turn_complete", "the answer", "a")
        sealed = ch.finalize()
    finally:
        EV._TURN_BOM.reset(tok)
    turn = {"turn": 1, "bom": bom, "final_summary_text": "the answer", "user_query": "hello",
            **sealed}
    refs = [IngredientRef("ontology", "kg1", "ho1", "ho1", label="Fitness"),
            IngredientRef("skill", "sk1", "hs1", "hs1", label="sql")]
    with _Now(refs):
        v = V.turn_view(OWNER, "s1", 1, _session(turn), db_path=db)
    check("C1 a sealed turn verifies", v["chain"]["valid"] is True
          and v["chain"]["chain_valid"] is True, v["chain"])
    check("C2 the key fingerprint and its status are reported",
          v["chain"].get("key_fingerprint") and v["chain"].get("key_status") is not None, v["chain"])
    bad = copy.deepcopy(turn)
    bad["bom"]["parts"][1]["live_hash"] = "forged"
    with _Now(refs):
        vb = V.turn_view(OWNER, "s1", 1, _session(bad), db_path=db)
    check("C3 an edited BOM stamp reads invalid, and says why",
          vb["chain"]["valid"] is False and any("trust.bom" in e for e in vb["chain"]["errors"]),
          vb["chain"])
    check("C4 a turn without a chain says so",
          V._chain({"turn": 1})["valid"] is None)


# ── §D wiring ─────────────────────────────────────────────────────────────────

def _src(rel):
    return (ROOT / rel).read_text()


def t_wiring():
    sm = _src("src/trusted_data_agent/core/session_manager.py")
    i = sm.index("async def update_last_turn_data")
    body = sm[i:sm.index("\nasync def ", i + 10)]
    check("D1 the chokepoint indexes the turn", "evidence_index" in body and "record_turn(user_uuid, session_id, turn_data)" in body)
    tree = ast.parse(body)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "bom" in ast.unparse(node.test):
            if "record_turn" in ast.unparse(node):
                found = True
    check("D2 only a turn carrying a BOM is indexed (guarded by the bom test)", found)

    mn = _src("src/trusted_data_agent/main.py")
    j = mn.index("def _register_optional_enterprise_suite")
    reg = mn[j:mn.index("\ndef ", j + 10)]
    t2 = ast.parse(reg)
    guarded = False
    for node in ast.walk(t2):
        if isinstance(node, ast.If) and ast.unparse(node.test) == "trust_ok" \
                and "evidence_bp" in ast.unparse(node):
            guarded = True
    check("D3 Evidence registers only beside the Trust Membrane", guarded)

    rt = ast.parse(_src("src/trusted_data_agent/api/evidence_routes.py"))
    fns = {n.name: n for n in ast.walk(rt) if isinstance(n, ast.AsyncFunctionDef)}
    tv = ast.unparse(fns["get_turn_evidence"])
    check("D4 the turn route loads the caller's own session",
          "get_session(user.id, session_id)" in tv)
    check("D5 the turn route reads with the caller's own uuid",
          "_view.turn_view, user.id" in tv)
    us = ast.unparse(fns["get_artifact_usage"])
    check("D6 usage reads the caller's own unless scope=system is granted",
          "None if system else user.id" in us and "_can_view_all(user)" in us)


# ── §E REST ───────────────────────────────────────────────────────────────────

def t_rest():
    try:
        from quart import Quart
    except Exception:  # noqa: BLE001
        print("  (quart unavailable — REST section skipped)")
        return
    from trusted_data_agent.api import evidence_routes as R
    from trusted_data_agent.core import session_manager as SM
    db = _tmp_db()
    saved_db = S._db_path
    S._db_path = lambda p=None: db
    state = {"user": SimpleNamespace(id=OWNER), "view_all": False}
    s_user, s_all, s_get = R._current_user, R._can_view_all, SM.get_session
    R._current_user = lambda: state["user"]
    R._can_view_all = lambda u: state["view_all"]
    sessions = {(OWNER, "s1"): _session(_turn(1, _bom()))}

    async def get_session(uid, sid):
        return sessions.get((uid, sid))
    SM.get_session = get_session
    app = Quart("t")
    app.register_blueprint(R.evidence_bp, url_prefix="/api")
    refs = [IngredientRef("ontology", "kg1", "ho1", "ho1", label="Fitness"),
            IngredientRef("skill", "sk1", "hs1", "hs1", label="sql")]

    async def go():
        c = app.test_client()
        r = await c.get("/api/v1/evidence/turn/s1/1")
        check("E1 own turn → 200", r.status_code == 200, r.status_code)
        body = await r.get_json()
        check("E2 the body is the read model", body.get("recorded") is True and body.get("groups"))
        r = await c.get("/api/v1/evidence/turn/s9/1")
        check("E3 a turn the caller does not have → 404", r.status_code == 404)
        state["user"] = SimpleNamespace(id=OTHER)
        r = await c.get("/api/v1/evidence/turn/s1/1")
        check("E4 another user's turn → 404", r.status_code == 404)
        state["user"] = None
        r = await c.get("/api/v1/evidence/turn/s1/1")
        check("E5 no user → 401", r.status_code == 401)
        state["user"] = SimpleNamespace(id=OWNER)
        r = await c.get("/api/v1/evidence/artifact/skill/sk1/usage?scope=system")
        check("E6 scope=system without VIEW_ALL_SESSIONS → 403", r.status_code == 403)
        state["view_all"] = True
        r = await c.get("/api/v1/evidence/artifact/skill/sk1/usage?scope=system")
        check("E7 scope=system with the grant → 200", r.status_code == 200)
        r = await c.get("/api/v1/evidence/artifact/skill/sk1/usage")
        b = await r.get_json()
        check("E8 the caller's own usage hides owner ids", r.status_code == 200
              and all("owner_uuid" not in t for t in b["turns"]) and b["scope"] == "mine", b)
    try:
        IX.record_turn(OWNER, "s1", _turn(1, _bom()), db_path=db)
        with _Now(refs, leaf={("skill", "sk1"): "hs1"}):
            asyncio.run(go())
    finally:
        R._current_user, R._can_view_all, SM.get_session = s_user, s_all, s_get
        S._db_path = saved_db


if __name__ == "__main__":
    for fn in (t_index, t_view, t_chain, t_wiring, t_rest):
        try:
            fn()
        except Exception as e:  # a raising section is a NAMED failure, never a dead run
            import traceback
            traceback.print_exc()
            FAILED += 1
            print(f"  FAIL: {fn.__name__} raised {e!r}")
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
