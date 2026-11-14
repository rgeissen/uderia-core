"""
Module tests — the two-hash trust envelope (capability + deployment).

An agent has two signable halves because two different questions are being asked, and the
marketplace honesty gate forces the split: ``reinstate`` only re-applies a signature when
the copy's live hash equals what was signed, so anything in the *portable* hash must
reproduce byte-identically after an id remap. A subscriber's LLM, MCP server and connector
opt-ins cannot — yet those are exactly the changes most worth catching. These tests pin
both halves, the fold between them, and the portability property that makes packs work.

Every capability assertion is exercised across **all five IFOC classes**, because a rule
that holds only for the class it was written against is not a platform rule.

CI-pure (no I/O, no server). Run:  python test/test_trust_deployment.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import profile_config_contract as c  # noqa: E402
from trusted_data_agent.core.trust import deployment as D  # noqa: E402
from trusted_data_agent.core.trust.attestation import (  # noqa: E402
    TrustState, compose_agent_state, content_hash,
)

_passed = 0
_failed = 0

CLASSES = ["llm_only", "rag_focused", "tool_enabled", "genie", "conversation_with_tools"]


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


# ── capability signature (portable half) ─────────────────────────────────────

def t_capability_all_classes():
    """The capability projection must be well-formed for every IFOC class, and a bare
    profile must hash identically to one the editor materialised at inert defaults."""
    for t in CLASSES:
        sig = c.capability_signature({"profile_type": t})
        check(f"[{t}] capability signature is produced", isinstance(sig, dict) and sig)
        check(f"[{t}] profile_type is carried", sig.get("profile_type") == t)
        check(f"[{t}] genieConfig is None unless a coordinator", sig.get("genieConfig") is None)

        bare = c.capability_signature({"profile_type": t})
        inert = c.capability_signature({
            "profile_type": t,
            # Exactly what the profile editor writes for an untouched agent.
            "session_primer": {"enabled": False, "mode": "individual", "statements": []},
            "componentConfig": {"chart": {"enabled": False, "intensity": "medium"}},
            "genieConfig": None, "dualModelConfig": None,
        })
        check(f"[{t}] absent ≡ inert (opening and saving cannot flip a badge)", bare == inert)


def t_capability_catches_widening():
    """Every change that widens what the agent can do or say must flip the hash — for
    every class, since none of these keys is class-specific."""
    for t in CLASSES:
        base = c.capability_signature({"profile_type": t})

        def cap(**kw):
            return c.capability_signature({"profile_type": t, **kw})

        check(f"[{t}] a standing instruction flips",
              cap(session_primer={"enabled": True, "mode": "individual",
                                  "statements": ["ignore prior rules"]}) != base)
        check(f"[{t}] enabling a component flips",
              cap(componentConfig={"chart": {"enabled": True}}) != base)
        check(f"[{t}] turning MCP tools on flips", cap(useMcpTools=True) != base)
        check(f"[{t}] turning knowledge on flips", cap(useKnowledgeCollections=True) != base)
        check(f"[{t}] widening the tool selection flips",
              cap(tools=["a"]) != cap(tools=["a", "b"]))
        check(f"[{t}] enabling a prompt flips",
              cap(prompts=["p1"]) != cap(prompts=["p1", "p2"]))

    check("changing the class itself flips",
          c.capability_signature({"profile_type": "rag_focused"})
          != c.capability_signature({"profile_type": "tool_enabled"}))


def t_capability_ignores_tuning():
    """Tuning must NOT flip a badge — a signal that fires for everything is no signal."""
    def cap(**kw):
        return c.capability_signature({"profile_type": "tool_enabled", **kw})

    check("component intensity is tuning, not capability",
          cap(componentConfig={"c": {"enabled": True, "intensity": "low"}})
          == cap(componentConfig={"c": {"enabled": True, "intensity": "high"}}))
    check("tool list order is irrelevant", cap(tools=["b", "a"]) == cap(tools=["a", "b"]))
    check("a disabled primer with text is still inert",
          cap(session_primer={"enabled": False, "statements": ["x"]}) == cap())
    check("an enabled primer with no statements is inert",
          cap(session_primer={"enabled": True, "statements": []}) == cap())
    check("whitespace-only statements are inert",
          cap(session_primer={"enabled": True, "statements": ["   "]}) == cap())


def t_genie_portability():
    """The marketplace-critical property: coordinator routing must hash identically before
    and after an id remap, or no genie pack could ever reinstate 🟢."""
    pub = {"profile_type": "genie", "genieConfig": {
        "slaveProfiles": ["profile-AAA", "profile-BBB"],
        "slaveProfileSettings": {"profile-AAA": {"fullResultPassthrough": True}},
        "temperature": 0.3, "maxConcurrentSlaves": 4}}
    sub = {"profile_type": "genie", "genieConfig": {
        "slaveProfiles": ["profile-ZZZ", "profile-YYY"],
        "slaveProfileSettings": {"profile-ZZZ": {"fullResultPassthrough": True}},
        "temperature": 0.9, "maxConcurrentSlaves": 1}}
    pm = {"profile-AAA": "OPTIM", "profile-BBB": "FOCUS"}
    sm = {"profile-ZZZ": "OPTIM", "profile-YYY": "FOCUS"}
    a = c.capability_signature(pub, id_to_tag=lambda i: pm.get(i))
    b = c.capability_signature(sub, id_to_tag=lambda i: sm.get(i))
    check("routing survives a publish→install id remap", a == b)
    check("routing is expressed in tag space, not ids",
          "OPTIM" in str(a["genieConfig"]) and "profile-" not in str(a["genieConfig"]))

    dropped = {"profile_type": "genie",
               "genieConfig": {**sub["genieConfig"], "slaveProfiles": ["profile-ZZZ"]}}
    check("dropping an expert still flips",
          c.capability_signature(dropped, id_to_tag=lambda i: sm.get(i)) != b)
    trusted = {"profile_type": "genie",
               "genieConfig": {**sub["genieConfig"], "requireTrustedExperts": True}}
    check("trust-aware routing flips",
          c.capability_signature(trusted, id_to_tag=lambda i: sm.get(i)) != b)
    check("an unresolvable id is kept, never silently dropped",
          "profile-QQQ" in str(c.capability_signature(
              {"profile_type": "genie", "genieConfig": {"slaveProfiles": ["profile-QQQ"]}},
              id_to_tag=lambda i: None)))
    check("temperature and concurrency are tuning",
          c.capability_signature(
              {"profile_type": "genie", "genieConfig": {"slaveProfiles": ["p"], "temperature": 1}},
              id_to_tag=lambda i: "T")
          == c.capability_signature(
              {"profile_type": "genie", "genieConfig": {"slaveProfiles": ["p"], "temperature": 0}},
              id_to_tag=lambda i: "T"))


def t_dual_model_drops_local_ids():
    """Whether a second model is used is intrinsic; WHICH stored config fills the role is
    deployment-bound and must not enter the portable hash."""
    a = c.capability_signature({"profile_type": "tool_enabled",
                                "dualModelConfig": {"enabled": True, "tacticalConfigId": "cfg-1"}})
    b = c.capability_signature({"profile_type": "tool_enabled",
                                "dualModelConfig": {"enabled": True, "tacticalConfigId": "cfg-9"}})
    check("dual-model shape ignores instance-local config ids", a == b)
    check("turning dual-model on is still visible",
          a != c.capability_signature({"profile_type": "tool_enabled"}))


# ── deployment signature (local half) ────────────────────────────────────────

def t_deployment_pure():
    b = D.build_deployment_signature
    check("empty deployment is baseline", D.is_baseline(b(None, None, None)))
    check("an LLM makes it non-baseline",
          not D.is_baseline(b({"provider": "P", "model": "M"}, None, None)))

    check("llm identity carries provider+model only",
          b({"provider": "P", "model": "M", "credentials": "SECRET"}, None, None)["llm"]
          == {"provider": "P", "model": "M"})
    check("credentials never enter the signature",
          "SECRET" not in str(b({"provider": "P", "model": "M", "credentials": "SECRET"},
                                None, None)))

    check("catalog is order-independent",
          b(None, {"tools": ["b", "a"], "prompts": []}, None)
          == b(None, {"tools": ["a", "b"], "prompts": []}, None))
    check("catalog de-duplicates",
          b(None, {"tools": ["a", "a"]}, None)["catalog"]["tools"] == ["a"])

    # Enablement is content: opted-out must equal absent, opted-in must not.
    check("an opted-out connector ≡ no connector",
          b(None, None, [{"server_id": "x", "opted_in": False, "user_tools": ["a"]}])
          == b(None, None, []))
    check("an opted-in connector is visible",
          b(None, None, [{"server_id": "x", "opted_in": True, "user_tools": ["a"]}])
          != b(None, None, []))
    check("connector order is irrelevant",
          b(None, None, [{"server_id": "b", "opted_in": True}, {"server_id": "a", "opted_in": True}])
          == b(None, None, [{"server_id": "a", "opted_in": True}, {"server_id": "b", "opted_in": True}]))
    check("widening a connector's tool grant is visible",
          b(None, None, [{"server_id": "x", "opted_in": True, "user_tools": ["a"]}])
          != b(None, None, [{"server_id": "x", "opted_in": True, "user_tools": ["a", "b"]}]))

    check("unresolved is not confused with absent",
          b(D.UNRESOLVED, None, None) != b(None, None, None))
    check("the hash is deterministic",
          D.deployment_hash(b({"provider": "P", "model": "M"}, {"tools": ["t"]}, []))
          == D.deployment_hash(b({"provider": "P", "model": "M"}, {"tools": ["t"]}, [])))


def t_catalog_from_classification():
    check("no MCP server bound → no catalog",
          D.catalog_from_classification({"profile_type": "llm_only"}) is None)
    check("server bound but never discovered → unresolved, not 'no tools'",
          D.catalog_from_classification({"mcpServerId": "s1"}) is D.UNRESOLVED)

    prof = {"mcpServerId": "s1", "classification_results": {
        "tools": {"All Tools": [{"name": "base_readQuery",
                                 "arguments": [{"name": "sql"}]}]},
        "prompts": {"Cat": [{"name": "p1", "arguments": []}]}}}
    cat = D.catalog_from_classification(prof)
    check("tools are captured with their arg names",
          cat["tools"] == ["base_readQuery(sql)"])
    check("prompts are captured too (a prompt is a capability)", cat["prompts"] == ["p1()"])

    grown = {"mcpServerId": "s1", "classification_results": {
        "tools": {"All Tools": [{"name": "base_readQuery", "arguments": [{"name": "sql"}]},
                                {"name": "base_writeQuery", "arguments": [{"name": "sql"}]}]}}}
    check("a server gaining a write tool changes the catalog",
          D.catalog_from_classification(grown) != cat)

    argy = {"mcpServerId": "s1", "classification_results": {
        "tools": {"All Tools": [{"name": "base_readQuery",
                                 "arguments": [{"name": "sql"}, {"name": "unsafe"}]}]}}}
    check("a tool silently gaining a parameter is visible",
          D.catalog_from_classification(argy) != cat)

    # A snapshot that EXISTS but was never TAKEN — the block `_build_profile` writes for
    # every imported profile. It hashed as "no tools" and an attestation over it read
    # `trusted` for a catalog the instance had never looked at (found live, 2026-09-07).
    never = {"mcpServerId": "s1", "classification_results": {
        "tools": {}, "prompts": {}, "resources": {},
        "last_classified": None, "classified_with_mode": None}}
    check("an empty, never-taken snapshot is UNRESOLVED, not 'no tools'",
          D.catalog_from_classification(never) is D.UNRESOLVED)
    empty_real = {"mcpServerId": "s1", "classification_results": {
        "tools": {}, "prompts": {}, "last_classified": "2026-09-01T00:00:00Z",
        "classified_with_mode": "light"}}
    check("a classified server that exposes nothing is an EMPTY catalog, not unresolved",
          D.catalog_from_classification(empty_real) == {"tools": [], "prompts": []})
    legacy = {"mcpServerId": "s1", "classification_results": {
        "tools": {"All Tools": [{"name": "base_readQuery", "arguments": [{"name": "sql"}]}]}}}
    check("a populated snapshot without a timestamp (older classifier) is still a catalog",
          D.catalog_from_classification(legacy) == {"tools": ["base_readQuery(sql)"], "prompts": []})


def t_clear_is_a_clear():
    """`save_profile_classification` stamped `last_classified = now` on EVERY save — a clear
    (empty block, `last_classified: None`) came back timestamped, read as "classified,
    exposes nothing", and an owner could attest over it. Three attestations over nothing
    were taken on the local subscriber before this was caught (2026-09-07)."""
    from trusted_data_agent.core.config_manager import ConfigManager
    cm = ConfigManager.__new__(ConfigManager)
    saved = {}
    cm.get_profiles = lambda u=None: [{"id": "p1", "classification_mode": "light", "mcpServerId": "s1"}]
    cm.save_profiles = lambda profiles, u=None: saved.update({p["id"]: p for p in profiles}) or True
    cm.clear_profile_classification("p1", "u")
    cr = saved["p1"]["classification_results"]
    check("a clear stays a clear — no timestamp on an empty snapshot", cr["last_classified"] is None and cr["classified_with_mode"] is None)
    check("...and the deployment half reads it UNRESOLVED (not attestable)",
          D.catalog_from_classification(saved["p1"]) is D.UNRESOLVED)
    cm.save_profile_classification("p1", {"tools": {"All": [{"name": "t", "arguments": []}]}, "prompts": {}, "resources": {}}, "u")
    cr2 = saved["p1"]["classification_results"]
    check("a snapshot with entries is stamped", bool(cr2["last_classified"]) and cr2["classified_with_mode"] == "light")
    cm.save_profile_classification("p1", {"tools": {}, "prompts": {}, "resources": {}}, "u")
    check("a classification that found nothing keeps no timestamp — nothing to attest",
          saved["p1"]["classification_results"]["last_classified"] is None)


def t_attest_refuses_unresolved():
    """An attestation over an UNRESOLVED surface says 'this is what I intend here' about
    something nobody has looked at. Refuse, naming the surface and the act that resolves it."""
    import ast as _ast, inspect as _inspect, textwrap as _tw
    ok = D.build_deployment_signature({"provider": "A", "model": "B"},
                                      {"tools": ["t(x)"], "prompts": []}, [])
    check("a fully resolved signature is attestable", D.attest_refusal(ok) is None)
    check("...even when the catalog is genuinely empty",
          D.attest_refusal(D.build_deployment_signature(
              {"provider": "A", "model": "B"}, {"tools": [], "prompts": []}, [])) is None)
    r = D.attest_refusal(D.build_deployment_signature(
        {"provider": "A", "model": "B"}, D.UNRESOLVED, []))
    check("an unresolved catalog refuses", bool(r) and r["reason"] == "unresolved_surface")
    check("...naming the surface", r and r["unresolved"] == ["catalog"])
    check("...and the remedy (classification), not just the fact",
          r and "classif" in r["message"].lower())
    r2 = D.attest_refusal(D.build_deployment_signature(
        {"provider": "A", "model": "B"}, D.UNRESOLVED, D.UNRESOLVED))
    check("every unresolved surface is named, not the first",
          r2 and sorted(r2["unresolved"]) == ["catalog", "connectors"])
    check("unresolved_surfaces is pure over the signature keys",
          D.unresolved_surfaces({"a": D.UNRESOLVED, "b": None, "c": D.UNRESOLVED}) == ["a", "c"])
    # wiring, on the AST: attest_deployment consults the refusal BEFORE it signs
    from trusted_data_agent.core.trust import composition as _comp
    src = _tw.dedent(_inspect.getsource(_comp.attest_deployment))
    tree = _ast.parse(src)
    calls = [n.func.attr for n in _ast.walk(tree)
             if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)]
    check("attest_deployment consults attest_refusal", "attest_refusal" in calls)
    check("...before it signs", "attest_refusal" in calls and "sign" in calls
          and calls.index("attest_refusal") < calls.index("sign"))
    # the drift line for an unresolved catalog says what to do
    old_sig = D.build_deployment_signature({"provider": "A", "model": "B"},
                                           {"tools": ["t(x)"], "prompts": []}, [])
    new_sig = D.build_deployment_signature({"provider": "A", "model": "B"}, D.UNRESOLVED, [])
    lines = " ".join(D.describe_drift(old_sig, new_sig)).lower()
    check("the unresolved-catalog drift names classification as the remedy",
          "classif" in lines and "unreadable" in lines)


def t_drift_is_described():
    """An unexplained amber trains operators to click through it, so drift must name what
    actually changed."""
    base = D.build_deployment_signature(
        {"provider": "Google", "model": "gemini-3-pro"},
        {"tools": ["base_readQuery(sql)"], "prompts": []}, [])
    grown = D.build_deployment_signature(
        {"provider": "Google", "model": "gemini-3-pro"},
        {"tools": ["base_readQuery(sql)", "base_writeQuery(sql)"], "prompts": []}, [])
    swapped = D.build_deployment_signature(
        {"provider": "Ollama", "model": "llama-8b"},
        {"tools": ["base_readQuery(sql)"], "prompts": []}, [])
    wired = D.build_deployment_signature(
        {"provider": "Google", "model": "gemini-3-pro"},
        {"tools": ["base_readQuery(sql)"], "prompts": []},
        [{"server_id": "uderia-shell", "opted_in": True, "user_tools": ["run"]}])

    check("no drift against itself", D.describe_drift(base, base) == [])
    check("a new tool is named",
          any("base_writeQuery" in s for s in D.describe_drift(base, grown)))
    check("a model swap names both sides",
          any("gemini-3-pro" in s and "llama-8b" in s for s in D.describe_drift(base, swapped)))
    check("a connector opt-in is named",
          any("uderia-shell" in s for s in D.describe_drift(base, wired)))
    check("a removed tool is reported too",
          any("removed" in s for s in D.describe_drift(grown, base)))
    check("each drift changes the hash",
          len({D.deployment_hash(x) for x in (base, grown, swapped, wired)}) == 4)

    # TC-26: a connector whose user_tools list widened is a real change a server_id-set
    # comparison misses, leaving an unexplained amber the describer exists to prevent.
    wired2 = D.build_deployment_signature(
        {"provider": "Google", "model": "gemini-3-pro"},
        {"tools": ["base_readQuery(sql)"], "prompts": []},
        [{"server_id": "uderia-shell", "opted_in": True, "user_tools": ["run", "write_file"]}])
    check("a widened connector tool list is named (not an empty amber)",
          any("write_file" in s for s in D.describe_drift(wired, wired2)))
    check("a widened connector still changes the hash",
          D.deployment_hash(wired) != D.deployment_hash(wired2))

    # TC-26: a dict→UNRESOLVED catalog is 'became unreadable', not 'every tool removed'.
    unreadable = D.build_deployment_signature(
        {"provider": "Google", "model": "gemini-3-pro"}, D.UNRESOLVED, [])
    drift = D.describe_drift(base, unreadable)
    check("catalog dict→UNRESOLVED reads 'unreadable', never mass-removal",
          any("unreadable" in s for s in drift) and not any("removed" in s for s in drift))

    # TC-25: connector opt-in that could not be read is UNRESOLVED, never silently "no
    # connectors" — so a persistent read failure cannot sign a signature omitting an enabled
    # shell/browser connector while the badge reads intact.
    unread_conn = D.build_deployment_signature(
        {"provider": "Google", "model": "gemini-3-pro"},
        {"tools": ["base_readQuery(sql)"], "prompts": []}, D.UNRESOLVED)
    check("UNRESOLVED connectors hash distinctly from empty connectors",
          D.deployment_hash(unread_conn) != D.deployment_hash(base))
    check("UNRESOLVED connectors are not baseline (a deployment attestation is required)",
          D.is_baseline(unread_conn) is False)
    check("UNRESOLVED connector transition is described as unreadable",
          any("unreadable" in s for s in D.describe_drift(wired, unread_conn)))


# ── the fold ─────────────────────────────────────────────────────────────────

def t_compose():
    f, T = compose_agent_state, TrustState
    check("an unsigned artefact is not rescued by local attestation",
          f(T.UNSIGNED, T.TRUSTED) is T.UNSIGNED)
    check("a drifted artefact is not rescued either",
          f(T.CHANGED, T.TRUSTED) is T.CHANGED)
    check("both intact → trusted", f(T.TRUSTED, T.TRUSTED) is T.TRUSTED)
    check("deployment drift → changed", f(T.TRUSTED, T.CHANGED) is T.CHANGED)
    check("publisher-signed but not attested here → its own state",
          f(T.TRUSTED, T.UNSIGNED) is T.DEPLOYMENT_UNATTESTED)
    check("a missing deployment record is unattested, never trusted",
          f(T.TRUSTED, None) is T.DEPLOYMENT_UNATTESTED)
    check("nothing deployment-bound to attest → not held short of green",
          f(T.TRUSTED, T.UNSIGNED, deployment_required=False) is T.TRUSTED)
    check("the new state is distinct from both collapses",
          T.DEPLOYMENT_UNATTESTED not in (T.CHANGED, T.UNSIGNED))
    check("state wire values are stable",
          T.DEPLOYMENT_UNATTESTED.value == "deployment_unattested")


def t_separation_of_halves():
    """The whole point of two hashes: deployment facts must not touch the portable hash,
    or no pack could reinstate; portable facts must not be silently local."""
    prof = {"profile_type": "rag_focused", "llmConfigurationId": "cfg-local-1",
            "mcpServerId": "srv-local-1"}
    other = {"profile_type": "rag_focused", "llmConfigurationId": "cfg-local-9",
             "mcpServerId": "srv-local-9"}
    check("instance-local config ids never enter the portable hash",
          c.capability_signature(prof) == c.capability_signature(other))
    check("...nor does the resolved catalog",
          "catalog" not in str(c.capability_signature(prof)))
    check("the deployment half does carry them",
          D.build_deployment_signature({"provider": "A", "model": "B"}, None, None)
          != D.build_deployment_signature({"provider": "A", "model": "C"}, None, None))


def t_marketplace_reinstate():
    """The whole justification for splitting the hash: a published agent must reinstate on
    a subscriber's instance, where every instance-local id differs — while real tampering
    is still caught. If this fails, the marketplace trust story is broken."""
    from trusted_data_agent.core.trust.composition import (
        build_agent_ingredients, agent_version_hash)

    def agent(pid, kg, coll, llm, mcp, slaves, mapping, posture="enforce", corpus="CH"):
        prof = {"id": pid, "tag": "UDX", "profile_type": "genie",
                "llmConfigurationId": llm, "mcpServerId": mcp,
                "groundingConfig": {"mode": posture},
                "componentConfig": {"memory": {"enabled": True}},
                "genieConfig": {"slaveProfiles": slaves}}
        refs = build_agent_ingredients({"kg_id": kg, "content_hash": "KH"}, [],
                                       lambda k, a: "KH",
                                       [{"kind": "knowledge", "collection_id": coll,
                                         "content_hash": corpus}])
        return agent_version_hash(refs, prof, id_to_tag=lambda i: mapping.get(i))

    pub = agent("profile-PUB", "kg-pub", "7", "llm-pub", "mcp-pub",
                ["profile-PUB-A", "profile-PUB-B"],
                {"profile-PUB-A": "OPTIM", "profile-PUB-B": "FOCUS"})
    sub = agent("profile-SUB", "kg-sub", "42", "llm-sub", "mcp-sub",
                ["profile-SUB-X", "profile-SUB-Y"],
                {"profile-SUB-X": "OPTIM", "profile-SUB-Y": "FOCUS"})
    check("a published agent reinstates after a full id remap", pub == sub)

    submap = {"profile-SUB-X": "OPTIM", "profile-SUB-Y": "FOCUS"}
    weakened = agent("profile-SUB", "kg-sub", "42", "llm-sub", "mcp-sub",
                     ["profile-SUB-X", "profile-SUB-Y"], submap, posture="observe")
    check("...but weakened posture is still caught", weakened != pub)
    edited = agent("profile-SUB", "kg-sub", "42", "llm-sub", "mcp-sub",
                   ["profile-SUB-X", "profile-SUB-Y"], submap, corpus="CH-EDITED")
    check("...and an edited corpus is still caught", edited != pub)
    rerouted = agent("profile-SUB", "kg-sub", "42", "llm-sub", "mcp-sub",
                     ["profile-SUB-X"], submap)
    check("...and a dropped expert is still caught", rerouted != pub)


def t_db_path_is_not_reimplemented():
    """Regression: connector state must resolve its DB path through the store's resolver.

    A hand-rolled copy of the path rule missed that ``AUTH_DB_PATH`` carries a
    ``sqlite:///`` prefix, so in the running server the path was unopenable — and behind
    the fail-soft, connector opt-in silently vanished from the signature while the badge
    still read intact. Unit tests could not catch it (they pass a path explicitly), so the
    invariant pinned here is structural: one resolver, and the failure is loud.
    """
    import inspect
    src = inspect.getsource(D._connector_state)
    # Compare CODE, not prose — the explanation of this very bug mentions the key by name.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    check("connector state uses the store's path resolver, not its own",
          "_db_path" in code)
    check("it does not re-derive the auth DB path", "AUTH_DB_PATH" not in code)
    check("a connector read failure is logged loudly, not swallowed at debug level",
          "logger.warning" in code and "logger.debug" not in code)

    store_src = inspect.getsource(
        __import__("trusted_data_agent.core.trust.store", fromlist=["_db_path"])._db_path)
    check("the shared resolver is the one that strips the sqlite:/// prefix",
          "sqlite:///" in store_src)


def main():
    print("=" * 70)
    print("Two-hash trust envelope — capability (portable) + deployment (local)")
    print("=" * 70)
    for fn in (t_capability_all_classes, t_capability_catches_widening,
               t_capability_ignores_tuning, t_genie_portability,
               t_dual_model_drops_local_ids, t_deployment_pure,
               t_catalog_from_classification, t_clear_is_a_clear, t_attest_refuses_unresolved,
               t_drift_is_described,
               t_compose, t_separation_of_halves, t_marketplace_reinstate,
               t_db_path_is_not_reimplemented):
        fn()
    print("=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
