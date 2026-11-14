"""update_in_place reinstates the publisher's attestations on the updated copies (2026-09-04).

The reinstate step lived on the fresh-install path only, so every in-place update refreshed
the ontology and corpus and left the consumer's old attestations pointing at the previous
content — four pack agents read `changed` on a consumer right after the publisher had
re-signed the very content they held. ONE method now serves both paths. CI-pure: the
portability layer is faked; the wiring is pinned by AST."""
import ast, os, pathlib, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core.agent_pack_manager import AgentPackManager as M
from trusted_data_agent.core.trust import portability, pack, content

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

class Ref:
    def __init__(self, kind, aid, lh): self.kind, self.artifact_id, self.live_hash = kind, aid, lh

def t_method():
    calls = []
    saved = (portability.reinstate, pack.member_refs, content.live_hash)
    owners_seen = []

    def _reinstate(kind, aid, claim, lh, owner_uuid=None):
        calls.append((kind, aid, lh))
        owners_seen.append((kind, owner_uuid))
    portability.reinstate = _reinstate
    pack.member_refs = lambda iid: ([Ref("ontology", "kg-1", "H-ONT"), Ref("knowledge", "496", "H-KNOW"), Ref("agent", "p-1", "H-OTHER")], "owner")
    content.live_hash = lambda kind, sid: {"skill-a": "H-SKILL"}.get(sid, "H-NONE")
    try:
        manifest = {"member_trust": [{"kind": "ontology", "version_hash": "H-ONT"}, {"kind": "knowledge", "version_hash": "H-KNOW"},
                                     {"kind": "agent", "version_hash": "H-AGENT-PUBLISHER"}, {"kind": "skill", "version_hash": "H-SKILL"}],
                    "skills": [{"skill_id": "skill-a"}, {"skill_id": "skill-b"}]}
        n = M.__new__(M)._reinstate_member_trust(manifest, 89)
        check("a member whose live hash equals the signed hash is reinstated", ("ontology", "kg-1", "H-ONT") in calls and ("knowledge", "496", "H-KNOW") in calls, calls)
        check("a member whose content differs from what was signed is NOT reinstated (honesty gate)", not any(c[1] == "p-1" for c in calls), calls)
        check("a skill is matched by its own content hash", ("skill", "skill-a", "H-SKILL") in calls and not any(c[1] == "skill-b" for c in calls), calls)
        check("the count reports what was reinstated", n == 3, n)
        check("an agent member is reinstated as the PACK OWNER's (owner-scoped, PROP-009)",
              all(o == "owner" for k, o in owners_seen if k != "skill"), owners_seen)
        check("no member_trust in the manifest is a clean no-op", M.__new__(M)._reinstate_member_trust({}, 89) == 0, None)
        pack.member_refs = lambda iid: (_ for _ in ()).throw(RuntimeError("boom"))
        check("a failing lookup never raises", M.__new__(M)._reinstate_member_trust(manifest, 89) == 0, None)
    finally:
        portability.reinstate, pack.member_refs, content.live_hash = saved

SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent/core/agent_pack_manager.py"
def _calls_in(fn):
    for n in ast.walk(ast.parse(SRC.read_text())):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")) for c in ast.walk(n) if isinstance(c, ast.Call)}
    return None

def t_wiring():
    check("the fresh install reinstates through the shared method", "_reinstate_member_trust" in (_calls_in("import_pack") or set()), None)
    check("update_in_place reinstates through the shared method", "_reinstate_member_trust" in (_calls_in("_handle_update_in_place") or set()), None)
    src = SRC.read_text()
    check("no second copy of the reinstate loop survives", src.count("_trust_portability.reinstate(") == 2 and src.count("_by_kv.get(") == 2, (src.count("_trust_portability.reinstate("), src.count("_by_kv.get(")))
    body = src[src.index("async def _handle_update_in_place"):]
    check("in-place: reinstate runs AFTER the copies are updated and BEFORE the commit flag", body.index("_reinstate_member_trust(") > body.index("Step 5i") and body.index("_reinstate_member_trust(") < body.index("commit_succeeded = True"), None)

for fn in (t_method, t_wiring):
    try: fn()
    except Exception as exc:
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
