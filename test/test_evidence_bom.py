"""PROP-009 increment 2 — the bill of materials sealed into every turn.

CI-pure: a temp SQLite store, the real composition code with its I/O seams replaced by
faithful fakes, the real ProvenanceChain and verify_content. No server, no network.

  §A  The canonical form is deterministic and ignores key order and internal keys, so the
      seal and the verification can never disagree about the same BOM.
  §B  `turn_evidence` is ONE resolution: its integrity stamp equals what
      `resolve_profile_trust` reports, and its BOM records every part — agent, deployment,
      each ingredient — with the live hash, the attested hash, the history row that matched
      and the state. An unresolvable agent yields (None, None), never a guess.
  §C  The chain seals a `trust.bom` step when (and only when) the turn has a BOM;
      verify_content re-derives it from the turn's `bom` stamp; an edited stamp or a missing
      one is a NAMED mismatch; a chain without the step (every turn before PROP-009)
      verifies exactly as before.
  §D  The BOM survives a later re-sign: the history row it references still resolves to the
      hash the turn used.
  §E  Concurrent turns keep their own BOM (contextvar isolation).
  §F  The wiring: executor.run and the genie coordinator resolve through `turn_evidence`
      (one resolution), set the BOM before the chain is sealed, and reset it at turn start;
      the persistence chokepoint stamps `bom`.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.core.trust import store as S  # noqa: E402
from trusted_data_agent.core.trust import evidence as EV  # noqa: E402
from trusted_data_agent.core.trust import composition as C  # noqa: E402
from trusted_data_agent.core.trust.attestation import IngredientRef  # noqa: E402
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
    return path


# ── §A canonical form ────────────────────────────────────────────────────────

def t_canonical():
    a = {"version": 1, "parts": [{"kind": "skill", "artifact_id": "s"}], "agent": {"x": 1, "y": 2}}
    b = {"agent": {"y": 2, "x": 1}, "parts": [{"artifact_id": "s", "kind": "skill"}], "version": 1}
    check("A1 key order does not move the digest", EV.bom_digest(a) == EV.bom_digest(b))
    c = copy.deepcopy(a)
    c["_sealed"] = True
    c["agent"]["_scratch"] = 1
    check("A2 internal keys do not move the digest", EV.bom_digest(a) == EV.bom_digest(c))
    d = copy.deepcopy(a)
    d["parts"][0]["artifact_id"] = "t"
    check("A3 any content change moves the digest", EV.bom_digest(a) != EV.bom_digest(d))
    content = EV.bom_step_content(a)
    check("A4 step content fits the chain's content cap", len(content) < P.CONTENT_MAX_LEN)
    big = {"version": 1, "parts": [{"kind": "skill", "artifact_id": str(i), "live_hash": "h" * 64}
                                   for i in range(400)]}
    check("A5 a very large BOM still binds fully (digest, not a truncated prefix)",
          EV.bom_step_content(big) != EV.bom_step_content(
              {**big, "parts": big["parts"][:-1] + [{**big["parts"][-1], "live_hash": "z"}]}))
    check("A6 all_parts flattens agent, deployment and ingredients",
          len(EV.all_parts({"agent": {}, "deployment": {}, "parts": [{}, {}]})) == 4)
    check("A7 all_parts tolerates junk", EV.all_parts(None) == [] and EV.all_parts("x") == [])


# ── §B turn_evidence ─────────────────────────────────────────────────────────

class _Fakes:
    """Replace composition's I/O seams with deterministic fakes; restore on exit."""

    def __init__(self, db, refs, dep):
        self.db, self.refs, self.dep = db, refs, dep
        self.saved = {}

    def __enter__(self):
        for name, fn in (
            ("agent_ingredient_refs", lambda pid, uid, profile=None, db_path=None: [
                IngredientRef(r.kind, r.artifact_id, r.live_hash,
                              S.attested_hash(r.kind, r.artifact_id, db_path=self.db,
                                              owner_uuid=uid),
                              unresolved=r.unresolved, label=r.label) for r in self.refs]),
            ("resolve_deployment", lambda pid, uid, profile=None, db_path=None: dict(self.dep)),
            ("profile_id_to_tag", lambda uid: {}),
        ):
            self.saved[name] = getattr(C, name)
            setattr(C, name, fn)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(C, k, v)


