"""
PROP-003 · the IN-FLIGHT half, proven end to end — SIBLINGS.

The plane shipped with only the post-hoc lane consulted, which made a third-party module
something that could measure a turn but never affect one. This drives the other half:

    a pack declares its OWN property, routed to a gate (`enforced_by`)
    ONE module bound to BOTH lanes
    → in flight the gate REDACTS what it found, through the platform's own ladder
    → afterwards the SAME module grades it on the Scorecard
    → registry.symmetry() reads `shared` — the Guide's S badge; the thing that acted and
      the thing that graded are not two implementations that agree, they are one

Also proven: the union rule (a plugin adds to the platform's own findings and can never
subtract from them), and that the raw matched value never reaches the recorded event.

Pure: no server, no network. Builds a temp pack and drives the real guardrail runtime.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


_TMP = Path(tempfile.mkdtemp(prefix="inflight_pack_"))
_DB = tempfile.mktemp(suffix="_inflight.db")

# A pack that says: our internal customer reference is personal data, and the outbound
# guard should act on it.
(_TMP / "acmeref").mkdir(parents=True)
(_TMP / "acmeref" / "manifest.json").write_text(json.dumps({
    "pack_id": "acmeref",
    "display_name": "Acme · Customer references",
    "version": "1.0.0",
    "description": "Treats an internal customer reference as personal data.",
    "properties": [{
        "key": "custref", "label": "Customer reference",
        "question": "Does the answer expose an internal customer reference?",
        "lanes": ["IN_FLIGHT", "POST_HOC"],
        "enforced_by": "outbound_guard",
    }],
    "assessors": [{
        "key": "ref_rule", "property": "custref", "label": "Reference rule",
        "tier": "declarative", "requires": ["answer"],
        "subject_is_model_output": False,
        "spec": {"patterns": {"flag": ["CUST-[0-9]{5}"]},
                 "finding_category": "pii", "span_label": "CUSTREF"},
    }],
}))

from trusted_data_agent.agent import assurance as A                         # noqa: E402
from trusted_data_agent.agent.assurance import registry, inflight           # noqa: E402
from trusted_data_agent.agent.assurance.capabilities import Lane            # noqa: E402
from trusted_data_agent.assurance_modules import manager as M               # noqa: E402
from trusted_data_agent.assurance_modules import settings as S              # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG                       # noqa: E402

A.reset_for_tests()
A.bootstrap(db_path=_DB)
S.save_settings({"third_party_assurance_enabled": True}, "admin", db_path=_DB)
M.reset_assurance_module_manager()
mgr = M.AssuranceModuleManager(user_dir=_TMP, modules_dir=_TMP / "_none", db_path=_DB)
mgr.discover()

ANSWER = "Contact Jane at jane@acme.com regarding CUST-48219 and CUST-77031."
PROFILE = {"guardrailConfig": {"outbound": {"mode": "enforce",
                                            "pii": True, "toxicity": True}}}
APP_CONFIG.GUARDRAILS_ENABLED = True

from trusted_data_agent.agent.guardrails import runtime as GR               # noqa: E402

section("1 · the pack loads and routes itself to a gate")

pack = mgr.packs.get("acmeref")
check("the pack loads", pack is not None and not pack.problems,
      str(pack.problems if pack else "not loaded"))
check("…and its property is routed to the gate its manifest named",
      inflight.routed_to("outbound_guard") == ("acmeref.custref",),
      str(inflight.routed_to("outbound_guard")))
check("…but nothing is bound yet — installing is not assigning",
      registry.resolve("acmeref.custref", Lane.IN_FLIGHT) is None)

section("2 · before binding — the platform's own behaviour, unchanged")

before_text, before_ev = GR.scan_outbound(ANSWER, profile=PROFILE)
check("the native email redaction happens", "[REDACTED:email]" in before_text)
check("…and the pack's references are NOT touched, because nothing is bound",
      "CUST-48219" in before_text, before_text)

section("3 · ONE module, BOTH lanes — the sibling shape")

registry.bind("acmeref.custref", "acmeref.ref_rule",
              (Lane.IN_FLIGHT, Lane.POST_HOC), validate=False)
sym = registry.symmetry("acmeref.custref")
check("symmetry reads SHARED — one assessment, two lanes (the Guide's S badge)",
      sym["state"] == "shared", str(sym))
check("…and both lanes resolve the SAME module",
      registry.resolve("acmeref.custref", Lane.IN_FLIGHT).key
      == registry.resolve("acmeref.custref", Lane.POST_HOC).key)

section("4 · IN FLIGHT — DORMANT first, then the operator raises it")

# The pack routes its property with `enforced_by`, which now SYNTHESISES a control rather
# than being a second runtime path. So it arrives DORMANT: reported, never acting. Before
# the unification this same install acted immediately, because the shorthand inherited the
# gate's posture — an install could alter answers with no separate decision.
from trusted_data_agent.agent.assurance import controls as CTL             # noqa: E402
from trusted_data_agent.agent.assurance import store as CSTORE             # noqa: E402

_syn = "acmeref.custref.control"
check("the shorthand produced a control", CTL.get(_syn) is not None,
      str([c.key for c in CTL.controls()]))
check("…which arrives DORMANT", CTL.posture(_syn, db_path=_DB) == "observe")

dormant_text, dormant_ev = GR.scan_outbound(ANSWER, profile=PROFILE)
check("so installing does NOT alter the answer",
      "CUST-48219" in dormant_text, dormant_text)
check("…while the finding is still recorded for the operator to see",
      any("CUSTREF" in str(f.get("label")) for f in (dormant_ev.get("findings") or [])))

# The operator raises it — the decision that was previously implicit in the install.
CSTORE.set_control_posture(_syn, "enforce", "admin", db_path=_DB)
_orig_cp = CSTORE.control_posture
CSTORE.control_posture = lambda k, db_path=None: _orig_cp(k, db_path=_DB)
after_text, after_ev = GR.scan_outbound(ANSWER, profile=PROFILE)
check("the pack's references are REDACTED by the platform's own ladder",
      "[REDACTED:CUSTREF]" in after_text, after_text)
check("…both of them", after_text.count("[REDACTED:CUSTREF]") == 2, after_text)
check("…and no raw reference survives in the delivered text",
      "CUST-48219" not in after_text and "CUST-77031" not in after_text)

# THE UNION RULE. A plugin adds; it can never subtract. If binding a pack module could
# displace the platform's own detection, an installed pack would be able to WEAKEN what
# an agent may say — the one thing this whole design refuses.
check("the platform's OWN detection still fires alongside it",
      "[REDACTED:email]" in after_text, after_text)

labels = [f.get("label") for f in (after_ev.get("findings") or [])]
check("the event reports both findings", len(labels) == 2, str(labels))
check("…naming the pack's module, so a reader knows where the finding came from",
      any("acmeref.ref_rule" in str(x) for x in labels), str(labels))
check("the action is the platform's, not the pack's",
      after_ev.get("action") == "redact", str(after_ev.get("action")))

# PRIVACY. The matched value is needed to mask, and must not leave the process. The
# assurance Finding carries it in `payload`, which no surface renders and which the seam
# strips before anything is reported.
raw = json.dumps(after_ev)
check("the RAW matched value never reaches the recorded event",
      "CUST-48219" not in raw and "CUST-77031" not in raw)

section("5 · observe mode still only observes")


obs = {"guardrailConfig": {"outbound": {"mode": "observe", "pii": True}}}
obs_text, obs_ev = GR.scan_outbound(ANSWER, profile=obs)
check("a pack finding in observe does NOT alter the answer", obs_text == ANSWER)
check("…but it is still recorded", obs_ev.get("action") == "pass"
      and any("acmeref" in str(f.get("label")) for f in (obs_ev.get("findings") or [])),
      str(obs_ev.get("findings")))

section("6 · AFTER the turn — the same module grades it")

from trusted_data_agent.eval.scorers import plugin_properties as PP         # noqa: E402

PP.reset_for_tests()
dims = PP.sync_plugin_scorers(mgr.tunables())
check("the bound property has a scorer, written by nobody",
      "acmeref.custref" in dims, str(dims))


class _Run:
    _t = {"user_query": "q"}

    def final_answer_text(self):
        return ANSWER

    def retrieved_documents(self):
        return ()

    def tool_calls(self):
        return ()

    def platform_authored_answer(self):
        return ""


v = PP._INSTALLED["acmeref.custref"].score(None, _Run())
check("…and it grades the turn through the SAME module the gate acted on",
      v.passed is False, str(v))
check("…so the thing that acted and the thing that graded cannot disagree",
      registry.resolve("acmeref.custref", Lane.IN_FLIGHT).key
      == registry.resolve("acmeref.custref", Lane.POST_HOC).key
      == "acmeref.ref_rule")

section("7 · unbinding restores the platform's own behaviour exactly")

registry.unbind("acmeref.custref", Lane.IN_FLIGHT)
final_text, _ = GR.scan_outbound(ANSWER, profile=PROFILE)
CSTORE.control_posture = _orig_cp
check("an unbound module stops affecting the answer", final_text == before_text,
      f"{final_text!r} != {before_text!r}")

shutil.rmtree(_TMP, ignore_errors=True)
try:
    os.unlink(_DB)
except OSError:
    pass

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
