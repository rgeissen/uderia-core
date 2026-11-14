#!/usr/bin/env python3
"""
Module tests — @TAG profile-override / provider cross-user ISOLATION (finding 1.2).

Two independent fixes are asserted here (both CI-pure, no live server):

  Commit 1 — per-executor STATE overlay (agent/engines/optimize_engine.py):
    an @TAG override installs its tool/prompt/LLM catalog in a per-executor ChainMap overlay
    instead of mutating the process-global APP_STATE, so a concurrent turn can never read
    another turn's overridden catalog. (Primary coverage lives in test_fusion_structural.py;
    a couple of core invariants are re-asserted here as a focused home for 1.2.)

  Commit 2 — provider-global de-race (llm/handler.py):
    the LLM handler's history normalization + Bedrock ARN provider are threaded off the per-call
    provider (falling back to the global), removing a PRE-EXISTING cross-user race where two
    concurrent turns on different providers both read the shared APP_CONFIG.CURRENT_PROVIDER.

Run:  python test/test_provider_globals_isolation.py
"""

import os
import sys
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


# ============================================================ Commit 1 — per-executor overlay
print("\n[1.2 / Commit 1] per-executor STATE overlay isolates the 8 slots; global untouched")
from trusted_data_agent.agent.engines.optimize_engine import (  # noqa: E402
    apply_override_state_overlay, restore_override_app_state,
)
import copy as _copy


class _Ex:
    def __init__(self, deps):
        self.dependencies = deps
        self.profile_llm_instance = None
        self.profile_override_applied = True
        self.user_uuid = "u1"
        self.original_provider = "Google"
        self.original_model = "gemini"
        self.original_provider_details = {}
        self.current_provider = "Override"
        self.current_model = "override-model"


def _rebuild(tool_to_exclude=None, state=None):
    return ("TC:" + ",".join(sorted(state.get("mcp_tools", {}).keys())), "PC")


_GLOBAL = {"llm": "DEF_LLM", "mcp_tools": {"d": 1}, "mcp_prompts": {}, "structured_tools": {},
           "structured_prompts": {}, "mcp_client": "DEF_CLIENT", "tool_scopes": {"x": "column"}}
_before = _copy.deepcopy(_GLOBAL)
_ex = _Ex({"STATE": _GLOBAL})
_ov = apply_override_state_overlay(
    _ex, _GLOBAL,
    {"mcp_client": "OVR", "mcp_tools": {"o": 2}, "mcp_prompts": {}, "structured_tools": {}, "structured_prompts": {}},
    llm="OVR_LLM", rebuild_context=_rebuild)
check("overlay carries the override catalog (mcp_tools)", _ov["mcp_tools"] == {"o": 2})
check("overlay carries the override LLM", _ov["llm"] == "OVR_LLM")
check("overlay tools_context derived from the OVERRIDE catalog", _ov["tools_context"] == "TC:o")
check("non-overridden slot falls through to the global", _ov["tool_scopes"] == {"x": "column"})
check("profile_llm_instance set", _ex.profile_llm_instance == "OVR_LLM")
check("GLOBAL APP_STATE is UNMUTATED (the cross-user-bleed fix)", _GLOBAL == _before)
check("executor.dependencies['STATE'] is the overlay, not the global", _ex.dependencies["STATE"] is _ov)

# restore is provider-only now (no app_state / rebuild args required)
_sig = inspect.signature(restore_override_app_state)
check("restore_override_app_state signature: app_state + rebuild_context are optional/defaulted",
      _sig.parameters["app_state"].default is None and _sig.parameters["rebuild_context"].default is None)


# ============================================================ Commit 2 — handler provider de-race
print("\n[1.2 / Commit 2] handler history-normalization + Bedrock provider threaded per-call")
import trusted_data_agent.llm.handler as h  # noqa: E402

_src = open(os.path.join(os.path.dirname(__file__), "..", "src",
            "trusted_data_agent", "llm", "handler.py")).read()

check("history Google-branch threaded off `provider` (not the global)",
      'if provider == "Google" and provider_history' in _src)
check("denormalize uses the threaded `provider`",
      "_denormalize_history(cleaned_history, provider)" in _src)
check("BOTH call sites pass provider=effective_provider",
      _src.count("_condense_and_clean_history(current_history, provider=effective_provider)") == 2)
check("Bedrock ARN provider prefers the USER-KEYED getter",
      "get_user_model_provider_in_profile(user_uuid)" in _src)
check("Bedrock still falls back to the global (no regression)",
      "APP_CONFIG.CURRENT_MODEL_PROVIDER_IN_PROFILE or " in _src)