def t_turn_evidence():
    db = _tmp_db()
    try:
        prof = {"id": "p1", "tag": "@IDGGT", "profile_type": "llm_only"}
        refs = [IngredientRef("skill", "sk1", "hs1", None, label="SQL expert"),
                IngredientRef("ontology", "kg1", "ho1", None, label="Fitness"),
                IngredientRef("knowledge", "7", "", None, unresolved=True, label="Docs"),
                # unresolved, but its stale hash equals the attested one: must still read
                # changed (cannot show it is intact), never trusted
                IngredientRef("planner", "8", "hp", None, unresolved=True, label="Champions")]
        dep = {"state": "unsigned", "required": True, "drift": [], "hash": "dh1",
               "signature": {"llm": {"provider": "Google", "model": "gemini-x"},
                             "catalog": {"tools": ["a", "b"], "prompts": ["p"]},
                             "connectors": [{"server_id": "c", "opted_in": True}]}}
        hid_sk = S.record_attestation("skill", "sk1", "hs1", signed=True, signature="s",
                                      db_path=db, event="sign")
        S.record_attestation("ontology", "kg1", "OLD", signed=True, signature="s",
                             db_path=db, event="sign")
        S.record_attestation("planner", "8", "hp", signed=True, signature="s",
                             db_path=db, event="sign")
        with _Fakes(db, refs, dep):
            stamp, bom = C.turn_evidence("p1", "alice", profile=prof, db_path=db)
            trust = C.resolve_profile_trust("p1", "alice", profile=prof, db_path=db)
        check("B1 the stamp is the badge's state (one resolution, one rule)",
              stamp == {"state": trust.state.value}, f"{stamp} vs {trust.state}")
        check("B2 a BOM is produced", isinstance(bom, dict))
        parts = {p["artifact_id"]: p for p in bom["parts"]}
        check("B3 every ingredient recorded", set(parts) == {"sk1", "kg1", "7", "8"})
        check("B4 parts are in a stable order",
              [p["kind"] for p in bom["parts"]] == sorted(p["kind"] for p in bom["parts"]))
        check("B5 a signed, intact skill: trusted, its history row named",
              parts["sk1"]["state"] == "trusted" and parts["sk1"]["history_id"] == hid_sk
              and parts["sk1"]["attested_hash"] == "hs1")
        check("B6 a changed ontology: changed, both hashes recorded",
              parts["kg1"]["state"] == "changed" and parts["kg1"]["live_hash"] == "ho1"
              and parts["kg1"]["attested_hash"] == "OLD")
        check("B7 an unresolved repository is marked unresolved, never trusted",
              parts["7"].get("unresolved") is True and parts["7"]["state"] != "trusted")
        check("B7 an unresolved part whose stale hash matches is CHANGED, never trusted",
              parts["8"]["state"] == "changed", parts["8"])
        check("B8 labels carried", parts["kg1"]["label"] == "Fitness")
        check("B9 the agent part carries its composition hash and tag",
              bom["agent"]["live_hash"] and bom["agent"]["label"] == "@IDGGT"
              and bom["agent"]["state"] == "unsigned" and bom["agent"]["history_id"] is None)
        check("B10 deployment part: hash, state and a readable summary (no credentials)",
              bom["deployment"]["live_hash"] == "dh1" and bom["deployment"]["state"] == "unsigned"
              and bom["deployment"]["summary"] == {"provider": "Google", "model": "gemini-x",
                                                    "tools": 2, "prompts": 1, "connectors": 1})
        check("B11 owner and profile recorded; resolved at turn start",
              bom["owner_uuid"] == "alice" and bom["profile_id"] == "p1"
              and bom["resolved"] == "turn_start" and bom["resolved_at"])
        # agent signed by alice → BOM names the agent's history row
        S.record_attestation("agent", "p1", bom["agent"]["live_hash"], signed=True,
                             signature="s", db_path=db, owner_uuid="alice", event="sign")
        with _Fakes(db, refs, dep):
            stamp2, bom2 = C.turn_evidence("p1", "alice", profile=prof, db_path=db)
            _, bom_bob = C.turn_evidence("p1", "bob", profile=prof, db_path=db)
        check("B12 a signed agent: its own signature intact and its history row named",
              bom2["agent"]["state"] == "trusted" and isinstance(bom2["agent"]["history_id"], int))
        check("B12 …while its badge still says an ingredient is not green (no double report)",
              bom2["agent"]["badge"] != "trusted" and bom2["badge"] == stamp2["state"])
        check("B13 another account's turn does not borrow that signature",
              bom_bob["agent"]["state"] == "unsigned" and bom_bob["agent"]["history_id"] is None)
        # unresolvable agent
        saved = C._load_profile
        C._load_profile = lambda *a, **k: None
        try:
            check("B14 an unresolvable agent yields (None, None)",
                  C.turn_evidence("p1", "alice", db_path=db) == (None, None))
        finally:
            C._load_profile = saved
        check("B15 turn_integrity_stamp is turn_evidence's stamp",
              "turn_evidence(" in ast.unparse(
                  next(n for n in ast.walk(ast.parse(Path(C.__file__).read_text()))
                       if isinstance(n, ast.FunctionDef) and n.name == "turn_integrity_stamp")))
        # §D the BOM survives a re-sign of the skill
        S.record_attestation("skill", "sk1", "hs2", signed=True, signature="s2", db_path=db,
                             event="sign")
        old = S.history_entry(parts["sk1"]["history_id"], db_path=db)
        check("D1 the history row a BOM names still resolves to the hash the turn used",
              old and old["version_hash"] == "hs1")
        check("D2 …while the current attestation moved on",
              S.attested_hash("skill", "sk1", db_path=db) == "hs2")
    finally:
        os.unlink(db)


