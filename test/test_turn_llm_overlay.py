"""A turn's in-turn LLM tasks use ITS model — even when a second overlay is stacked on the first.

FOUND LIVE (trust campaign 3, 2026-10-28, Optimize · P2, session c75fdbb4): an Optimize agent
driven as a `profile_id` override, on a governed tool surface. The override installs its LLM in a
per-turn STATE overlay — `ChainMap({llm, llm_provider, llm_model, …}, APP_STATE)`; the governed
surface then wraps THAT overlay in a second one — `ChainMap({structured_tools, …}, override)`.
`mcp_adapter.turn_llm` looked only at `maps[0]`, which is now the surface's slots and carries no
`llm`, so it fell through to the PER-USER slot. The override had set the per-user PROVIDER to its
own (OpenRouter) but leaves the per-user INSTANCE alone (its client lives in the overlay), so the
final report called the user's default Google client as OpenRouter: "'Client' object has no
attribute 'chat'", an empty answer, every station quiet. `turn_server_id` read `maps[0]` the same
way.

The rule: an overlay slot is found at ANY depth of the stacked ChainMaps, above the process-global
APP_STATE; the instance, provider and model come from the SAME overlay map. CI-pure.
"""
import os
import sys
from collections import ChainMap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


from trusted_data_agent.core import config as C  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter as A  # noqa: E402


class _GoogleClient:      # the user's default client — has no `.chat`
    pass


class _OpenRouterClient:  # the override's client
    chat = object()


USER = "test-user-turn-llm-overlay"
DEFAULT, OVERRIDE = _GoogleClient(), _OpenRouterClient()
C.set_user_llm_instance(DEFAULT, USER)
C.set_user_provider("OpenRouter", USER)       # what the override writes per user
C.set_user_model("amazon/nova-2-lite-v1", USER)

app_state = {"llm": DEFAULT, "mcp_tools": {}}
override = ChainMap({"llm": OVERRIDE, "llm_provider": "OpenRouter", "llm_model": "amazon/nova-2-lite-v1",
                     "mcp_server_id": "server-override", "mcp_tools": {}}, app_state)
surface = ChainMap({"structured_tools": {}, "tools_context": "", "prompts_context": ""}, override)

print("\n── §1 one overlay (the case that already worked)")
inst, prov, model = A.turn_llm(override, USER)
check("the override's client", inst is OVERRIDE)
check("…with the override's provider and model", (prov, model) == ("OpenRouter", "amazon/nova-2-lite-v1"))

print("\n── §2 the governed surface stacked on the override (P2)")
inst, prov, model = A.turn_llm(surface, USER)
check("the override's client, not the user's default", inst is OVERRIDE, type(inst).__name__)
check("…with the provider and model of the SAME overlay", (prov, model) == ("OpenRouter", "amazon/nova-2-lite-v1"))
check("the server id is found through the stack too", A.turn_server_id(surface, USER) == "server-override")
deep = ChainMap({"structured_tools": {}}, surface)
check("…at any depth", A.turn_llm(deep, USER)[0] is OVERRIDE and A.turn_server_id(deep, USER) == "server-override")

print("\n── §3 no overlay LLM → the per-user registry, never the process-global slot")
plain_surface = ChainMap({"structured_tools": {}}, app_state)
check("a surface over APP_STATE alone reads the per-user client", A.turn_llm(plain_surface, USER)[0] is DEFAULT)
check("…and the global APP_STATE['llm'] is never mistaken for an overlay slot",
      A.turn_llm(ChainMap({}, {"llm": OVERRIDE}), USER)[0] is DEFAULT)
check("a plain dict reads the per-user client", A.turn_llm(app_state, USER)[0] is DEFAULT)
check("no user, no overlay → the process-global slot (the documented last resort)",
      A.turn_llm(app_state, None)[0] is DEFAULT)

print("\n── §4 the executor stacks the surface on whatever STATE it holds")
import inspect  # noqa: E402
from trusted_data_agent.agent import executor as EX  # noqa: E402
src = inspect.getsource(EX.PlanExecutor._apply_surface_overlay)
check("the surface overlay wraps the base state (so the override sits one level down)",
      "ChainMap(slots, base)" in src)

print("\n── §5 the executor's own calls: a matching client is built when the override's LLM differs")
# Campaign 3, second full run: on the Ideate + tools lane the outcome revision went through
# `_call_llm_and_update_tokens`, whose guard compared the profile's provider with the PER-USER
# provider — which the override had already set to its own — so it read "same LLM", built no
# client, and fell back to the default Google client with provider OpenRouter.
import types  # noqa: E402
import trusted_data_agent.core.config_manager as CM  # noqa: E402


class _CM:
    def get_default_profile_id(self, u): return "p-default"
    def get_profile(self, pid, u): return {"id": pid, "llmConfigurationId": "cfg-google"}
    def get_llm_configurations(self, u): return [{"id": "cfg-google", "provider": "Google", "model": "gemini-3.1-flash-lite"}]


_orig = CM.get_config_manager
CM.get_config_manager = lambda: _CM()
try:
    exe = EX.PlanExecutor.__new__(EX.PlanExecutor)
    exe.user_uuid = USER
    base = exe._profile_llm_baseline()
    check("the baseline is the DEFAULT profile's LLM, not the per-user provider the override wrote",
          base == ("Google", "gemini-3.1-flash-lite"), base)
    check("…so an OpenRouter override differs from it and gets its own client",
          ("OpenRouter", "amazon/nova-2-lite-v1") != base)
finally:
    CM.get_config_manager = _orig

CM.get_config_manager = lambda: types.SimpleNamespace(get_default_profile_id=lambda u: None)
try:
    check("no readable default → the per-user values, as before",
          exe._profile_llm_baseline() == ("OpenRouter", "amazon/nova-2-lite-v1"))
finally:
    CM.get_config_manager = _orig
gsrc = inspect.getsource(EX.PlanExecutor)
check("the client guard reads the baseline, never the per-user provider directly",
      "current_user_provider, current_user_model = self._profile_llm_baseline()" in gsrc
      and "current_user_provider = get_user_provider(self.user_uuid)" not in gsrc)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
