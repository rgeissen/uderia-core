"""PROP-009 increment 4 — the estate-wide Evidence list and artefact usage.

CI-pure: a temp SQLite store; the real index, read-model helpers and the route's `_estate`
with only its trust seams (ownership, live state) faked.

  §A  Rows and the summary: a changed signature, a retired key and a row recorded without a
      signature are each flagged; the summary is in words and worst-first; the list sorts
      worst first; an empty estate reads "no signatures", never "all intact".
  §B  Usage counts match the turn index: the one-query counts equal the per-artefact usage
      read, per owner; labels come from what turns recorded.
  §C  `_estate`: an owner sees their own agents and only the artefacts the ownership gate
      grants (fail closed on an ownership error); the system scope sees every account's; a
      deployment row is read through the deployment resolver; a filter narrows the list but
      never the summary; search matches the label.
  §D  Wiring: SIGNED and PROVEN lead with an Evidence route only when the view is present;
      the tab and its section exist only with the suite; the route dispatch reaches the tab
      pre-filtered; the endpoint gates scope=system.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.core.trust import store as S  # noqa: E402
from trusted_data_agent.core.trust import evidence_index as IX  # noqa: E402
from trusted_data_agent.core.trust import evidence_view as V  # noqa: E402

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


def _rec(kind, aid, signed=True, fp="fpA", at=100.0, owner=""):
    return {"kind": kind, "artifact_id": aid, "signed": signed, "signer_fingerprint": fp,
            "signer_uuid": "u-a", "signed_at": at, "history_id": 1, "version_hash": "v",
            "owner_uuid": owner}


# ── §A rows and summary ───────────────────────────────────────────────────────

def t_rows():
    ok = V.estate_row(_rec("skill", "s1"), "trusted", "sql", {"answers": 3, "since_current": 3}, "active")
    ch = V.estate_row(_rec("ontology", "kg1"), "changed", "Fitness", None, "active")
    rk = V.estate_row(_rec("skill", "s2", at=50), "trusted", "notes", None, "retired")
    ns = V.estate_row(_rec("flow", "f1", signed=False), "unsigned", "nightly", None, None)
    check("A1 an intact signature is trusted with no flags", ok["worst"] == "trusted" and ok["flags"] == [])
    check("A2 a changed signature is flagged changed", ch["worst"] == "changed" and "changed" in ch["flags"])
    check("A3 a retired signing key is flagged", rk["worst"] == "retired_key" and "retired_key" in rk["flags"])
    check("A4 a row recorded without a signature is flagged", "not_signed" in ns["flags"] and ns["worst"] == "unsigned")
    check("A5 usage rides on the row", ok["answers"] == 3 and ok["since_current"] == 3)
    s = V.estate_summary([ok, ch, rk, ns])
    check("A6 the summary is in words, worst first",
          s["words"] == "4 signatures · 1 changed since signed · 1 made with a retired key", s)
    check("A7 attention warns when something needs a look", s["attention"] == "warn")
    s2 = V.estate_summary([ok])
    check("A8 an all-intact estate says so", s2["words"] == "1 signature · all intact" and s2["attention"] == "pass", s2)
    s3 = V.estate_summary([])
    check("A9 an empty estate is never 'all intact'", s3["words"] == "0 signatures" and s3["attention"] == "na", s3)
    order = [r["worst"] for r in V.sort_estate([ok, ns, rk, ch])]
    check("A10 the list sorts worst first", order == ["changed", "retired_key", "unsigned", "trusted"], order)
    newer = V.estate_row(_rec("skill", "s3", at=200), "trusted", "x", None, "active")
    check("A11 within one level, newest first",
          [r["artifact_id"] for r in V.sort_estate([ok, newer])] == ["s3", "s1"])


# ── §B usage counts match the index ──────────────────────────────────────────

def _bom(skill_hash):
    return {"version": 1, "profile_id": "p1", "profile_tag": "AG",
            "agent": {"kind": "agent", "artifact_id": "p1", "label": "AG", "live_hash": "ha", "state": "trusted"},
            "parts": [{"kind": "skill", "artifact_id": "sk1", "label": "sql", "live_hash": skill_hash,
                       "state": "trusted"}]}


def t_counts():
    db = _tmp_db()
    for owner, sid, h in (("a", "s1", "h1"), ("a", "s2", "h2"), ("a", "s3", "h2"), ("b", "s4", "h2")):
        IX.record_turn(owner, sid, {"turn": 1, "timestamp": f"2026-10-08T10:0{sid[-1]}:00Z", "bom": _bom(h)},
                       db_path=db)
    for owner in ("a", "b", None):
        c = IX.usage_counts(owner, db_path=db).get(("skill", "sk1"))
        u = IX.usage("skill", "sk1", owner_uuid=owner, current_hash="h2", db_path=db)
        check(f"B1 counts equal the usage read (owner={owner})",
              c and c["answers"] == u["answers"] and c["hashes"].get("h2", 0) == u["since_current"],
              (c, u["answers"], u["since_current"]))
    ca = IX.usage_counts("a", db_path=db)[("skill", "sk1")]
    check("B2 per-version counts", ca["hashes"] == {"h1": 1, "h2": 2}, ca)
    check("B3 another owner is never counted in a caller's view",
          IX.usage_counts("b", db_path=db)[("skill", "sk1")]["answers"] == 1)
    lab = IX.labels("a", db_path=db)
    check("B4 labels come from what turns recorded", lab.get(("skill", "sk1")) == "sql", lab)
    check("B5 junk databases never raise", IX.usage_counts("a", db_path="/nonexistent/dir/x.db") == {}
          and IX.labels("a", db_path="/nonexistent/dir/x.db") == {})


# ── §C _estate ────────────────────────────────────────────────────────────────

def t_estate():
    from trusted_data_agent.api import evidence_routes as R
    from trusted_data_agent.api import trust_routes as TR
    from trusted_data_agent.core.trust import composition as C
    recs = [
        _rec("agent", "p1", owner="me"),
        _rec("agent", "p1", owner="other"),
        _rec("agent_deployment", "p1", owner="me"),
        _rec("ontology", "kg-mine"),
        _rec("ontology", "kg-theirs"),
        _rec("ontology", "kg-broken"),
        _rec("skill", "sk1"),
    ]
    saved = (S.list_current, TR._assert_owner, TR._resolve_artifact_state, TR._artifact_live_hash,
             C.resolve_deployment, IX.labels, IX.usage_counts, R._usernames, R._label_for)

    def list_current(db_path=None, owner_uuid=None, kinds=None):
        return [r for r in recs if owner_uuid is None or r["kind"] not in S.OWNER_SCOPED_KINDS
                or r["owner_uuid"] == owner_uuid]

    def assert_owner(kind, aid, user):
        if aid == "kg-broken":
            raise RuntimeError("lookup failed")
        if aid == "kg-theirs":
            return False, "not yours", 403
        return True, "", 200

    calls = {"dep": 0}

    def resolve_deployment(aid, owner, profile=None, db_path=None):
        calls["dep"] += 1
        return {"state": "changed", "hash": "dh"}

    S.list_current = list_current
    TR._assert_owner = assert_owner
    TR._resolve_artifact_state = lambda kind, aid, user_uuid=None: "changed" if aid == "kg-mine" else "trusted"
    TR._artifact_live_hash = lambda kind, aid, user_uuid=None: "live-" + aid
    C.resolve_deployment = resolve_deployment
    IX.labels = lambda owner=None, db_path=None: {("skill", "sk1"): "sql"}
    IX.usage_counts = lambda owner=None, db_path=None: {("skill", "sk1"): {"answers": 4, "hashes": {"live-sk1": 3, "old": 1}}}
    R._usernames = lambda uuids: {"u-a": "admin"}
    R._label_for = lambda kind, aid, acting, names, tag_maps: names.get((kind, aid)) or aid
    try:
        out = R._estate("me", False, None, None, None)
        ids = {(r["kind"], r["artifact_id"]) for r in out["rows"]}
        check("C1 an owner sees their own agent, never another account's",
              sum(1 for r in out["rows"] if r["kind"] == "agent") == 1)
        check("C2 another user's artefact is not listed", ("ontology", "kg-theirs") not in ids)
        check("C3 an ownership error fails closed (not listed)", ("ontology", "kg-broken") not in ids)
        check("C4 own artefacts and platform-wide kinds are listed",
              ("ontology", "kg-mine") in ids and ("skill", "sk1") in ids, ids)
        dep = next(r for r in out["rows"] if r["kind"] == "agent_deployment")
        check("C5 a deployment row is read through the deployment resolver",
              dep["state_now"] == "changed" and calls["dep"] >= 1)
        sk = next(r for r in out["rows"] if r["kind"] == "skill")
        check("C6 usage and since-current come from the index", sk["answers"] == 4 and sk["since_current"] == 3, sk)
        check("C7 the signer is named", sk["signer_name"] == "admin")
        check("C8 worst first", out["rows"][0]["worst"] == "changed")
        check("C9 the caller's view carries no owner ids", all("owner_uuid" not in r for r in out["rows"]))
        filt = R._estate("me", False, None, "changed", None)
        check("C10 a filter narrows the list, never the summary",
              all(r["worst"] == "changed" or "changed" in r["flags"] for r in filt["rows"])
              and filt["summary"] == out["summary"], (filt["summary"], out["summary"]))
        q = R._estate("me", False, None, None, "SQL")
        check("C11 search matches the label, case-insensitive", [r["artifact_id"] for r in q["rows"]] == ["sk1"])
        k = R._estate("me", False, "ontology", None, None)
        check("C12 a kind filter keeps only that kind", {r["kind"] for r in k["rows"]} == {"ontology"})
        sysv = R._estate("me", True, None, None, None)
        check("C13 the system scope sees every account's agents and artefacts",
              sum(1 for r in sysv["rows"] if r["kind"] == "agent") == 2
              and ("ontology", "kg-theirs") in {(r["kind"], r["artifact_id"]) for r in sysv["rows"]})
        check("C14 the system scope names each row's owner",
              any(r.get("owner_uuid") == "other" for r in sysv["rows"]))
    finally:
        (S.list_current, TR._assert_owner, TR._resolve_artifact_state, TR._artifact_live_hash,
         C.resolve_deployment, IX.labels, IX.usage_counts, R._usernames, R._label_for) = saved


def t_labels():
    from trusted_data_agent.api import evidence_routes as R
    names = {("skill", "sk1"): "sql", ("agent", "p1"): "AG"}
    check("D0a a recorded label names a leaf", R._label_for("skill", "sk1", "me", names, {}) == "sql")
    check("D0b a data product is named by its own name", R._label_for("data_product", "kg1::sales", "me", {}, {}) == "sales")
    tm = {"me": lambda pid: "IDGGT" if pid == "p1" else None}
    check("D0c an agent is named by its @tag, never a recorded bare tag",
          R._label_for("agent", "p1", "me", names, tm) == "@IDGGT")
    check("D0d an unknown agent has no invented name", R._label_for("agent", "p9", "me", {}, tm) is None)


# ── §D wiring ─────────────────────────────────────────────────────────────────

def t_wiring():
    ap = (ROOT / "static/js/handlers/agentPerformanceHandler.js").read_text()
    check("D1 the Evidence section key exists", "'calibration', 'evidence']" in ap)
    check("D2 the section falls back when the view is absent",
          "if (key === 'evidence' && !window.EvidenceView) key = 'circle';" in ap)
    check("D3 showing the tab mounts the estate, pre-filtered",
          "window.EvidenceView.mountEstate(scroller, { filter: (opts && opts.filter) || undefined });" in ap)
    i = ap.index("const signed = _assemble('signed',")
    sig = ap[i:ap.index("_assemble(", i + 40)]
    check("D4 SIGNED leads with Evidence only when the view is present",
          "...(window.EvidenceView ? { execCta:" in sig and "'evidence:changed' : 'evidence'" in sig)
    check("D5 SIGNED keeps the posture matrix route", "action: 'posture-matrix'" in sig)
    check("D6 PROVEN offers Evidence beside the evidence tools",
          "label: 'Open Evidence — what each answer was made with', action: 'evidence'" in ap
          and "action: 'posture-tools'" in ap)
    check("D7 the route dispatch reaches the tab with its filter",
          "showSection('evidence', { filter: action.split(':')[1] || 'all' });" in ap)
    html = (ROOT / "templates/index.html").read_text()
    check("D8 the tab exists only with the suite",
          '{% if trust_membrane_available %}<button class="agent-section-tab ind-tab ind-tab--underline" data-section="evidence">' in html)
    check("D9 the section exists only with the suite",
          '{% if trust_membrane_available %}<div id="agent-section-evidence"' in html)
    rt = (ROOT / "src/trusted_data_agent/api/evidence_routes.py").read_text()
    j = rt.index("async def get_evidence_estate")
    body = rt[j:j + 800]
    check("D10 the estate endpoint gates scope=system", "if system and not _can_view_all(user):" in body)
    tb = (ROOT / "static/js/handlers/trustBadge.js").read_text()
    check("D11 the trust popover fills its usage line from the evidence index",
          "_fillUsage(overlay.querySelector('.tb-usage'), kind, id);" in tb
          and "/api/v1/evidence/artifact/" in tb)


if __name__ == "__main__":
    for fn in (t_rows, t_counts, t_estate, t_labels, t_wiring):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILED += 1
            print(f"  FAIL: {fn.__name__} raised {e!r}")
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
