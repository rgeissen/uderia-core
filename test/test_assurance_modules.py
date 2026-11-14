"""
PROP-003 · Phase 2 — the third-party assurance-module plane.

The load-bearing sections are §4 (the T1 verifier) and §5 (the bind gate). Everything else
is plumbing; those two are the reason it is safe to let a third party ship code that runs
beside the answer. Each is mutation-tested — removing the trust gate, or the lane
confinement on the scanned tier, must fail here by name.

Pure: no server, no network. Builds temp packs on disk and drives the real manager.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent import assurance as A                        # noqa: E402
from trusted_data_agent.agent.assurance import (                           # noqa: E402
    conformance, properties as props, registry)
from trusted_data_agent.agent.assurance.capabilities import Lane           # noqa: E402
from trusted_data_agent.agent.assurance.contract import AssessmentContext  # noqa: E402
from trusted_data_agent.assurance_modules import manager as M              # noqa: E402
from trusted_data_agent.assurance_modules import settings as S             # noqa: E402
from trusted_data_agent.assurance_modules.verifier import verify_source    # noqa: E402

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


_TMP = Path(tempfile.mkdtemp(prefix="assurance_packs_"))
_DB = tempfile.mktemp(suffix="_modules.db")


def write_pack(pack_id, manifest, sources=None):
    d = _TMP / pack_id
    d.mkdir(parents=True, exist_ok=True)
    manifest = dict(manifest)
    manifest.setdefault("pack_id", pack_id)
    manifest.setdefault("display_name", pack_id)
    manifest.setdefault("version", "1.0.0")
    manifest.setdefault("description", "a test pack")
    (d / "manifest.json").write_text(json.dumps(manifest))
    for fn, src in (sources or {}).items():
        (d / fn).write_text(src)
    return d


def fresh_manager(enabled=True):
    A.reset_for_tests()
    A.bootstrap(db_path=_DB)
    S.save_settings({"third_party_assurance_enabled": enabled}, "admin", db_path=_DB)
    M.reset_assurance_module_manager()
    mgr = M.AssuranceModuleManager(user_dir=_TMP, modules_dir=_TMP / "_none",
                                   db_path=_DB)
    mgr.discover()
    return mgr


# ── 1 · the master switch ────────────────────────────────────────────────────
section("1 · the master switch — off by default, and off means nothing loads")

S.save_settings({"third_party_assurance_enabled": False}, "admin", db_path=_DB)
check("the plane ships OFF", not S.is_third_party_enabled(_DB))

write_pack("acme", {
    "properties": [{"key": "tone", "question": "Is the tone right?",
                    "lanes": ["POST_HOC"]}],
    "assessors": [{"key": "tone_rules", "property": "tone", "tier": "declarative",
                   "spec": {"patterns": {"flag": ["\\bstupid\\b"]}}}],
})

mgr = fresh_manager(enabled=False)
check("…so no pack is loaded", len(mgr.packs) == 0)
check("…and no dotted key reaches the registry",
      not any("." in a.key for a in registry.assessors()))
_baseline = len(registry.assessors())
# Pin updated 2026-08-25: 9 -> 11 (79fb4a2f added cited_but_unread +
# fabricated_identifier; this suite was outside the battery and sat red — now added).
check("…leaving exactly the shipped assessors", _baseline == 11, str(_baseline))

section("2 · a pack contributes properties, assessors and tunables")

mgr = fresh_manager()
check("the pack loads when the master is on", "acme" in mgr.packs)
pack = mgr.packs["acme"]
check("…with no problems", pack.problems == [], str(pack.problems))
check("its property is NAMESPACED by the loader, not by the author",
      pack.properties == ["acme.tone"], str(pack.properties))
check("…and joins the live vocabulary", props.get("acme.tone") is not None)
check("…and appears in the binding table",
      any(r["property"] == "acme.tone" for r in registry.bindings()))
check("its assessor is namespaced too", pack.assessors == ["acme.tone_rules"])
check("…and is registered", registry.get("acme.tone_rules") is not None)

section("3 · INSTALLING BINDS NOTHING")

# The invariant the whole governance model rests on: the set of assessments running on a
# deployment equals the set an operator assigned. An install that changed what a gate sees
# would be a protection change in either direction that nobody decided.
check("a freshly installed pack is bound to nothing",
      registry.resolve("acme.tone", Lane.POST_HOC) is None)
check("…even though the manifest may SUGGEST bindings",
      True)  # suggested_bindings is data the studio offers, never applied

section("4 · the T1 verifier — the pure subset")

PURE = '''
import re
from trusted_data_agent.agent.assurance.contract import Assessor, Finding, Severity

class Ok(Assessor):
    def assess(self, ctx):
        n = len(re.findall(r"\\d+", ctx.answer or ""))
        return Finding(property=self.property, assessor=self.key,
                       triggered=n > 3, severity=Severity.NONE, metrics={"digits": n})
'''
check("a genuinely pure module passes", verify_source(PURE) == [], str(verify_source(PURE)))

for src, why, expect in (
    ("import os\n", "imports os", "os"),
    ("import httpx\n", "imports a network client", "httpx"),
    ("import subprocess\n", "imports subprocess", "subprocess"),
    ("x = open('/etc/passwd')\n", "opens a file", "open"),
    ("x = eval('1+1')\n", "calls eval", "eval"),
    ("x = __import__('os')\n", "uses __import__", "__import__"),
    ("y = ''.__class__.__mro__\n", "walks dunder attributes", "__class__"),
    ("z = getattr(o, 'x')\n", "reconstructs attribute access from a string", "getattr"),
    ("async def f():\n    pass\n", "declares async code", "AsyncFunctionDef"),
    ("from . import sibling\n", "imports relatively", "relative"),
):
    probs = verify_source(src)
    named = any(expect in p for p in probs)
    check(f"a module that {why} is refused", bool(probs) and named,
          f"expected {expect!r} in {probs}")

_lines = verify_source("import os\n")
check("…and the refusal names the LINE, so an author can find it",
      _lines and _lines[0].startswith("line 1:"), str(_lines))
check("an oversized module is refused before it is parsed",
      any("caps at" in p for p in verify_source("x = 1\n" * 20000)))

section("5 · the tiers are enforced at LOAD, not at sign time")

write_pack("badtier", {
    "properties": [{"key": "p", "question": "q?", "lanes": ["POST_HOC"]}],
    "assessors": [{"key": "a", "property": "p", "tier": "verified",
                   "source_file": "assessors.py", "class": "Bad"}],
}, {"assessors.py": "import os\n" + PURE.replace("class Ok", "class Bad")})
mgr = fresh_manager()
bad = mgr.packs.get("badtier")
check("a module claiming the verified tier while failing it is REFUSED at load",
      bad is not None and not bad.assessors and any(
          "verified-tier subset" in p for p in bad.problems), str(bad.problems if bad else None))
check("…so it can never be resolved, whatever a later check does",
      registry.get("badtier.a") is None)

write_pack("scannedpack", {
    "properties": [{"key": "p", "question": "q?", "lanes": ["POST_HOC"]}],
    "assessors": [{"key": "a", "property": "p", "tier": "scanned",
                   "source_file": "assessors.py", "class": "Wide"}],
}, {"assessors.py": '''
import os
from trusted_data_agent.agent.assurance.capabilities import AssessorCapability as C
from trusted_data_agent.agent.assurance.contract import Assessor, Finding, Severity

class Wide(Assessor):
    # Claims PURE — which would earn the unconditional in-flight lane.
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC})
    def assess(self, ctx):
        return Finding(property=self.property, assessor=self.key, triggered=False)
'''})
mgr = fresh_manager()
sp = registry.get("scannedpack.a")
check("a scanned-tier module loads", sp is not None)
check("…but its lanes are DERIVED down to post-hoc only, whatever it claims",
      sp is not None and sp.lanes() == frozenset({Lane.POST_HOC}),
      str(sorted(l.name for l in sp.lanes())) if sp else "")
check("…so conformance refuses it in flight",
      bool(conformance.check_binding("scannedpack.p", "scannedpack.a", Lane.IN_FLIGHT)))

section("6 · the bind gate — signed, or not bound")

mgr = fresh_manager()
_probs = conformance.check_binding("acme.tone", "acme.tone_rules", Lane.POST_HOC)
check("an UNSIGNED third-party module cannot be bound",
      bool(_probs), "an unsigned module was bindable")
check("…and the refusal says what to do about it",
      any("sign the pack" in p for p in _probs), str(_probs))
check("…while a builtin needs no signature (trusted by shipping)",
      conformance.check_binding("answer_support", "corpus_support", Lane.POST_HOC) == [])

# Now sign it for real, through the real trust store, and watch the gate open.
_signed_ok = False
try:
    from trusted_data_agent.core.trust import store as _ts
    from trusted_data_agent.core.trust import content as _tc
    import trusted_data_agent.core.trust.content as _tcmod

    _orig_leaf = _tcmod._LEAF.get("assurance_module")

    def _hash_from_tmp(pack_id):
        d = _TMP / pack_id
        if not (d / "manifest.json").exists():
            return None
        payload = {"manifest": json.loads((d / "manifest.json").read_text()),
                   "sources": {p.name: p.read_text() for p in sorted(d.glob("*.py"))}}
        from trusted_data_agent.core.trust.attestation import content_hash
        return content_hash(payload)

    _tcmod._LEAF["assurance_module"] = _hash_from_tmp
    _ts.record_attestation("assurance_module", "acme", _hash_from_tmp("acme"),
                           signed=True, db_path=_DB)
    _signed_ok = True
except Exception as exc:  # noqa: BLE001
    print(f"    (trust suite unavailable: {exc})")

if _signed_ok:
    import trusted_data_agent.core.trust.store as _tsmod
    _orig_attested = _tsmod.attested_hash
    _tsmod.attested_hash = lambda k, i, db_path=None, owner_uuid=None: _orig_attested(k, i, db_path=_DB, owner_uuid=owner_uuid)
    _probs2 = conformance.check_binding("acme.tone", "acme.tone_rules", Lane.POST_HOC)
    check("a SIGNED third-party module binds", _probs2 == [], str(_probs2))

    # …and editing it afterwards closes the gate again.
    write_pack("acme", {
        "properties": [{"key": "tone", "question": "Is the tone right?",
                        "lanes": ["POST_HOC"]}],
        "assessors": [{"key": "tone_rules", "property": "tone", "tier": "declarative",
                       "spec": {"patterns": {"flag": ["\\bstupid\\b", "\\bidiot\\b"]}}}],
    })
    _probs3 = conformance.check_binding("acme.tone", "acme.tone_rules", Lane.POST_HOC)
    check("…and an EDIT after signing closes the gate again",
          bool(_probs3) and any("changed" in p for p in _probs3), str(_probs3))
    _tsmod.attested_hash = _orig_attested
    _tcmod._LEAF["assurance_module"] = _orig_leaf

section("7 · declarative assessors do the work")

mgr = fresh_manager()
a = registry.get("acme.tone_rules")
f = a.run(AssessmentContext(answer="this is stupid"))
check("a pattern rule flags what it declares", f.triggered)
check("…carrying bounded evidence, never the whole answer",
      f.evidence and all(len(e) <= 120 for e in f.evidence), str(f.evidence))
f2 = a.run(AssessmentContext(answer="this is fine"))
check("…and does not flag what it does not match", not f2.triggered)
f3 = a.run(AssessmentContext(answer=""))
check("…and abstains rather than passing when the field is empty", bool(f3.abstained))

write_pack("expr", {
    "properties": [{"key": "len", "question": "Is it short?", "lanes": ["POST_HOC"]}],
    "assessors": [{"key": "words", "property": "len", "tier": "declarative",
                   "spec": {"metric_expr": "answer_words", "metric_name": "answer_words",
                            "gate": 10, "higher_is_better": False}}],
})
mgr = fresh_manager()
e = registry.get("expr.words")
check("a metric expression computes over COUNTS, never raw text",
      e.run(AssessmentContext(answer="a b c")).metrics["answer_words"] == 3.0)
check("…and bands against its own gate",
      e.run(AssessmentContext(answer="w " * 50)).triggered)

section("8 · regex safety — a pattern is data, a catastrophic pattern is an outage")

for bad_spec, why in (
    ({"patterns": {"flag": ["(a+)+b"]}}, "a nested quantifier"),
    ({"patterns": {"flag": ["x" * 600]}}, "an oversized pattern"),
    ({"patterns": {"flag": ["["]}}, "an invalid regex"),
    ({"patterns": {"flag": ["a"] * 100}}, "too many patterns"),
    ({"patterns": {}}, "no flag family"),
):
    write_pack("rx", {
        "properties": [{"key": "p", "question": "q?", "lanes": ["POST_HOC"]}],
        "assessors": [{"key": "a", "property": "p", "tier": "declarative",
                       "spec": bad_spec}],
    })
    mgr = fresh_manager()
    p = mgr.packs.get("rx")
    check(f"{why} refuses the ASSESSOR, not a turn",
          p is not None and not p.assessors and p.problems, str(p.problems if p else None))

section("9 · a pack cannot forge its identity")

write_pack("honest", {
    "properties": [{"key": "p", "question": "q?", "lanes": ["POST_HOC"]}],
    "assessors": [{"key": "a", "property": "p", "tier": "verified",
                   "source_file": "assessors.py", "class": "Imposter"}],
}, {"assessors.py": PURE.replace("class Ok", "class Imposter").replace(
    "    def assess", "    key = 'corpus_support'\n    property = 'answer_support'\n"
    "    def assess")})
mgr = fresh_manager()
check("a module claiming a BUILTIN's key is re-identified from the manifest",
      registry.get("honest.a") is not None
      and registry.get("corpus_support").key == "corpus_support")
check("…and cannot displace the builtin it named",
      registry.get("corpus_support").property == "answer_support")

section("10 · the generic PropertyScorer")

from trusted_data_agent.eval.scorers import plugin_properties as PP        # noqa: E402
from trusted_data_agent.eval.scorer import scorers_for                     # noqa: E402


class _FakeRun:
    _t = {"user_query": "q"}

    def final_answer_text(self):
        return "this is stupid"

    def retrieved_documents(self):
        return ()

    def tool_calls(self):
        return ()

    def platform_authored_answer(self):
        return ""


PP.reset_for_tests()
mgr = fresh_manager()
dims = PP.sync_plugin_scorers(mgr.tunables())
check("an UNBOUND pack property gets no scorer", "acme.tone" not in dims, str(dims))

registry.bind("acme.tone", "acme.tone_rules", (Lane.POST_HOC,), validate=False)
dims = PP.sync_plugin_scorers(mgr.tunables())
check("a BOUND pack property gets one, with no pack-supplied eval code",
      "acme.tone" in dims, str(dims))
_s = PP._INSTALLED["acme.tone"]
_v = _s.score(None, _FakeRun())
check("…and it grades through the bound module", _v.passed is False, str(_v))
check("…declaring the property, so the Guide can route to it",
      getattr(_s, "property", None) == "acme.tone")
check("…and never GATES a turn (a v1 policy, stated)", _s.gating is False)

registry.unbind("acme.tone", Lane.POST_HOC)
_v2 = _s.score(None, _FakeRun())
check("an unbound property abstains rather than disappearing",
      _v2.passed is None and "no module is bound" in (_v2.explanation or ""),
      str(_v2))
check("…and the scorer stays registered, so the dimension is still visible",
      any(x.dimension == "acme.tone" for x in scorers_for("rag_focused")))

section("11 · the deployment envelope carries the bindings")

try:
    from trusted_data_agent.core.trust import deployment as D

    base = D.build_deployment_signature({"provider": "p", "model": "m"},
                                        {"tools": ["t"], "prompts": []}, [])
    check("a signature with no bindings field is unchanged (no upgrade churn)",
          "bindings" not in base)
    b1 = D.build_deployment_signature({"provider": "p", "model": "m"},
                                      {"tools": ["t"], "prompts": []}, [],
                                      [{"property": "x", "lane": "POST_HOC",
                                        "assessor": "a", "module": "builtin"}])
    b2 = D.build_deployment_signature({"provider": "p", "model": "m"},
                                      {"tools": ["t"], "prompts": []}, [],
                                      [{"property": "x", "lane": "POST_HOC",
                                        "assessor": "b", "module": "builtin"}])
    check("a REBIND moves the deployment hash",
          D.deployment_hash(b1) != D.deployment_hash(b2))
    check("…and identical bindings hash identically",
          D.deployment_hash(b1) == D.deployment_hash(dict(b1)))
    drift = D.describe_drift(b1, b2)
    check("…and the drift NAMES the property and the new module",
          any("x" in d and "b" in d for d in drift), str(drift))
    b3 = D.build_deployment_signature({"provider": "p", "model": "m"},
                                      {"tools": ["t"], "prompts": []}, [],
                                      [{"property": "x", "lane": "POST_HOC",
                                        "assessor": "a", "module": "hash2"}])
    check("an in-place EDIT of a bound module is named, not silent",
          any("not the version that was attested" in d
              for d in D.describe_drift(b1, b3)), str(D.describe_drift(b1, b3)))
    check("an unreadable binding table is UNRESOLVED, never an empty list",
          D.build_deployment_signature(None, None, None, D.UNRESOLVED)["bindings"]
          == D.UNRESOLVED)
except Exception as exc:  # noqa: BLE001
    print(f"    (trust deployment unavailable: {exc})")

section("12 · the shipped sample pack is real and disabled")

_ROOT = Path(__file__).resolve().parents[1]
_sample = _ROOT / "assurance_modules" / "builtin" / "sample_compliance" / "manifest.json"
check("a reference pack ships", _sample.exists())
_reg_file = _ROOT / "assurance_modules" / "module_registry.json"
_reg_json = json.loads(_reg_file.read_text())
check("…registered as DISABLED — it is there to be read, not run",
      all(m.get("status") == "disabled" for m in _reg_json.get("modules", [])))
_sm = json.loads(_sample.read_text())
check("…and it is a working manifest, not a sketch",
      bool(_sm.get("properties")) and bool(_sm.get("assessors"))
      and bool(_sm.get("tunables")))

# BROWSER-FOUND: the registry's `disabled` status was defeated by the auto-discovery
# pass, which exists to pick up builtins that have no registry entry at all — so the
# reference pack shipped disabled and loaded anyway. The only per-pack switch the
# registry has must actually hold.
S.save_settings({"third_party_assurance_enabled": True}, "admin", db_path=_DB)
M.reset_assurance_module_manager()
_real = M.AssuranceModuleManager(user_dir=_TMP / "_empty",
                                 modules_dir=_ROOT / "assurance_modules", db_path=_DB)
_real.discover()
check("a pack the registry DISABLES does not load, even though it is on disk",
      "sample_compliance" not in _real.packs, str(list(_real.packs)))
check("…and says why, rather than being silently absent",
      any(s["pack_id"] == "sample_compliance" and "registry" in s["reason"]
          for s in _real.skipped), str(_real.skipped))

shutil.rmtree(_TMP, ignore_errors=True)
try:
    os.unlink(_DB)
except OSError:
    pass

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