# signature: provider is optional and defaults to the global (backward compatible)
_hsig = inspect.signature(h._condense_and_clean_history)
check("_condense_and_clean_history(history, provider=None) — backward compatible",
      "provider" in _hsig.parameters and _hsig.parameters["provider"].default is None)

# behavior: the per-call provider is honored even when the global is set to a DIFFERENT provider
h.APP_CONFIG.CURRENT_PROVIDER = "Anthropic"  # deliberately-wrong global
_hist = [{"role": "user", "content": "hi", "isValid": True}, {"role": "assistant", "content": "yo"}]
_a = h._condense_and_clean_history(list(_hist), provider="Anthropic")
_g = h._condense_and_clean_history(list(_hist), provider="Google")
_d = h._condense_and_clean_history(list(_hist))  # default path -> reads the global
check("threaded provider path runs on dict history (both providers) without error",
      isinstance(_a, list) and isinstance(_g, list))
check("default (no provider) path still works — reads the global",
      isinstance(_d, list))


# ============================================================ deferred LLM client ("oldschool" placeholder)
print("\n[oldschool] deferred LLM client creation: placeholder dict is materialized, or fails CLEARLY")
import asyncio as _asyncio  # noqa: E402
import trusted_data_agent.llm.client_factory as _cf  # noqa: E402
import trusted_data_agent.core.configuration_service as _cs  # noqa: E402

_hsrc = open(os.path.join(os.path.dirname(__file__), "..", "src",
             "trusted_data_agent", "llm", "handler.py")).read()
check("call_llm_api detects a placeholder LLM at the chokepoint",
      'isinstance(llm_instance, dict) and llm_instance.get("placeholder")' in _hsrc)
check("placeholder is materialized via the deferred-creation helper",
      "await _materialize_placeholder_llm(" in _hsrc)
check("materialization uses the POOLED client factory (no network round-trip)",
      "get_or_create_llm_client(prov, mdl, credentials)" in _hsrc)
check("graceful contract: raise_on_error=False returns empty, not a crash",
      'return "", 0, 0, effective_provider, effective_model' in _hsrc)


async def _mat_tests():
    ph = {"placeholder": True, "provider": "OpenRouter", "model": "meta-llama/llama-4-scout"}

    # success: creds present -> real client materialized
    async def _creds_ok(uid, prov): return {"status": "success", "credentials": {"openrouter_api_key": "sk-x"}}
    async def _client_ok(prov, model, creds, validate=False):
        class _C:  # minimal client with the .chat attr the provider branch expects
            chat = object()
        return _C()
    _cs.retrieve_credentials_for_provider = _creds_ok
    _cf.get_or_create_llm_client = _client_ok
    c = await h._materialize_placeholder_llm(ph, "u1", "OpenRouter", "llama")
    check("placeholder materialized to a real client when credentials exist", hasattr(c, "chat"))

    # NO credentials -> clear, actionable RuntimeError (the real environment case)
    async def _creds_none(uid, prov): return {"status": "error", "credentials": None}
    async def _client_fail(prov, model, creds, validate=False):
        raise ValueError("OpenRouter API key is required but not provided")
    _cs.retrieve_credentials_for_provider = _creds_none
    _cf.get_or_create_llm_client = _client_fail
    try:
        await h._materialize_placeholder_llm(ph, "u1", "OpenRouter", "llama")
        check("no-creds should raise a clear error", False)
    except RuntimeError as e:
        check("no-creds -> clear actionable error naming the provider + credentials",
              "no credentials" in str(e).lower() and "OpenRouter" in str(e))
    except Exception:
        check("no-creds -> RuntimeError (not a cryptic AttributeError)", False)

    # incomplete config -> clear error, never a silent crash
    try:
        await h._materialize_placeholder_llm({"placeholder": True}, None, None, None)
        check("incomplete config should raise", False)
    except RuntimeError as e:
        check("incomplete config -> clear error", "incomplete" in str(e).lower())


_asyncio.run(_mat_tests())


# ============================================================ Commit 3 — concurrent users, one process
# (prod 2026-09-20: admin's Optimize turn on OpenRouter/server A ran beside the demo account's Gemini
# turn on server B; the admin's tool calls were rerouted to B's server and its final report was
# dispatched as Google against an AsyncOpenAI client. Both roots: reading process-global slots.)
print("\n[Commit 3] adapter in-turn LLM tasks + tool routing read THIS turn's identity, never the global")
from collections import ChainMap as _CM  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter as _ad  # noqa: E402
from trusted_data_agent.core import config as _cfg  # noqa: E402

# --- turn_llm: overlay wins outright and carries its own provider/model
_over = _CM({"llm": "OVR_LLM", "llm_provider": "OpenRouter", "llm_model": "gemma"}, {"llm": "GLOBAL_LLM"})
check("overlay: instance is the override's", _ad.turn_llm(_over, "u1")[0] == "OVR_LLM")
check("overlay: provider/model are the override's, not the global's", _ad.turn_llm(_over, "u1")[1:] == ("OpenRouter", "gemma"))

