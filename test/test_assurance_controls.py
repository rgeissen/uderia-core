"""
PROP-003 · THIRD-PARTY CONTROLS — a pack ships machinery that acts.

The plane opened assessment and stopped, on the reasoning that the posture ladder took
five recorded contract breaches to get right. That conflated four things; only two of them
must stay the platform's:

    the assessment   → third party      the act        → PLATFORM performs it
    the policy       → third party      the ladder     → PLATFORM

The load-bearing assertion is §2: **DORMANT ON ARRIVAL.** A third-party control installs at
`observe`, records what it would have done, and changes nothing until an admin raises it.
That property is what makes shipping a control safe to allow at all — it moves the decision
from the vendor to the operator — so it is asserted from every direction and mutation-tested.

Pure: no server, no network.
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


_TMP = Path(tempfile.mkdtemp(prefix="ctl_pack_"))
_DB = tempfile.mktemp(suffix="_ctl.db")

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
    }],
    "assessors": [{
        "key": "ref_rule", "property": "custref", "label": "Reference rule",
        "tier": "declarative", "requires": ["answer"],
        "spec": {"patterns": {"flag": ["CUST-[0-9]{5}"]},
                 "finding_category": "pii", "span_label": "CUSTREF"},
    }],
    # The pack's OWN control. It declares WHEN and WHICH act; the platform does the rest.
    "controls": [{
        "key": "ref_guard", "label": "Customer-reference guard", "station": "guarded",
        "attaches_to": "outbound_answer", "fires_on": "custref", "act": "alter",
        "description": "Redacts an internal customer reference from the answer.",
    }],
}))

from trusted_data_agent.agent import assurance as A                         # noqa: E402
from trusted_data_agent.agent.assurance import controls as CTL              # noqa: E402
from trusted_data_agent.agent.assurance import registry, store              # noqa: E402
from trusted_data_agent.agent.assurance.capabilities import Lane            # noqa: E402
from trusted_data_agent.assurance_modules import manager as M               # noqa: E402
from trusted_data_agent.assurance_modules import settings as S              # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG                       # noqa: E402

A.reset_for_tests()
CTL.reset_for_tests()
A.bootstrap(db_path=_DB)
S.save_settings({"third_party_assurance_enabled": True}, "admin", db_path=_DB)
M.reset_assurance_module_manager()
mgr = M.AssuranceModuleManager(user_dir=_TMP, modules_dir=_TMP / "_none", db_path=_DB)
mgr.discover()
registry.bind("acmeref.custref", "acmeref.ref_rule",
              (Lane.IN_FLIGHT, Lane.POST_HOC), validate=False)

ANSWER = "Contact Jane at jane@acme.com regarding CUST-48219 and CUST-77031."
PROFILE = {"guardrailConfig": {"outbound": {"mode": "enforce", "pii": True}}}
APP_CONFIG.GUARDRAILS_ENABLED = True
from trusted_data_agent.agent.guardrails import runtime as GR               # noqa: E402


def deliver(profile=PROFILE):
    # The store is keyed by the real DB path in production; point it at ours.
    _orig = store.control_posture
    store.control_posture = lambda k, db_path=None: _orig(k, db_path=_DB)
    try:
        return GR.scan_outbound(ANSWER, profile=profile)
    finally:
        store.control_posture = _orig


section("1 · a pack ships a control")

pack = mgr.packs.get("acmeref")
check("the pack loads with no problems", pack and not pack.problems,
      str(pack.problems if pack else "not loaded"))
check("…and registers its OWN control", pack.controls == ["acmeref.ref_guard"],
      str(pack.controls if pack else None))
c = CTL.get("acmeref.ref_guard")
check("the control is third-party, and says so", c and c.is_third_party
      and c.origin == "acmeref")
check("…it declares which act it wants at enforce", c.act == "alter")
check("…and where it attaches", c.attaches_to == "outbound_answer")

section("2 · DORMANT ON ARRIVAL — the property that replaces the old lock")

check("an unconfigured control's posture is observe",
      CTL.posture("acmeref.ref_guard", db_path=_DB) == "observe")
check("…so it resolves to NO act", CTL.effective_act(c, "observe") is None)
text, ev = deliver()
check("installing the pack does NOT alter the answer",
      "CUST-48219" in text and "[REDACTED:CUSTREF]" not in text, text)
check("…while the platform's own detection still runs",
      "[REDACTED:email]" in text, text)
check("…and the finding IS recorded, so the operator can see what it would do",
      any("CUSTREF" in str(f.get("label")) for f in (ev.get("findings") or [])),
      json.dumps(ev.get("findings")))

section("3 · the operator raises it — and only then does it act")

store.set_control_posture("acmeref.ref_guard", "enforce", "admin", db_path=_DB)
check("the posture persists", CTL.posture("acmeref.ref_guard", db_path=_DB) == "enforce")
text2, ev2 = deliver()
check("now the answer IS altered by the pack's control",
      text2.count("[REDACTED:CUSTREF]") == 2, text2)
check("…the platform's own detection still fires alongside it (union, not replacement)",
      "[REDACTED:email]" in text2, text2)
check("…the ACTION recorded is the platform's", ev2.get("action") == "redact",
      str(ev2.get("action")))
check("…and no raw value reaches the event",
      "CUST-48219" not in json.dumps(ev2))

section("4 · the LADDER is the platform's, not the pack's")

check("a pack declares the act it wants at enforce, and gets it",
      CTL.effective_act(c, "enforce") == "alter")
check("…at annotate it is never stronger than a note",
      CTL.effective_act(c, "annotate") == "annotate")
check("…at observe it does nothing", CTL.effective_act(c, "observe") is None)
check("…at off it does nothing", CTL.effective_act(c, "off") is None)
check("an unknown posture falls to OBSERVE, never to the declared act",
      CTL.effective_act(c, "ENFORSE") is None,
      "a typo must never silently grant a control more power than the operator gave it")

store.set_control_posture("acmeref.ref_guard", "annotate", "admin", db_path=_DB)
text3, _ = deliver()
check("at annotate the answer's references are NOT redacted",
      "CUST-48219" in text3, text3)
store.set_control_posture("acmeref.ref_guard", "enforce", "admin", db_path=_DB)

section("5 · a control obeys the AGENT's posture too")

obs = {"guardrailConfig": {"outbound": {"mode": "observe", "pii": True}}}
text4, ev4 = deliver(obs)
check("an agent in observe is untouched, however the control is set",
      text4 == ANSWER, text4)
check("…but the finding is still recorded",
      any("CUSTREF" in str(f.get("label")) for f in (ev4.get("findings") or [])))

section("6 · what a pack may NOT declare")

for bad, why in (
    ({"attaches_to": "outbound_answer", "act": "delete_database"},
     "an act outside the closed vocabulary"),
    ({"attaches_to": "the_planner", "act": "alter"},
     "a chokepoint the platform does not stand at"),
    ({"attaches_to": "outbound_answer", "act": "alter", "fires_on": ""},
     "no property to fire on"),
):
    spec = {"key": "x", "label": "X", "station": "guarded",
            "fires_on": "custref", "act": "alter", "attaches_to": "outbound_answer"}
    spec.update(bad)
    probs = CTL.register(CTL.Control(
        key="t." + spec["key"], label=spec["label"], station=spec["station"],
        attaches_to=spec["attaches_to"], fires_on=spec.get("fires_on", ""),
        act=spec["act"]))
    check(f"{why} is refused", bool(probs), str(probs))
check("…and the refusal explains WHY the set is closed",
      any("rendering" in x or "already stands" in x for x in CTL.register(CTL.Control(
          key="t.y", label="Y", station="guarded", attaches_to="nowhere",
          fires_on="p", act="explode"))))

section("7 · a control is always DATA — code lives only in an assessor")

import dataclasses                                                          # noqa: E402
check("Control is a frozen dataclass with no callable field",
      dataclasses.is_dataclass(CTL.Control)
      and not any(callable(getattr(c, f.name, None))
                  for f in dataclasses.fields(CTL.Control)),
      "the thing with POWER must never carry logic")
check("…and its policy is a platform-owned table, not a pack-supplied function",
      set(CTL._LADDER) == {"off", "observe", "annotate", "enforce", "review"})

section("8 · `enforced_by` is sugar for a control — ONE runtime path")

# It predates third-party controls. Left as its own mechanism it inherited the GATE's
# posture, so installing a pack routed to a gate already at enforce made it act
# immediately — no dormancy, no separate decision. Two mechanisms, two safety properties.
(_TMP / "sugarpack").mkdir(parents=True)
(_TMP / "sugarpack" / "manifest.json").write_text(json.dumps({
    "pack_id": "sugarpack", "display_name": "Sugar", "version": "1.0.0",
    "description": "uses the enforced_by shorthand",
    "properties": [{"key": "thing", "label": "Thing", "question": "q?",
                    "lanes": ["IN_FLIGHT", "POST_HOC"],
                    "enforced_by": "outbound_guard"}],
    "assessors": [{"key": "r", "property": "thing", "tier": "declarative",
                   "requires": ["answer"],
                   "spec": {"patterns": {"flag": ["ZZZ"]}}}],
}))
M.reset_assurance_module_manager()
mgr2 = M.AssuranceModuleManager(user_dir=_TMP, modules_dir=_TMP / "_none", db_path=_DB)
mgr2.discover()
sp = mgr2.packs.get("sugarpack")
check("the shorthand produces a real control", sp and sp.controls,
      str(sp.problems if sp else "not loaded"))
syn = CTL.get("sugarpack.thing.control")
check("…attached where the named gate stands", syn and syn.attaches_to == "outbound_answer")
check("…and DORMANT like any other, rather than inheriting the gate's posture",
      CTL.posture("sugarpack.thing.control", db_path=_DB) == "observe",
      "the old path let an install act immediately if the gate was already enforcing")

section("9 · SIGNED-TO-ACT — the gate is transitive, and that is structural")

# On paper a control has no signing gate: `check_binding` refuses to bind an unsigned
# module's ASSESSOR, but `register()` takes any control. That would put the WEAKER gate on
# the machinery with MORE power. It holds anyway, and this asserts WHY so a refactor cannot
# quietly undo it: a control is only ever offered `augment()`'s output — findings from
# BOUND modules — so signed→bindable→produces a Finding→a control may act. No binding, no
# finding, no act, whatever the operator set the posture to.
(_TMP / "unsigned").mkdir(parents=True)
(_TMP / "unsigned" / "manifest.json").write_text(json.dumps({
    "pack_id": "unsigned", "display_name": "Unsigned", "version": "1.0.0",
    "description": "never signed",
    "properties": [{"key": "p", "label": "P", "question": "q?",
                    "lanes": ["IN_FLIGHT", "POST_HOC"]}],
    "assessors": [{"key": "a", "property": "p", "tier": "declarative",
                   "requires": ["answer"],
                   "spec": {"patterns": {"flag": ["CUST-[0-9]{5}"]},
                            "finding_category": "pii", "span_label": "UNSIGNED"}}],
    "controls": [{"key": "g", "label": "G", "station": "guarded",
                  "attaches_to": "outbound_answer", "fires_on": "p", "act": "withhold"}],
}))
M.reset_assurance_module_manager()
mgr3 = M.AssuranceModuleManager(user_dir=_TMP, modules_dir=_TMP / "_none", db_path=_DB)
mgr3.discover()
refusal = registry.bind("unsigned.p", "unsigned.a", (Lane.IN_FLIGHT,))   # validate ON
check("an unsigned pack cannot bind its assessor", bool(refusal), str(refusal))
check("…and the refusal names signing as the reason",
      any("signed" in str(x) for x in refusal), str(refusal))
store.set_control_posture("unsigned.g", "enforce", "admin", db_path=_DB)
check("…its control is registered all the same", CTL.get("unsigned.g") is not None)
check("…and an operator can raise it",
      CTL.posture("unsigned.g", db_path=_DB) == "enforce")
t_unsigned, _ = deliver()
check("but it CANNOT act, because its trigger needs a bound module",
      "[REDACTED:UNSIGNED]" not in t_unsigned and "withheld" not in t_unsigned.lower(),
      t_unsigned)
check("…and the escalating act (withhold) is no exception",
      t_unsigned == text2, f"{t_unsigned!r} != {text2!r}")

section("10 · a control that could never fire is REFUSED, not left silent")

# Two ways to land on a control that registers, signs, is raised to enforce — and never
# fires: a typo, or naming a PLATFORM property. A bare name is namespaced to the pack, so
# both arrive as a reference to a property that does not exist. Silence here is the
# declared-but-never-consulted failure this project has already fixed twice.
for bad, why in (("custrefx", "a typo in the property name"),
                 ("output_sensitivity", "a PLATFORM property a control can never see")):
    (_TMP / f"bad_{bad}").mkdir(parents=True, exist_ok=True)
    (_TMP / f"bad_{bad}" / "manifest.json").write_text(json.dumps({
        "pack_id": f"bad_{bad}", "display_name": "Bad", "version": "1.0.0",
        "description": "d",
        "properties": [{"key": "custref", "label": "C", "question": "q?",
                        "lanes": ["IN_FLIGHT"]}],
        "controls": [{"key": "g", "label": "G", "station": "guarded",
                      "attaches_to": "outbound_answer", "fires_on": bad, "act": "alter"}],
    }))
M.reset_assurance_module_manager()
mgr4 = M.AssuranceModuleManager(user_dir=_TMP, modules_dir=_TMP / "_none", db_path=_DB)
mgr4.discover()
for bad, why in (("custrefx", "a typo in the property name"),
                 ("output_sensitivity", "a PLATFORM property a control can never see")):
    bp = mgr4.packs.get(f"bad_{bad}")
    check(f"{why} is refused", bool(bp and bp.problems), str(bp.problems if bp else None))
    check(f"…and the refusal names the pack's own properties, so it is fixable",
          any("custref" in str(x) for x in (bp.problems if bp else [])),
          str(bp.problems if bp else None))
    check(f"…and no dead control is registered for it",
          CTL.get(f"bad_{bad}.g") is None)

section("11 · a raised posture is part of the DEPLOYMENT envelope")

from trusted_data_agent.core.trust import deployment as DEP                # noqa: E402

sig_none = DEP.build_deployment_signature(None, None, None, [], [])
sig_raised = DEP.build_deployment_signature(
    None, None, None, [], [{"control": "acmeref.ref_guard",
                            "posture": "enforce", "act": "alter"}])
check("a deployment that never raised a control hashes as it did BEFORE this field existed",
      DEP.deployment_hash(sig_none)
      == DEP.deployment_hash(DEP.build_deployment_signature(None, None, None, [])),
      "adding the field must not flip every deployed agent to `changed` on upgrade")
check("…and raising one DOES move the deployment hash",
      DEP.deployment_hash(sig_none) != DEP.deployment_hash(sig_raised),
      "starting to withhold answers is a wider change than a rebind, which is already in")
check("…while 'we could not read the postures' hashes unlike 'there are none'",
      DEP.deployment_hash(DEP.build_deployment_signature(
          None, None, None, [], DEP.UNRESOLVED)) != DEP.deployment_hash(sig_none))
live = DEP.control_postures_state(db_path=_DB)
check("the live reading carries only what was moved off the default",
      isinstance(live, list) and all(r["posture"] != "observe" for r in live), str(live))

shutil.rmtree(_TMP, ignore_errors=True)
try:
    os.unlink(_DB)
except OSError:
    pass

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