# ── §C chain seal + verification ─────────────────────────────────────────────

def _sealed_chain(bom):
    EV.set_turn_bom(bom)
    try:
        ch = P.ProvenanceChain("sess", 1, "alice", "llm_only")
        ch.add_step("query_intake", "hello", "q")
        ch.add_step("turn_complete", "answer", "done")
        return ch.finalize()
    finally:
        EV.set_turn_bom(None)


def t_chain():
    bom = {"version": 1, "profile_id": "p1", "agent": {"kind": "agent", "state": "trusted"},
           "deployment": {"kind": "agent_deployment", "state": "trusted"},
           "parts": [{"kind": "skill", "artifact_id": "s", "state": "trusted"}]}
    prov = _sealed_chain(copy.deepcopy(bom))
    types = [s["step_type"] for s in prov["provenance_chain"]]
    check("C1 a BOM is sealed as a trust.bom step", types.count("trust.bom") == 1, types)
    v = P.verify_chain(prov)
    check("C2 the chain with the BOM step verifies", v.get("valid") is True, v)
    turn = {"user_query": "hello", "final_summary": "answer", "bom": copy.deepcopy(bom)}
    vc = P.verify_content(prov, turn)
    check("C3 the BOM step is re-derived from the turn stamp",
          vc.get("valid") is not False and not vc.get("mismatches"), vc)
    tampered = copy.deepcopy(turn)
    tampered["bom"]["parts"][0]["state"] = "changed"
    vc2 = P.verify_content(prov, tampered)
    check("C4 an edited BOM stamp is a named mismatch",
          any("trust.bom" in m for m in vc2.get("mismatches", [])), vc2)
    missing = {k: v for k, v in turn.items() if k != "bom"}
    vc3 = P.verify_content(prov, missing)
    check("C5 a sealed BOM with no stamp is a named mismatch",
          any("carries none" in m for m in vc3.get("mismatches", [])), vc3)
    prov0 = _sealed_chain(None)
    check("C6 no BOM ⇒ no step", all(s["step_type"] != "trust.bom" for s in prov0["provenance_chain"]))
    vc0 = P.verify_content(prov0, {"user_query": "hello", "final_summary": "answer"})
    check("C7 a turn without a BOM verifies exactly as before", not vc0.get("mismatches"), vc0)
    sealed_bom = copy.deepcopy(bom)
    EV.set_turn_bom(sealed_bom)
    try:
        ch = P.ProvenanceChain("sess", 2, "alice", "llm_only")
        ch.finalize()
        ch2 = P.ProvenanceChain("sess", 2, "alice", "llm_only")
        again = ch2.finalize()
    finally:
        EV.set_turn_bom(None)
    check("C8 one BOM is sealed once even if two chains finalize in one turn",
          all(s["step_type"] != "trust.bom" for s in again["provenance_chain"]))


# ── §E contextvar isolation ──────────────────────────────────────────────────

def t_isolation():
    async def turn(tag, delay):
        EV.set_turn_bom({"profile_id": tag})
        await asyncio.sleep(delay)
        return (EV.current_turn_bom() or {}).get("profile_id")

    async def main():
        return await asyncio.gather(turn("a", 0.02), turn("b", 0.0))
    check("E1 concurrent turns keep their own BOM", asyncio.run(main()) == ["a", "b"])


