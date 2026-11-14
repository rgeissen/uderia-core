"""
Module tests — Agent-pack MCP server resolution & v1.0 normalizer fidelity.

CI-pure (no network, no DB writes; config_manager is monkeypatched). Covers the
Jul 2026 fix chain for the "External Research imported as pure-LLM" flaw:
  * _normalize_v10_to_v11 preserves useMcpTools / mcpServerName / allowed_tools
    (previously dropped → llm_only+tools experts silently degraded to pure LLM),
  * _mcp_name_map builds a case-insensitive name → id map (fail-open),
  * _build_profile resolves a manifest mcpServerName against the importing
    user's installed servers BY NAME — name match wins over the pack-level
    mcp_server_id; pack-level id stays the fallback; unresolvable references
    surface as import warnings, never hard failures.

Sep 2026 — explicit per-agent bindings: `mcp_bindings` {tag: server_id} (validated
against the target user's OWN servers, foreign ids refused) wins over the name
auto-match and the pack-level id at every _build_profile call site (import /
update_in_place / subscribe); `pack_mcp_requirements` is the pure row model the
binding dialog renders; `read_manifest_profiles` reads a pack without importing.

Run:  python test/test_pack_mcp_resolution.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.core import agent_pack_manager as APM  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


class _FakeConfigManager:
    def __init__(self, servers):
        self._servers = servers

    def get_mcp_servers(self, user_uuid=None):
        if isinstance(self._servers, Exception):
            raise self._servers
        return self._servers


def _mgr():
    return APM.AgentPackManager()


def main():
    print("=" * 70)
    print("Agent-pack MCP resolution — module tests")
    print("=" * 70)
    mgr = _mgr()

    # ── 1. v1.0 → v1.1 normalizer preserves the MCP tool-calling fields ─────
    print("\n[1] _normalize_v10_to_v11 field fidelity")
    manifest = {
        "format_version": "1.0",
        "name": "vat-like",
        "coordinator": {"tag": "VAT", "name": "Coordinator"},
        "experts": [
            {"tag": "KB_SME", "profile_type": "rag_focused", "collection_ref": "col-1"},
            {"tag": "EXTERNAL_SME", "profile_type": "llm_only",
             "useMcpTools": True, "mcpServerName": "Google Search",
             "allowed_tools": ["google_search"], "classification_mode": "light"},
        ],
        "collections": [],
    }
    norm = mgr._normalize_v10_to_v11(manifest)
    profs = {p["tag"]: p for p in norm["profiles"]}
    ext = profs["EXTERNAL_SME"]
    check("useMcpTools preserved", ext.get("useMcpTools") is True)
    check("mcpServerName preserved", ext.get("mcpServerName") == "Google Search")
    check("allowed_tools preserved", ext.get("allowed_tools") == ["google_search"])
    check("plain expert untouched (no spurious fields)",
          "useMcpTools" not in profs["KB_SME"] and "mcpServerName" not in profs["KB_SME"])
    check("coordinator converted", profs["VAT"]["profile_type"] == "genie")
    check("format upgraded", norm["format_version"] == "1.1")

    # ── 2. _mcp_name_map ─────────────────────────────────────────────────────
    print("\n[2] _mcp_name_map")
    import trusted_data_agent.core.config_manager as CM
    real_get = CM.get_config_manager
    try:
        CM.get_config_manager = lambda: _FakeConfigManager([
            {"id": "srv-1", "name": "Google Search"},
            {"id": "srv-2", "name": "  Teradata  "},
            {"id": "srv-3"},              # nameless → skipped
            {"name": "No ID"},            # id-less → skipped
        ])
        m = APM.AgentPackManager._mcp_name_map("user-1")
        check("case-insensitive key", m.get("google search") == "srv-1")
        check("name trimmed", m.get("teradata") == "srv-2")
        check("degenerate rows skipped", len(m) == 2)

        CM.get_config_manager = lambda: _FakeConfigManager(RuntimeError("db down"))
        check("fail-open on error", APM.AgentPackManager._mcp_name_map("user-1") == {})
    finally:
        CM.get_config_manager = real_get

    # ── 3. _build_profile per-profile resolution ─────────────────────────────
    print("\n[3] _build_profile MCP binding")
    name_map = {"google search": "srv-google"}

    # 3a. llm_only + useMcpTools, name resolves (THE production case, fixed)
    warnings = []
    p = mgr._build_profile(
        {"tag": "EXTERNAL_SME", "profile_type": "llm_only",
         "useMcpTools": True, "mcpServerName": "Google Search"},
        {}, "llm-1", None, mcp_name_to_id=name_map, warnings=warnings)
    check("llm_only: useMcpTools set", p.get("useMcpTools") is True)
    check("llm_only: name-resolved binding", p.get("mcpServerId") == "srv-google")
    check("llm_only: no warning on clean resolve", warnings == [])

    # 3b. name resolution WINS over pack-level id
    p = mgr._build_profile(
        {"tag": "EXTERNAL_SME", "profile_type": "llm_only",
         "useMcpTools": True, "mcpServerName": "Google Search"},
        {}, "llm-1", "srv-pack-level", mcp_name_to_id=name_map, warnings=[])
    check("name match beats pack-level id", p.get("mcpServerId") == "srv-google")

    # 3c. unresolvable name, no pack-level id → unbound + warning
    warnings = []
    p = mgr._build_profile(
        {"tag": "EXTERNAL_SME", "profile_type": "llm_only",
         "useMcpTools": True, "mcpServerName": "Google Search"},
        {}, "llm-1", None, mcp_name_to_id={}, warnings=warnings)
    check("unresolvable: no binding", "mcpServerId" not in p)
    check("unresolvable: one warning", len(warnings) == 1)
    check("unresolvable: warning names server+tag",
          "Google Search" in warnings[0] and "@EXTERNAL_SME" in warnings[0])

    # 3d. unresolvable name WITH pack-level fallback → bound to fallback + warning
    warnings = []
    p = mgr._build_profile(
        {"tag": "EXTERNAL_SME", "profile_type": "llm_only",
         "useMcpTools": True, "mcpServerName": "Google Search"},
        {}, "llm-1", "srv-pack-level", mcp_name_to_id={}, warnings=warnings)
    check("fallback binding used", p.get("mcpServerId") == "srv-pack-level")
    check("fallback still warns about the name", len(warnings) == 1 and "pack-level" in warnings[0])

    # 3e. tool_enabled: name resolution applies there too
    p = mgr._build_profile(
        {"tag": "OPT", "profile_type": "tool_enabled", "mcpServerName": "Google Search"},
        {}, "llm-1", "srv-pack-level", mcp_name_to_id=name_map, warnings=[])
    check("tool_enabled: name-resolved binding", p.get("mcpServerId") == "srv-google")

    # 3f. tool_enabled without a name → pack-level id (unchanged legacy path)
    p = mgr._build_profile(
        {"tag": "OPT", "profile_type": "tool_enabled"},
        {}, "llm-1", "srv-pack-level", mcp_name_to_id=name_map, warnings=[])
    check("tool_enabled legacy: pack-level id", p.get("mcpServerId") == "srv-pack-level")

    # 3g. useMcpTools with NO name and NO server at all → warning
    warnings = []
    p = mgr._build_profile(
        {"tag": "IDEAT", "profile_type": "llm_only", "useMcpTools": True},
        {}, "llm-1", None, mcp_name_to_id={}, warnings=warnings)
    check("tools-without-any-server warns", len(warnings) == 1 and "@IDEAT" in warnings[0])

    # 3h. defensive: legacy call shape (no new kwargs) still works
    p = mgr._build_profile(
        {"tag": "LEGACY", "profile_type": "llm_only", "useMcpTools": True,
         "mcpServerName": "Google Search"},
        {}, "llm-1", "srv-pack-level")
    check("legacy call shape: pack-level binding", p.get("mcpServerId") == "srv-pack-level")

    # 3i. pure-LLM profile: resolution never invents a binding
    p = mgr._build_profile(
        {"tag": "PURE", "profile_type": "llm_only"},
        {}, "llm-1", "srv-pack-level", mcp_name_to_id=name_map, warnings=[])
    check("pure llm_only stays unbound", "mcpServerId" not in p and "useMcpTools" not in p)

    # ── 4. Explicit per-agent bindings (Sep 2026) ────────────────────────────
    print("\n[4] _normalize_mcp_bindings / _tag_binding")
    try:
        CM.get_config_manager = lambda: _FakeConfigManager([
            {"id": "srv-nibrs", "name": "Teradata MCP - nibrs_source_agent"},
            {"id": "srv-case", "name": "Teradata MCP - case_source_agent"},
        ])
        valid, rejected = APM.AgentPackManager._normalize_mcp_bindings(
            "user-1", {"NIBRS": "srv-nibrs", " CASEDF ": "srv-case"})
        check("owned ids accepted, tags trimmed",
              valid == {"NIBRS": "srv-nibrs", "CASEDF": "srv-case"} and rejected == [])
        valid, rejected = APM.AgentPackManager._normalize_mcp_bindings(
            "user-1", {"NIBRS": "srv-nibrs", "CASEDF": "srv-someone-elses", "PDOCS": None})
        check("foreign id rejected by tag, never written",
              valid == {"NIBRS": "srv-nibrs"} and rejected == ["CASEDF", "PDOCS"])
        check("None / empty / non-dict → empty",
              APM.AgentPackManager._normalize_mcp_bindings("user-1", None) == ({}, [])
              and APM.AgentPackManager._normalize_mcp_bindings("user-1", {}) == ({}, [])
              and APM.AgentPackManager._normalize_mcp_bindings("user-1", ["x"]) == ({}, []))
        CM.get_config_manager = lambda: _FakeConfigManager(RuntimeError("db down"))
        check("fail-open on lookup error",
              APM.AgentPackManager._normalize_mcp_bindings("user-1", {"NIBRS": "srv-nibrs"}) == ({}, []))
    finally:
        CM.get_config_manager = real_get
    tb = APM.AgentPackManager._tag_binding
    check("tag binding exact", tb({"NIBRS": "srv-nibrs"}, "NIBRS") == "srv-nibrs")
    check("tag binding case-insensitive", tb({"NIBRS": "srv-nibrs"}, "nibrs") == "srv-nibrs")
    check("tag binding absent → None", tb({"NIBRS": "srv-nibrs"}, "CASEDF") is None
          and tb(None, "NIBRS") is None and tb({"NIBRS": "x"}, None) is None)

    # ── 5. _build_profile: explicit binding wins over name AND pack-level ─────
    print("\n[5] _build_profile explicit binding precedence")
    name_map2 = {"teradata mcp - nibrs_source_agent": "srv-nibrs"}
    warnings = []
    p = mgr._build_profile(
        {"tag": "NIBRS", "profile_type": "tool_enabled", "requires_mcp": True,
         "mcpServerName": "Teradata MCP - nibrs_source_agent"},
        {}, "llm-1", "srv-pack-level", mcp_name_to_id=name_map2, warnings=warnings,
        mcp_tag_bindings={"NIBRS": "srv-chosen"})
    check("explicit binding beats name match", p.get("mcpServerId") == "srv-chosen")
    check("explicit binding: no warning", warnings == [])
    warnings = []
    p = mgr._build_profile(
        {"tag": "CASEDF", "profile_type": "tool_enabled", "requires_mcp": True,
         "mcpServerName": "Teradata MCP - case_source_agent"},
        {}, "llm-1", None, mcp_name_to_id=name_map2, warnings=warnings,
        mcp_tag_bindings={"CASEDF": "srv-chosen-case"})
    check("explicit binding resolves an otherwise-unresolvable name without a warning",
          p.get("mcpServerId") == "srv-chosen-case" and warnings == [])
    warnings = []
    p = mgr._build_profile(
        {"tag": "CASEDF", "profile_type": "tool_enabled", "requires_mcp": True,
         "mcpServerName": "Teradata MCP - case_source_agent"},
        {}, "llm-1", "srv-pack-level", mcp_name_to_id=name_map2, warnings=warnings,
        mcp_tag_bindings={"NIBRS": "srv-chosen"})
    check("a binding for ANOTHER tag does not leak; name miss falls back to pack-level",
          p.get("mcpServerId") == "srv-pack-level" and len(warnings) == 1 and "@CASEDF" in warnings[0])
    p = mgr._build_profile(
        {"tag": "EXT", "profile_type": "llm_only", "useMcpTools": True,
         "mcpServerName": "Google Search"},
        {}, "llm-1", None, mcp_name_to_id={"google search": "srv-google"}, warnings=[],
        mcp_tag_bindings={"EXT": "srv-mine"})
    check("llm_only+tools honours the explicit binding", p.get("mcpServerId") == "srv-mine")
    p = mgr._build_profile(
        {"tag": "PURE", "profile_type": "llm_only"},
        {}, "llm-1", None, mcp_name_to_id={}, warnings=[], mcp_tag_bindings={"PURE": "srv-mine"})
    check("a binding cannot invent MCP on a pure-LLM agent", "mcpServerId" not in p)

    # ── 6. pack_mcp_requirements — the dialog's rows ─────────────────────────
    print("\n[6] pack_mcp_requirements / profile_needs_mcp")
    profiles = [
        {"tag": "POLICE", "name": "Coordinator", "profile_type": "genie"},
        {"tag": "PDOCS", "name": "Docs", "profile_type": "rag_focused"},
        {"tag": "NIBRS", "name": "NIBRS analyst", "profile_type": "tool_enabled", "requires_mcp": True,
         "mcpServerName": "Teradata MCP - nibrs_source_agent"},
        {"tag": "CASEDF", "name": "CASE analyst", "profile_type": "tool_enabled", "requires_mcp": True,
         "mcpServerName": "Teradata MCP - case_source_agent"},
        {"tag": "EXT", "name": "Research", "profile_type": "llm_only", "useMcpTools": True},
    ]
    rows = APM.AgentPackManager.pack_mcp_requirements(
        profiles, {"teradata mcp - nibrs_source_agent": "srv-nibrs"},
        tag_bindings={"EXT": "srv-mine"})
    by_tag = {r["tag"]: r for r in rows}
    check("only MCP-needing agents listed, manifest order kept",
          [r["tag"] for r in rows] == ["NIBRS", "CASEDF", "EXT"])
    check("name match reported", by_tag["NIBRS"]["resolved_mcp_server_id"] == "srv-nibrs"
          and by_tag["NIBRS"]["resolution"] == "name")
    check("unresolved reported honestly", by_tag["CASEDF"]["resolved_mcp_server_id"] is None
          and by_tag["CASEDF"]["resolution"] is None
          and by_tag["CASEDF"]["mcp_server_name"] == "Teradata MCP - case_source_agent")
    check("explicit binding reported", by_tag["EXT"]["resolved_mcp_server_id"] == "srv-mine"
          and by_tag["EXT"]["resolution"] == "binding" and by_tag["EXT"]["mcp_server_name"] is None)
    check("rows carry the display name", by_tag["NIBRS"]["name"] == "NIBRS analyst")
    check("empty inputs → no rows", APM.AgentPackManager.pack_mcp_requirements([], {}) == []
          and APM.AgentPackManager.pack_mcp_requirements(None, None) == [])

    # ── 7. read_manifest_profiles on a real zip (no import) ──────────────────
    print("\n[7] read_manifest_profiles")
    import io, json as _json, tempfile, zipfile
    from pathlib import Path as _P
    with tempfile.TemporaryDirectory() as td:
        zp = _P(td) / "t.agentpack"
        man = {"format_version": "1.1", "name": "t", "version": "1.0.0",
               "profiles": [{"tag": "NIBRS", "name": "n", "profile_type": "tool_enabled", "role": "expert",
                             "requires_mcp": True, "mcpServerName": "X"}],
               "collections": []}
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("manifest.json", _json.dumps(man))
        m2, profs2 = mgr.read_manifest_profiles(zp)
        check("manifest read without import", m2.get("name") == "t" and len(profs2) == 1
              and profs2[0]["mcpServerName"] == "X")
        zp2 = _P(td) / "bad.agentpack"
        with zipfile.ZipFile(zp2, "w") as zf:
            zf.writestr("manifest.json", _json.dumps({"format_version": "1.1"}))
        try:
            mgr.read_manifest_profiles(zp2)
            check("invalid manifest refused", False)
        except ValueError:
            check("invalid manifest refused", True)

    # ── 7b. expand strategy: bindings follow the renamed tags ─────────────────
    print("\n[7b] _remap_bindings (expand)")
    rb = APM.AgentPackManager._remap_bindings
    check("remapped tag carries its binding",
          rb({"NIBRS": "srv-a", "CASEDF": "srv-b"}, {"NIBRS": "NIBRS2"}) == {"NIBRS2": "srv-a", "CASEDF": "srv-b"})
    check("no remap → unchanged; empty → {}",
          rb({"NIBRS": "srv-a"}, None) == {"NIBRS": "srv-a"} and rb(None, {"NIBRS": "NIBRS2"}) == {})
    import inspect as _insp
    _imp = _insp.getsource(APM.AgentPackManager.import_pack)
    _expand_idx = _imp.find('conflict_strategy == "expand"')
    _tail = _imp[_expand_idx:_expand_idx + 900]
    # Asserted as a PROPERTY, not as the literal call text: this pinned
    # "_apply_tag_remap(profiles, tag_remap)" verbatim and broke the day the remap
    # gained a third argument (the knowledge graphs) while the property it stands for
    # held perfectly. What matters is that the expand branch remaps EVERY tag-keyed
    # thing -- the profiles, the consumer's explicit bindings, and the KG entries.
    check("expand branch remaps the tags", "_apply_tag_remap(" in _tail)
    check("...the consumer's explicit bindings follow the renamed tags",
          "self._remap_bindings(mcp_tag_bindings, tag_remap)" in _tail)
    check("...and the knowledge-graph entries follow too (a second fork otherwise "
          "renames the profiles and leaves every graph pointing at the originals)",
          "knowledge_graphs" in _tail[:_tail.find("self._remap_bindings")])

    # ── 8. wiring: every _build_profile call site threads the bindings ───────
    print("\n[8] wiring")
    import ast, inspect
    src = inspect.getsource(APM)
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_build_profile"]
    check("_build_profile has call sites", len(calls) >= 5)
    check("every _build_profile call passes mcp_tag_bindings",
          all(any(k.arg == "mcp_tag_bindings" for k in c.keywords) for c in calls))
    sig_import = inspect.signature(APM.AgentPackManager.import_pack).parameters
    sig_sub = inspect.signature(APM.AgentPackManager.build_subscribed_profiles).parameters
    sig_uip = inspect.signature(APM.AgentPackManager._handle_update_in_place).parameters
    check("import_pack / build_subscribed_profiles accept mcp_bindings; update_in_place accepts the map",
          "mcp_bindings" in sig_import and "mcp_bindings" in sig_sub and "mcp_tag_bindings" in sig_uip)

    # ── 9. the fork route passes the update_in_place validation opt-ins through ──
    # A consumer whose older fork carried a STRICTER posture than the pack now ships
    # could not take the update through the fork route: the guard refused and the
    # route had no field to say "accepted" (found live, 2026-09-07).
    print("\n[9] fork route opt-ins")
    import os as _os
    _routes = open(_os.path.join(_os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                                 "api", "agent_pack_routes.py")).read()
    _rt = ast.parse(_routes)
    _fork = next(n for n in ast.walk(_rt) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == "fork_marketplace_agent_pack")
    _imp_calls = [n for n in ast.walk(_fork) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == "import_pack"]
    check("the fork route calls import_pack exactly once", len(_imp_calls) == 1)
    _kw = {k.arg for k in _imp_calls[0].keywords} if _imp_calls else set()
    check("...passing allow_posture_downgrade, force_drop_tags and dry_run",
          {"allow_posture_downgrade", "force_drop_tags", "dry_run"} <= _kw)
    _reads = {n.args[0].value for n in ast.walk(_fork) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
              and isinstance(n.func.value, ast.Name) and n.func.value.id == "data"
              and n.args and isinstance(n.args[0], ast.Constant)}
    check("...each read from the request body, not defaulted silently",
          {"allow_posture_downgrade", "force_drop_tags", "dry_run"} <= _reads)

    # ── 10. multi-server agents travel with ALL their servers (plan stage 2) ──
    # Ids are instance-local: the manifest names each server, the importer resolves the
    # names (or an explicit alias binding) to its OWN ids, and the signed capability of the
    # copy equals the publisher's — so a publisher signature can be reinstated.
    print("\n[10] multi-server agents")
    from trusted_data_agent.core import agent_mcp_servers as AMS
    from trusted_data_agent.core import profile_config_contract as PCC
    pub = {"tag": "MSOPT", "profile_type": "tool_enabled", "mcpServerId": "pub-sales",
           "tools": ["base_readQuery"],
           "additionalMcpServers": [{"id": "pub-crm", "alias": "crm",
                                     "tools": ["base_readQuery", "base_tableList"], "prompts": []}]}
    pub_names = {"pub-sales": "Sales MCP", "pub-crm": "CRM MCP"}
    listed = AMS.manifest_servers(pub, pub_names)
    check("export lists every server, the first first",
          [e["name"] for e in (listed or [])] == ["Sales MCP", "CRM MCP"])
    check("...the first server carries no alias; the additional server its alias and selection",
          listed and listed[0]["alias"] is None and listed[1]["alias"] == "crm"
          and listed[1]["tools"] == ["base_readQuery", "base_tableList"])
    check("a single-server agent exports no server list (its manifest is unchanged)",
          AMS.manifest_servers({"mcpServerId": "x"}, {"x": "X"}) is None)

    entry = {"tag": "MSOPT", "profile_type": "tool_enabled", "mcpServerName": "Sales MCP",
             "allowed_tools": ["base_readQuery"], "mcpServers": listed}
    consumer = {"sales mcp": "c-sales", "crm mcp": "c-crm", "other": "c-other"}
    warnings = []
    got = mgr._build_profile(entry, {}, "llm-1", None, mcp_name_to_id=consumer, warnings=warnings)
    check("import resolves the first server by name", got.get("mcpServerId") == "c-sales")
    check("import resolves the additional server by name to the consumer's own id",
          got.get("additionalMcpServers") == [{"id": "c-crm", "alias": "crm",
                                               "tools": ["base_readQuery", "base_tableList"],
                                               "prompts": []}])
    check("...with no warning", warnings == [])
    check("the copy's signed capability equals the publisher's (reinstatement possible)",
          PCC.capability_signature(got) == PCC.capability_signature(pub))
    check("...and a different alias is a different capability",
          PCC.capability_signature({**got, "additionalMcpServers": [
              {**got["additionalMcpServers"][0], "alias": "crm2"}]}) != PCC.capability_signature(pub))
    check("a single-server agent's capability signature carries no server key",
          "additionalMcpServers" not in PCC.capability_signature(
              {"profile_type": "tool_enabled", "mcpServerId": "x", "tools": ["a"]}))

    # explicit alias binding beats the name
    got = mgr._build_profile(entry, {}, "llm-1", None, mcp_name_to_id=consumer, warnings=[],
                             mcp_tag_bindings={"MSOPT": {"primary": "c-sales", "crm": "c-other"}})
    check("an explicit alias binding wins over the name",
          (got.get("additionalMcpServers") or [{}])[0].get("id") == "c-other")
    check("...and the dict form still binds the first server", got.get("mcpServerId") == "c-sales")

    # unresolvable additional server: left out and NAMED, never guessed
    warnings = []
    got = mgr._build_profile(entry, {}, "llm-1", None,
                             mcp_name_to_id={"sales mcp": "c-sales"}, warnings=warnings)
    check("an unresolvable additional server is left out", "additionalMcpServers" not in got)
    check("...and named in a warning",
          len(warnings) == 1 and "CRM MCP" in warnings[0] and "@MSOPT" in warnings[0])

    # an additional server resolving to the FIRST server is refused, not doubled
    warnings = []
    got = mgr._build_profile(entry, {}, "llm-1", None,
                             mcp_name_to_id={"sales mcp": "c-sales", "crm mcp": "c-sales"},
                             warnings=warnings)
    check("an additional server resolving to the first server is refused",
          "additionalMcpServers" not in got and len(warnings) == 1)

    # no first server → no additional servers
    got = mgr._build_profile(entry, {}, "llm-1", None, mcp_name_to_id={"crm mcp": "c-crm"},
                             warnings=[])
    check("no first server bound → no additional server bound", "additionalMcpServers" not in got)

    # binding normalisation: dict rows, foreign ids named by tag:alias
    try:
        CM.get_config_manager = lambda: _FakeConfigManager(
            [{"id": "c-sales", "name": "Sales MCP"}, {"id": "c-crm", "name": "CRM MCP"}])
        valid, rejected = APM.AgentPackManager._normalize_mcp_bindings(
            "u", {"MSOPT": {"primary": "c-sales", "crm": "c-crm"},
                  "BAD": {"primary": "c-sales", "crm": "someone-elses"}})
        check("a dict binding of owned servers is kept whole",
              valid.get("MSOPT") == {"primary": "c-sales", "crm": "c-crm"})
        check("a foreign alias id is rejected and named tag:alias", rejected == ["BAD:crm"])
    finally:
        CM.get_config_manager = real_get
    check("_remap_bindings carries a dict row through an expand remap",
          APM.AgentPackManager._remap_bindings({"MSOPT": {"primary": "a", "crm": "b"}},
                                               {"MSOPT": "MSOPT2"})
          == {"MSOPT2": {"primary": "a", "crm": "b"}})

    rows = APM.AgentPackManager.pack_mcp_requirements(
        [entry, {"tag": "ONE", "profile_type": "tool_enabled", "mcpServerName": "Sales MCP"}],
        consumer)
    check("the requirement row of a multi-server agent lists its additional servers",
          rows[0].get("additional_servers") == [{"alias": "crm", "mcp_server_name": "CRM MCP",
                                                 "resolved_mcp_server_id": "c-crm",
                                                 "resolution": "name"}])
    check("a single-server agent's row carries no additional_servers key",
          "additional_servers" not in rows[1])

    # the three places a manifest entry is written all stamp the server list
    import os as _os2
    _mgr_src = open(_os2.path.join(_os2.path.dirname(__file__), "..", "src", "trusted_data_agent",
                                   "core", "agent_pack_manager.py")).read()
    _mt = ast.parse(_mgr_src)
    _stamp_callers = {f.name for f in ast.walk(_mt)
                      if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                              and n.func.id == "_stamp_manifest_servers" for n in ast.walk(f))}
    check("file export and the subscribe/fork re-export both stamp the server list",
          {"export_pack", "_to_manifest_entry"} <= _stamp_callers)
    _pub = next(n for n in ast.walk(_rt) if isinstance(n, ast.FunctionDef)
                and n.name == "_publisher_mcp_requirement_entries")
    check("the marketplace requirement entries list the servers too",
          any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "manifest_servers" for n in ast.walk(_pub)))

    # the route accepts the dict binding shape and refuses a malformed one
    import importlib
    _R = importlib.import_module("trusted_data_agent.api.agent_pack_routes")
    check("the route parses a dict binding row",
          _R._parse_mcp_bindings({"MSOPT": {"primary": "a", "crm": "b", "x": ""}})
          == {"MSOPT": {"primary": "a", "crm": "b"}})
    try:
        _R._parse_mcp_bindings({"MSOPT": {"crm": 5}})
        check("a non-string server id in a dict row is refused", False)
    except ValueError:
        check("a non-string server id in a dict row is refused", True)

    print("\n" + "=" * 70)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
