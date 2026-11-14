"""#24 — the deployment master switches reach the surfaces that report on them.

The Trust Circle read only the per-profile mode blocks; nothing in the ring referenced
`GROUNDING_GATE_ENABLED` or `GUARDRAILS_ENABLED` (verified by grep — zero hits). With a
master off both runtimes short-circuit at their first line, yet GROUNDED/GUARDED
rendered `observe · 7/7` and claimed measurement — the falsehood behind the day-one
confusion where a sign-off the operator expected to be gated sailed through.

The fix is one helper, `rest_routes._deployment_masters()`, carried on the SAME payload
SCORED has always read its own master from (the evals dashboard) and reused by the
audit pack — one source per rule. This suite executes the helper for real (extracted by
ast, run against a stubbed config so no Quart import is needed) and pins the wiring.

Pure: no server, no network.
"""
import ast
import os
import sys
import types

failures = []
passed = 0


def check(name, got, want):
    global passed
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")
    else:
        passed += 1


ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = os.path.join(ROOT, "src", "trusted_data_agent", "api", "rest_routes.py")
with open(SRC, encoding="utf-8") as f:
    source = f.read()

# ── 1 · the helper, executed for real ───────────────────────────────────────
# `rest_routes` imports the whole Quart surface, so the function is lifted out by ast
# and exec'd against a stubbed config module — the test then exercises the SHIPPED
# body, not a reimplementation.
tree = ast.parse(source)
fn = next((n for n in tree.body
           if isinstance(n, ast.FunctionDef) and n.name == "_deployment_masters"), None)
check("#24 the helper exists at module level", fn is not None, True)
check("#24 exactly one definition of the masters dict",
      sum(1 for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "_deployment_masters"), 1)


def _masters_with(planes=None, policy=None, **cfg_attrs):
    """Run the shipped helper body under a stubbed APP_CONFIG and OBDA planes."""
    planes = planes or {}
    policy = policy or {"read": "off", "model": "off", "act": "off"}
    stub_cfg = types.ModuleType("trusted_data_agent.core.config")
    stub_cfg.APP_CONFIG = type("Cfg", (), cfg_attrs)()
    stub_pkg = types.ModuleType("trusted_data_agent")
    stub_core = types.ModuleType("trusted_data_agent.core")
    # The OBDA planes are components (Oct 2026): the helper reads their availability
    # from core/obda_planes for the caller. Stubbed with the planes OFF and the
    # policy at its default, so these keys are asserted as facts too.
    stub_planes = types.ModuleType("trusted_data_agent.core.obda_planes")
    stub_planes.plane_available = lambda plane, user_uuid=None: planes.get(plane, False)
    stub_planes.policy_map = lambda: dict(policy)
    stub_core.obda_planes = stub_planes
    saved = {k: sys.modules.get(k) for k in
             ("trusted_data_agent", "trusted_data_agent.core",
              "trusted_data_agent.core.config", "trusted_data_agent.core.obda_planes")}
    sys.modules.update({"trusted_data_agent": stub_pkg,
                        "trusted_data_agent.core": stub_core,
                        "trusted_data_agent.core.config": stub_cfg,
                        "trusted_data_agent.core.obda_planes": stub_planes})
    try:
        # the request-scoped caller lookup the helper calls (a module global in rest_routes)
        ns: dict = {"_get_user_uuid_from_request": lambda: "u-test"}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), SRC, "exec"), ns)
        return ns["_deployment_masters"]()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


on = _masters_with(GROUNDING_GATE_ENABLED=True, GUARDRAILS_ENABLED=True,
                   ONLINE_EVAL_ENABLED=True, PREFLIGHT_SQL_MODE="enforce")
check("#24 masters on read True",
      (on["grounding_gate_enabled"], on["guardrails_enabled"], on["online_eval_enabled"]),
      (True, True, True))
check("#24 preflight mode is carried verbatim", on["preflight_sql_mode"], "enforce")

off = _masters_with(GROUNDING_GATE_ENABLED=False, GUARDRAILS_ENABLED=False)
check("#24 masters off read False — an explicit fact, not an absence",
      (off["grounding_gate_enabled"], off["guardrails_enabled"]), (False, False))

# A config that predates the flags must fail toward "off" (the runtimes' own default:
# `getattr(..., False)` at their short-circuit) — never raise, never invent True.
bare = _masters_with()
check("#24 absent flags default to the runtimes' own default (off)",
      (bare["grounding_gate_enabled"], bare["guardrails_enabled"],
       bare["online_eval_enabled"], bare["preflight_sql_mode"]),
      (False, False, False, "observe"))
check("#24 every value is JSON-safe",
      all(isinstance(v, (bool, str)) or (isinstance(v, dict) and all(
          isinstance(x, str) for x in v.values())) for v in bare.values()), True)

# The OBDA planes read the component chain for the caller — off unless the plane is
# available, and the policy map is carried verbatim.
check("#24 OBDA planes off by default",
      (bare["obda_enabled"], bare["obda_model_enabled"], bare["obda_act_enabled"]),
      (False, False, False))
lit = _masters_with(planes={"read": True, "act": True},
                    policy={"read": "enforce", "model": "off", "act": "observe"})
check("#24 each plane reads its own availability",
      (lit["obda_enabled"], lit["obda_model_enabled"], lit["obda_act_enabled"]),
      (True, False, True))
check("#24 the OBDA policy is carried verbatim", lit["obda_policy"],
      {"read": "enforce", "model": "off", "act": "observe"})

# ── 2 · the wiring: both consumers, one source ──────────────────────────────
check("#24 the dashboard payload carries the masters",
      '"deployment_masters": _deployment_masters()' in source, True)
check("#24 the audit pack reuses the helper",
      '{**_deployment_masters(), "trust_behavior": cfg}' in source, True)
check("#24 no second inline masters dict survives anywhere in the module",
      source.count('"grounding_gate_enabled": bool(getattr'), 1)

# The dashboard is the payload the ring loads; the masters must ride the SAME response
# as `enabled` (SCORED's master) — a separate endpoint would be a second fetch the
# older-frontend degradation contract does not cover. Asserted on the FUNCTION BODY,
# not a char window: a proximity window is the hollow-assertion shape this walkthrough
# catalogued (it weakens silently as the file grows).
dash_fn = next((n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "evals_online_dashboard"), None)
check("#24 the dashboard endpoint exists", dash_fn is not None, True)
dash_body = ast.get_source_segment(source, dash_fn) or ""
check("#24 the masters ride the dashboard response, beside SCORED's own master",
      '"deployment_masters": _deployment_masters()' in dash_body
      and '"enabled": bool(getattr(APP_CONFIG, "ONLINE_EVAL_ENABLED"' in dash_body, True)

# ── 3 · the consumer contract the frontend depends on ───────────────────────
# The ring treats `null` as UNKNOWN (older backend) and only acts on an explicit
# False — so the helper must always emit real booleans for the two gate keys.
for key in ("grounding_gate_enabled", "guardrails_enabled", "online_eval_enabled",
            "obda_enabled", "obda_model_enabled", "obda_act_enabled", "recommender_enabled"):
    check(f"#24 {key} is a real boolean, never None", isinstance(bare[key], bool), True)

# ── report ──────────────────────────────────────────────────────────────────
print()
if failures:
    for f in failures:
        print(f"  FAIL  {f}")
    print(f"\n{passed} passed, {len(failures)} failed")
    sys.exit(1)
print("#24: the deployment masters reach every surface that reports on them.")
print(f"{passed} passed, 0 failed")