# ── §F wiring ────────────────────────────────────────────────────────────────

def _fn(path, name):
    tree = ast.parse((ROOT / path).read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def t_wiring():
    run = _fn("src/trusted_data_agent/agent/executor.py", "run")
    src = ast.unparse(run)
    check("F1 executor.run resolves through turn_evidence", "turn_evidence(" in src)
    check("F1 executor.run no longer resolves twice (no separate integrity stamp)",
          "turn_integrity_stamp(" not in src)
    i_reset = src.find("set_turn_bom(None)")
    i_set = src.find("set_turn_bom(_bom)")
    check("F2 the BOM is reset at turn start, then set", 0 <= i_reset < i_set, (i_reset, i_set))
    g = (ROOT / "src/trusted_data_agent/agent/engines/coordinate_engine.py").read_text()
    i_ev = g.find("turn_evidence(")
    i_fin = g.find("_provenance_data = _genie_provenance.finalize()")
    check("F3 genie resolves its BOM BEFORE sealing its chain", 0 < i_ev < i_fin, (i_ev, i_fin))
    i_gset = g.find("_evidence.set_turn_bom(_genie_bom)")
    check("F3 genie SETS its BOM before sealing", 0 < i_gset < i_fin, (i_gset, i_fin))
    check("F3 genie resolves once (no separate integrity stamp)",
          "turn_integrity_stamp(" not in g)
    upd = ast.unparse(_fn("src/trusted_data_agent/core/session_manager.py", "update_last_turn_data"))
    check("F4 the persistence chokepoint reads the BOM", "current_turn_bom()" in upd)
    # structural, not textual: the stamp sits under a REAL condition (a text search
    # survives `if False:` — mutation-proven)
    fn = _fn("src/trusted_data_agent/core/session_manager.py", "update_last_turn_data")
    guards = [n for n in ast.walk(fn) if isinstance(n, ast.If)
              and any(isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "setdefault"
                      and c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == "bom"
                      for b in n.body for c in ast.walk(b))]
    inner = max(guards, key=lambda g: g.lineno) if guards else None   # the innermost guard
    check("F4 the BOM stamp is guarded by the BOM itself, not a constant",
          inner is not None and isinstance(inner.test, ast.Name),
          ast.unparse(inner.test) if inner is not None else None)
    fin = ast.unparse(_fn("src/trusted_data_agent/core/provenance.py", "finalize"))
    check("F5 finalize seals the BOM through the one content function",
          "bom_step_content(" in fin and "BOM_STEP_TYPE" in fin)
    vc = ast.unparse(_fn("src/trusted_data_agent/core/provenance.py", "verify_content"))
    check("F6 verification re-derives through the SAME function", "bom_step_content(" in vc)


# ── §G the delivered answer verifies on every engine ─────────────────────────

def _answer_chain(answer):
    EV.set_turn_bom(None)
    ch = P.ProvenanceChain("s", 1, "u", "llm_only")
    ch.add_step("turn_complete", answer, "done")
    return ch.finalize()


def t_answer_field():
    ans = "The answer is 42."
    prov = _answer_chain(ans)
    v = P.verify_content(prov, {"final_summary": ans})
    check("G1 Optimize's field (final_summary) verifies", v["verified"] == 1 and not v["mismatches"])
    v = P.verify_content(prov, {"final_summary_text": ans, "final_summary_html": "<p>x</p>"})
    check("G2 the other engines' field (final_summary_text) verifies",
          v["verified"] == 1 and not v["mismatches"], v)
    v = P.verify_content(prov, {"final_summary_text": "The answer is 43."})
    check("G3 an altered answer is still a named mismatch",
          any("turn_complete" in m for m in v["mismatches"]), v)
    long_ans = "x" * 900
    prov2 = _answer_chain(long_ans)
    v = P.verify_content(prov2, {"final_response": long_ans[:500]})
    check("G4 a legacy record that kept only 500 characters is NOT CHECKABLE, never tampering",
          v["skipped"] == 1 and not v["mismatches"], v)
    v = P.verify_content(_answer_chain("short"), {"final_response": "short"})
    check("G5 a legacy short answer still verifies", v["verified"] == 1, v)


def main():
    for fn in (t_canonical, t_turn_evidence, t_chain, t_isolation, t_wiring, t_answer_field):
        try:
            fn()
        except Exception as e:
            global FAILED
            FAILED += 1
            import traceback
            print(f"  FAIL: {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