# --- no overlay: the PER-USER registry is the authority (the executor's own source), global last
_cfg.APP_STATE.setdefault("llm_by_user", {})["uA"] = "A_LLM"
_cfg.APP_STATE.setdefault("current_provider_by_user", {})["uA"] = "OpenRouter"
_cfg.APP_STATE.setdefault("current_model_by_user", {})["uA"] = "gemma"
_cfg.APP_STATE["llm_by_user"].pop("uB", None)
_cfg.APP_STATE["current_provider_by_user"]["uB"] = "Google"
_cfg.APP_STATE["current_model_by_user"]["uB"] = "gemini"
_glob = {"llm": "B_GLOBAL_LLM"}   # what user B's activation left in the process-global slot
check("user A (registered) gets A's instance + provider while the global slot holds B's",
      _ad.turn_llm(_glob, "uA") == ("A_LLM", "OpenRouter", "gemma"))
check("user B (no registered instance) falls back to the global instance but keeps B's provider",
      _ad.turn_llm(_glob, "uB") == ("B_GLOBAL_LLM", "Google", "gemini"))
check("no user at all: global instance, provider/model left to the handler (None)",
      _ad.turn_llm(_glob, None) == ("B_GLOBAL_LLM", None, None))

# --- every in-turn LLM task passes the turn's provider/model (structural: no site left on the global)
import inspect as _insp  # noqa: E402
for _fn in ("_invoke_llm_filter_task", "_invoke_core_llm_task", "_invoke_final_report_task",
            "_invoke_complex_prompt_report_task"):
    _src = _insp.getsource(getattr(_ad, _fn))
    check(f"{_fn} resolves via turn_llm and passes current_provider/current_model",
          "turn_llm(STATE, user_uuid)" in _src and "current_provider=_turn_provider" in _src
          and "current_model=_turn_model" in _src and "STATE.get('llm')" not in _src)

# --- get_user_mcp_server_id: validated against the USER'S servers, never the global client
class _FakeCM:
    def __init__(self, servers): self._s = servers
    def get_mcp_servers(self, user_uuid=None): return self._s
import trusted_data_agent.core.config_manager as _cmm  # noqa: E402
_orig_gcm = _cmm.get_config_manager
_cmm.get_config_manager = lambda: _FakeCM([{"id": "srv-A"}, {"id": "srv-A2"}])
_cfg.APP_STATE.setdefault("current_server_id_by_user", {})["uA"] = "srv-A"
_cfg.APP_STATE["mcp_client"] = type("C", (), {"connections": {"srv-B": 1}})()   # B's client is global
check("A's own server id is kept even though the global client holds only B's server",
      _cfg.get_user_mcp_server_id("uA") == "srv-A")
check("… and the stored id was not rewritten to B's", _cfg.APP_STATE["current_server_id_by_user"]["uA"] == "srv-A")
_cfg.APP_STATE["current_server_id_by_user"]["uA"] = "srv-GONE"
check("a genuinely stale id (absent from A's config) falls back to A's OWN first server, never B's",
      _cfg.get_user_mcp_server_id("uA") == "srv-A")
_cmm.get_config_manager = _orig_gcm
_cfg.APP_STATE.pop("mcp_client", None)

# --- invoke_mcp_tool prefers the overlay's own server id
# PROP-006 phase 2: `invoke_mcp_tool` delegates to `_invoke_mcp_tool_impl`, which carries the
# routing (test_sql_gate_lanes asserts the wrapper only delegates)
_isrc = _insp.getsource(_ad._invoke_mcp_tool_impl)
check("invoke_mcp_tool reads the overlay's mcp_server_id before the per-user id",
      # the ONE resolution is `turn_server_id` (overlay slot → the turn's armed primary → the
      # per-user id); the inline fallback it replaced read the per-user id straight after the
      # overlay, which two concurrent turns of one user raced on (OC-F1, test_turn_server_pin.py)
      "turn_server_id(STATE, user_uuid)" in _isrc and "server_id or get_user_mcp_server_id" not in _isrc)
_osrc = _insp.getsource(apply_override_state_overlay)
check("the overlay carries llm_provider / llm_model / mcp_server_id",
      all(k in _osrc for k in ("slots['llm_provider']", "slots['llm_model']", "slots['mcp_server_id']")))


# ============================================================ summary
print(f"\n{'=' * 60}\nprovider/state isolation: {_passed} passed, {_failed} failed\n{'=' * 60}")
sys.exit(1 if _failed else 0)
