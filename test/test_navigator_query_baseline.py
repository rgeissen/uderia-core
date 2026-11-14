"""P1 baseline freeze — committed GREEN before the Database Navigator `query` action lands.

Pins that for an agent WITHOUT the new opt-in (`componentConfig.database_navigator.query`),
nothing the model sees, nothing that is signed and nothing a pack carries changes:
the navigator's tool definition and instructions on every model-facing surface, its
LangChain argument schema, the portable capability signature, the deployment signature,
and pack transport of `componentConfig`. Existing actions' behaviour is frozen by
`test_dbcatalog` (it drives describe/preview/search/suggest_joins/open through the real
handler); this suite freezes the surfaces that suite does not read.

Every assertion here must stay green through P1 and after it. The pinned values are
sha256 prefixes of the canonical JSON, taken from the code at commit e21a261e.

Run: python3 test/test_navigator_query_baseline.py     (no server, no network)
"""
import hashlib
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def h(o):
    b = o.encode() if isinstance(o, str) else json.dumps(o, sort_keys=True).encode()
    return hashlib.sha256(b).hexdigest()[:16]


from trusted_data_agent.components.manager import get_component_manager  # noqa: E402
from trusted_data_agent.core import profile_config_contract as pcc  # noqa: E402
from trusted_data_agent.core.trust import deployment as dep  # noqa: E402

cm = get_component_manager()
nav = cm.get_component("database_navigator")

# An agent with the navigator ON and a chosen default source — but no `query` block.
PROFILE = {"profile_type": "llm_only", "useMcpTools": True,
           "componentConfig": {"database_navigator": {"enabled": True, "sourceId": "conn:abc"}}}

print("[1] what the model reads — unchanged for an agent without `query`")
check("tool definition (gated, as every surface reads it)",
      h(cm.tool_definition_for(nav, PROFILE)) == "bd49f6c6144d2bb9", h(cm.tool_definition_for(nav, PROFILE)))
for lvl, want in (("none", "e3b0c44298fc1c14"), ("medium", "f1b301774533f113"), ("heavy", "eca72a8c728fd7da")):
    got = h(cm.apply_instruction_gates(nav.get_instructions(lvl), PROFILE))
    check(f"instructions · {lvl}", got == want, got)
lc = [t for t in cm.get_langchain_tools(PROFILE, {"user_uuid": "u"}) if t.name == "TDA_DatabaseNavigator"]
check("the ReAct lane builds the navigator tool", len(lc) == 1)
if lc:
    check("ReAct description", h(lc[0].description) == "775d6855b6d8747b", h(lc[0].description))
    check("ReAct argument schema", h(lc[0].args_schema.model_json_schema()) == "385bd7ba6d48fe98",
          h(lc[0].args_schema.model_json_schema()))

print("[2] what is signed — unchanged for an agent without `query`")
check("componentConfig canonicalises to the enabled component set only",
      pcc._canon_component_config(PROFILE["componentConfig"]) == ["database_navigator"],
      pcc._canon_component_config(PROFILE["componentConfig"]))
check("portable capability signature", h(pcc.signature_for(PROFILE, "capability")) == "e5cc401c039d18bf",
      h(pcc.signature_for(PROFILE, "capability")))
sig = dep.build_deployment_signature({"provider": "Google", "model": "gemini-3.1-flash-lite"},
                                     {"tools": [{"name": "base_readQuery", "args": ["sql"]}], "prompts": []},
                                     [{"server_id": "uderia-shell", "opted_in": False, "user_tools": []}])
check("deployment signature carries only llm / catalog / connectors", sorted(sig) == ["catalog", "connectors", "llm"], sorted(sig))
check("deployment hash", dep.deployment_hash(sig)[:16] == "440ae273d3daac62", dep.deployment_hash(sig)[:16])

print("[3] what a pack carries — unchanged")
check("componentConfig travels as today (incl. the pre-existing sourceId)",
      pcc.project_to_manifest(PROFILE, keys=["componentConfig"])
      == {"componentConfig": {"database_navigator": {"enabled": True, "sourceId": "conn:abc"}}},
      json.dumps(pcc.project_to_manifest(PROFILE, keys=["componentConfig"])))

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — an agent without `query` is byte-identical"))
sys.exit(1 if FAIL else 0)
