"""
Module tests — the declarative portable-profile-config contract
(core/profile_config_contract.py).

This registry replaces four hand-maintained allow-lists in agent_pack_manager.py. The
properties that matter:

  * portability round-trips (source projection → sink application) for every portable key;
  * an ABSENT signed block hashes identically to one written at its documented defaults —
    without this, merely opening and saving a profile in the editor would flip a green
    trust badge to amber;
  * the downgrade guard catches every way a pack could reduce protection on an
    update-in-place, including the authority asymmetries (``allowed_connectors: []`` means
    deny-all, ``max_cost_per_run: 0`` is a real cap) that a naive normalizer would invert
    into a privilege grant.

CI-pure. Run:  python test/test_profile_config_contract.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import profile_config_contract as c  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def t_registry():
    keys = set(c.portable_keys())
    for expected in ("knowledgeConfig", "skillsConfig", "componentConfig",
                     "recommenderConfig", "groundingConfig", "guardrailConfig",
                     "honestyConfig", "authorityConfig"):
        check(f"{expected} is portable", expected in keys)
    signed = set(c.signed_keys())
    check("posture dimension is the agent default + three answer gates + knowledgeConfig's honesty "
          "threshold + the turn budget (S5) + the outcome loop (S7: revise-before-delivery is posture)",
          {s.key for s in c.REGISTRY if s.signed and s.signed_dimension == "posture"}
          == {"postureConfig", "groundingConfig", "guardrailConfig", "honestyConfig",
              "knowledgeConfig", "turnBudget", "outcomeConfig"})
    check("capability dimension is what the agent can DO",
          {s.key for s in c.REGISTRY if s.signed and s.signed_dimension == "capability"}
          == {"profile_type", "session_primer", "tools", "prompts", "useMcpTools",
              "useKnowledgeCollections", "componentConfig", "genieConfig",
              "dualModelConfig", "additionalMcpServers"})
    check("every signed key travels — by this contract or as a declared import binding "
          "(or a pack could never reinstate it)",
          signed <= set(c.portable_keys()) | set(c.binding_keys()))
    check("a key that travels as a binding is an import binding, and is not contract-portable",
          set(c.binding_keys()) <= set(c.IMPORT_BINDING_KEYS)
          and not (set(c.binding_keys()) & set(c.portable_keys())))
    check("skillsConfig stays unsigned — active skills are covered compositionally",
          "skillsConfig" not in signed)
    # Deliberate exclusions — see the module docstring (trust != access).
    check("authorityConfig is portable but NOT signed",
          "authorityConfig" in keys and "authorityConfig" not in signed)
    check("every signed key declares defaults",
          all(c.spec_for(k).signed_defaults for k in signed))
    check("every signed key with an enforcement ladder declares a mode path",
          all(c.spec_for(k).mode_paths
              for k in ("groundingConfig", "guardrailConfig", "honestyConfig")))

    # knowledgeConfig is signed for exactly ONE sub-path. Assert on the produced
    # signature, not the registry — the guarantee is about what gets hashed.
    sig = c.posture_signature({"knowledgeConfig": {
        "sufficiencyThreshold": 0.4, "maxSensitivity": "confidential",
        "maxDocs": 10, "maxTokens": 8000, "minRelevanceScore": 0.25,
        "collections": [{"id": 2}]}})
    kc_sig = sig.get("knowledgeConfig") or {}
    check("sufficiencyThreshold IS signed (the honesty gate reads it)",
          kc_sig.get("sufficiencyThreshold") == 0.4)
    check("maxSensitivity is NOT signed (access, not speech)",
          "maxSensitivity" not in kc_sig)
    for tuning in ("maxDocs", "maxTokens", "minRelevanceScore", "collections"):
        check(f"{tuning} is NOT signed (tuning must not flip a badge)",
              tuning not in kc_sig)

    # Regression: a signed block without an enforcement ladder is not an answer gate and
    # must never surface in the agent validator's acting/idle split.
    acting = c.posture_is_acting({"knowledgeConfig": {"sufficiencyThreshold": 0.4}})
    check("posture_is_acting reports only ladder-bearing gates",
          set(acting) == {"groundingConfig", "guardrailConfig", "honestyConfig"})
    check("spec_for returns None for an unknown key", c.spec_for("nopeConfig") is None)


def t_projection_roundtrip():
    profile = {
        "id": "p1", "tag": "UDERIA",
        "knowledgeConfig": {"maxDocs": 10},
        "skillsConfig": {"skills": [{"id": "s1", "active": True}]},
        "componentConfig": {"memory": {"enabled": True}},
        "groundingConfig": {"mode": "enforce", "corpus": {"mode": "review"}},
        "guardrailConfig": {"outbound": {"mode": "enforce", "pii": True}},
        "honestyConfig": {"mode": "annotate"},
        "authorityConfig": {"deny_writes": True},
        "recommenderConfig": {"enabled": False},
        "llmConfigurationId": "not-portable",
    }
    entry = c.project_to_manifest(profile)
    check("projection carries all 8 portable blocks", len(entry) == 8)
    check("projection excludes non-config keys", "llmConfigurationId" not in entry)
    check("projection preserves nested posture",
          entry["groundingConfig"]["corpus"]["mode"] == "review")

    target = {}
    c.apply_from_manifest(entry, target)
    check("round-trip preserves every portable key", set(target) == set(entry))
    check("round-trip preserves grounding", target["groundingConfig"] == profile["groundingConfig"])
    check("round-trip preserves authority", target["authorityConfig"] == profile["authorityConfig"])
    check("round-trip preserves honesty", target["honestyConfig"] == profile["honestyConfig"])

    # Absence must PRESERVE, never clear — the long-standing update-in-place fail-safe.
    existing = {"groundingConfig": {"mode": "enforce"}}
    c.apply_from_manifest({"honestyConfig": {"mode": "off"}}, existing)
    check("absent key leaves the existing value untouched",
          existing["groundingConfig"]["mode"] == "enforce")
    check("present key is applied", existing["honestyConfig"]["mode"] == "off")

    # Empty blocks are noise, not intent.
    check("empty block is not projected", c.project_to_manifest({"honestyConfig": {}}) == {})
    check("non-dict profile projects nothing", c.project_to_manifest(None) == {})
    check("non-dict entry applies nothing", c.apply_from_manifest(None, {}) == {})


def t_instance_local_refs():
    """A path the registry declares instance-local is NEVER transported in either
    direction, and the write merges so the caller's remap survives any ordering.

    Regression: adding ``genieConfig`` to the registry (two-hash trust envelope, Jul 2026)
    silently enrolled ``slaveProfiles`` into transport. ``apply_from_manifest`` replaced the
    whole block, so ``_build_profile``'s child_tags → new-id remap was overwritten by the
    publisher's own profile ids — every imported/forked/subscribed coordinator lost its
    experts and failed activation with "Child profile(s) not found".
    """
    # Every declared path must be reachable and, where it holds PROFILE ids, tag-mapped for
    # the signature too — otherwise a path is excluded from transport but hashed raw.
    for spec in c.REGISTRY:
        for path in spec.local_ref_paths:
            check(f"{spec.key}: local ref path {'.'.join(path)} is not also an id ref path",
                  path not in spec.id_ref_paths)
    declared = {s.key for s in c.REGISTRY if c.instance_local_paths(s)}
    check("genieConfig declares its instance-local ids", "genieConfig" in declared)
    check("knowledgeConfig declares its instance-local ids", "knowledgeConfig" in declared)

    publisher = {
        "profile_type": "genie",
        "genieConfig": {"slaveProfiles": ["pub-1", "pub-2"],
                        "slaveProfileSettings": {"pub-1": {"fullResultPassthrough": True}},
                        "temperature": 0.4},
        "knowledgeConfig": {"collections": [{"id": 7, "name": "publisher corpus"}],
                            "maxDocs": 5},
    }

    # ── Source: a manifest never carries another instance's ids ───────────────────
    entry = {"child_tags": ["C1", "C2"],
             "genieConfig": {"slaveProfileSettings": {"C1": {"fullResultPassthrough": True}}}}
    c.apply_to_manifest(publisher, entry)
    gc, kc = entry.get("genieConfig") or {}, entry.get("knowledgeConfig") or {}
    check("manifest omits foreign profile ids", "slaveProfiles" not in gc)
    check("manifest omits foreign collection ids", "collections" not in kc)
    check("manifest keeps the portable tuning", gc.get("temperature") == 0.4)
    check("manifest keeps the caller's tag-keyed settings",
          gc.get("slaveProfileSettings") == {"C1": {"fullResultPassthrough": True}})
    check("projection never mutates the source profile",
          publisher["genieConfig"]["slaveProfiles"] == ["pub-1", "pub-2"])
    check("project_to_manifest strips them too",
          "slaveProfiles" not in (c.project_to_manifest(publisher).get("genieConfig") or {}))

    # ── Sink: a resolved remap survives, whichever order the contract runs in ─────
    for label, target in (
        ("remap first", {"genieConfig": {"slaveProfiles": ["local-1", "local-2"]},
                         "knowledgeConfig": {"collections": [{"id": 42}]}}),
        ("contract first", {}),
    ):
        c.apply_from_manifest(entry, target)
        tgc, tkc = target.get("genieConfig") or {}, target.get("knowledgeConfig") or {}
        if label == "remap first":
            check("sink preserves the resolved experts",
                  tgc.get("slaveProfiles") == ["local-1", "local-2"])
            check("sink preserves the resolved collections",
                  tkc.get("collections") == [{"id": 42}])
        else:
            check("sink writes no experts it cannot resolve", "slaveProfiles" not in tgc)
        check(f"sink applies the portable tuning ({label})", tgc.get("temperature") == 0.4)

    # A manifest that still carries a publisher's ids (every pack built between the
    # regression and this fix) must not be able to inject them.
    stale = {"genieConfig": {"slaveProfiles": ["pub-1", "pub-2"], "temperature": 0.4}}
    target = {"genieConfig": {"slaveProfiles": ["local-1"]}}
    c.apply_from_manifest(stale, target)
    check("a stale manifest cannot re-inject publisher ids",
          (target.get("genieConfig") or {}).get("slaveProfiles") == ["local-1"])


def t_alias():
    legacy = {"identityConfig": {"deny_writes": True}}
    entry = c.project_to_manifest(legacy)
    check("legacy identityConfig is exported under the canonical key",
          entry.get("authorityConfig") == {"deny_writes": True})
    # Enforcement-path precedence: the canonical key wins whenever present, even if empty.
    both = {"authorityConfig": {}, "identityConfig": {"deny_writes": True}}
    check("empty canonical block wins over a populated legacy one",
          c.project_to_manifest(both) == {})
    populated = {"authorityConfig": {"deny_writes": False},
                 "identityConfig": {"deny_writes": True}}
    check("populated canonical block wins",
          c.project_to_manifest(populated)["authorityConfig"]["deny_writes"] is False)


def t_absent_equals_defaults():
    """The property that keeps the trust badge honest: an unconfigured agent and one
    explicitly written at the runtime defaults must hash identically."""
    bare = {"tag": "A"}
    # Exactly what the profile editor materialises on a save with nothing changed.
    materialised = {
        "tag": "A",
        "groundingConfig": {"mode": "observe", "allowOutOfScope": False},
        "guardrailConfig": {"outbound": {"mode": "observe", "pii": True, "toxicity": True},
                            "inbound": {"mode": "observe"}},
        "honestyConfig": {"mode": "observe"},
    }
    check("absent posture == editor-materialised defaults",
          c.posture_signature(bare) == c.posture_signature(materialised))
    # The dead `allowOutOfScope` control must not enter the signature.
    flipped = {"groundingConfig": {"mode": "observe", "allowOutOfScope": True}}
    check("allowOutOfScope does not affect the signature",
          c.posture_signature(flipped) == c.posture_signature(bare))
    # A real posture change must.
    hardened = {"groundingConfig": {"mode": "enforce"}}
    check("a real mode change alters the signature",
          c.posture_signature(hardened) != c.posture_signature(bare))
    # review and enforce differ (retention is a real difference).
    check("review is distinguishable from enforce",
          c.posture_signature({"honestyConfig": {"mode": "review"}}) !=
          c.posture_signature({"honestyConfig": {"mode": "enforce"}}))
    # An unrecognised mode collapses to what the runtime would actually use.
    check("unknown mode collapses to the runtime default",
          c.posture_signature({"honestyConfig": {"mode": "bogus"}}) ==
          c.posture_signature(bare))
    # Authority is deliberately absent from the signature.
    check("authority change does not affect the signature",
          c.posture_signature({"authorityConfig": {"deny_writes": True}}) ==
          c.posture_signature(bare))
    check("maxSensitivity does not affect the signature",
          c.posture_signature({"knowledgeConfig": {"maxSensitivity": "public"}}) ==
          c.posture_signature(bare))
    # Structural stability — each dimension covers exactly its own signed keys, and the
    # two dimensions partition the signed set with no key in both and none missing.
    posture_keys = {s.key for s in c.REGISTRY if s.signed and s.signed_dimension == "posture"}
    cap_keys = {s.key for s in c.REGISTRY if s.signed and s.signed_dimension == "capability"}
    # A BARE profile projects exactly the ATTESTED BASELINE keys (the formula every existing
    # attestation was taken with); a key registered after the baseline appears only when USED.
    baseline = c._ATTESTED_BASELINE_KEYS
    check("a bare profile projects exactly the baseline posture keys",
          set(c.posture_signature(bare)) == posture_keys & baseline)
    check("a bare profile projects exactly the baseline capability keys",
          set(c.capability_signature(bare)) == cap_keys & baseline)
    uses_all = {"tag": "A", "profile_type": "genie", "session_primer": {"enabled": True, "mode": "warmup", "statements": ["hello"]},
                "tools": ["t"], "prompts": ["p"], "useMcpTools": True, "useKnowledgeCollections": True,
                "genieConfig": {"slaveProfiles": ["p1"]}, "dualModelConfig": {"enabled": True, "strategicLlmConfigurationId": "s"},
                "componentConfig": {"chart": {"enabled": True}}, "knowledgeConfig": {"sufficiencyThreshold": 0.3},
                "postureConfig": {"mode": "enforce"}, "groundingConfig": {"mode": "enforce"},
                "guardrailConfig": {"outbound": {"mode": "enforce"}}, "honestyConfig": {"mode": "enforce"},
                "turnBudget": {"steps": 5}, "outcomeConfig": {"enabled": True},
                "additionalMcpServers": [{"id": "srv2", "alias": "crm", "tools": ["*"], "prompts": []}]}
    check("a profile that uses every signed key projects exactly the posture keys",
          set(c.posture_signature(uses_all)) == posture_keys)
    check("…and exactly the capability keys",
          set(c.capability_signature(uses_all, id_to_tag=lambda i: "T")) == cap_keys)
    check("the two dimensions are disjoint", not (posture_keys & cap_keys))
    check("together they are exactly the signed set",
          posture_keys | cap_keys == set(c.signed_keys()))
    check("signature is deterministic",
          c.posture_signature(materialised) == c.posture_signature(materialised))
    check("signature does not mutate its input",
          (c.posture_signature(bare), bare) and bare == {"tag": "A"})


def t_posture_is_acting():
    quiet = c.posture_is_acting({})
    check("an unconfigured agent acts nowhere", not any(quiet.values()))
    acting = c.posture_is_acting({"guardrailConfig": {"outbound": {"mode": "enforce"}}})
    check("enforce counts as acting", acting["guardrailConfig"] is True)
    check("other gates stay quiet", acting["groundingConfig"] is False)
    check("annotate counts as acting",
          c.posture_is_acting({"honestyConfig": {"mode": "annotate"}})["honestyConfig"] is True)
    check("review counts as acting",
          c.posture_is_acting({"honestyConfig": {"mode": "review"}})["honestyConfig"] is True)
    check("corpus-only enforcement counts",
          c.posture_is_acting(
              {"groundingConfig": {"mode": "observe",
                                   "corpus": {"mode": "enforce"}}})["groundingConfig"] is True)


def t_downgrade_modes():
    hardened = {"groundingConfig": {"mode": "enforce"},
                "guardrailConfig": {"outbound": {"mode": "review", "pii": True}},
                "honestyConfig": {"mode": "enforce"}}
    check("no downgrade against itself", c.find_downgrades(hardened, hardened) == [])
    check("absent block is never a downgrade",
          c.find_downgrades(hardened, {"knowledgeConfig": {"maxDocs": 5}}) == [])

    weak = c.find_downgrades(hardened, {"groundingConfig": {"mode": "observe"}})
    check("mode weakening is caught", len(weak) == 1)
    check("downgrade names the key", weak[0].key == "groundingConfig")
    check("downgrade names the path", weak[0].path == "mode")
    check("downgrade carries both values",
          weak[0].current == "enforce" and weak[0].incoming == "observe")
    check("downgrade describes itself", "enforce" in weak[0].describe())

    check("strengthening is not a downgrade",
          c.find_downgrades({"honestyConfig": {"mode": "observe"}},
                            {"honestyConfig": {"mode": "enforce"}}) == [])
    # review ranks ABOVE enforce: identical on content, stronger on human oversight. A pack
    # replacing review with enforce silently removes the parked original, so it is guarded.
    check("review→enforce is guarded (loses HITL retention)",
          len(c.find_downgrades({"guardrailConfig": {"outbound": {"mode": "review"}}},
                                {"guardrailConfig": {"outbound": {"mode": "enforce"}}})) == 1)
    check("enforce→review is not a downgrade",
          c.find_downgrades({"guardrailConfig": {"outbound": {"mode": "enforce"}}},
                            {"guardrailConfig": {"outbound": {"mode": "review"}}}) == [])
    check("nested corpus weakening is caught",
          len(c.find_downgrades({"groundingConfig": {"corpus": {"mode": "enforce"}}},
                                {"groundingConfig": {"corpus": {"mode": "observe"}}})) == 1)
    check("disabling a detector is caught",
          len(c.find_downgrades({"guardrailConfig": {"outbound": {"pii": True}}},
                                {"guardrailConfig": {"outbound": {"pii": False}}})) == 1)
    check("enabling a detector is not a downgrade",
          c.find_downgrades({"guardrailConfig": {"outbound": {"pii": False}}},
                            {"guardrailConfig": {"outbound": {"pii": True}}}) == [])
    # TC-2: the backend merge for update_in_place is a shallow dict.update, so a PRESENT
    # incoming block replaces the stored one wholesale — a sub-key dropped from it reverts to
    # the ladder's (weaker) runtime default. The old contract ("absence within a present block
    # preserves") was a bypass: a pack could hold the mode at enforce and quietly drop `pii`.
    check("a protective flag dropped from a present block is a downgrade (wholesale replace)",
          len(c.find_downgrades({"guardrailConfig": {"outbound": {"mode": "enforce", "pii": True}}},
                                {"guardrailConfig": {"outbound": {"mode": "enforce"}}})) == 1)
    check("an acting mode dropped from a present block is a downgrade (reverts to observe)",
          len(c.find_downgrades({"guardrailConfig": {"outbound": {"mode": "enforce", "pii": True}}},
                                {"guardrailConfig": {"outbound": {"pii": True}}})) == 1)
    check("dropping a NON-acting mode from a present block is not a downgrade",
          c.find_downgrades({"guardrailConfig": {"outbound": {"mode": "observe", "pii": True}}},
                            {"guardrailConfig": {"outbound": {"pii": True}}}) == [])
    check("an ABSENT block still preserves (no downgrade) — only present blocks replace",
          c.find_downgrades({"guardrailConfig": {"outbound": {"mode": "enforce"}}}, {}) == [])


def t_downgrade_thresholds():
    """Signed numeric posture must be guarded too: a pack could leave every gate in
    `enforce` while quietly relaxing the numbers those gates fire on."""
    def g(old, new):
        return c.find_downgrades({"groundingConfig": old}, {"groundingConfig": new})

    # Floor — a minimum bar the agent must clear; lowering or dropping it relaxes.
    check("lowering a floor is a downgrade",
          len(g({"minNumericFidelity": 0.9}, {"minNumericFidelity": 0.5})) == 1)
    check("raising a floor is not a downgrade",
          g({"minNumericFidelity": 0.5}, {"minNumericFidelity": 0.9}) == [])
    check("dropping a configured floor to null is a downgrade",
          len(g({"minNumericFidelity": 0.9}, {"minNumericFidelity": None})) == 1)
    check("no floor configured before ⇒ nothing to relax",
          g({"mode": "enforce"}, {"mode": "enforce", "minNumericFidelity": 0.5}) == [])
    check("floor downgrade names the path",
          g({"minNumericFidelity": 0.9}, {"minNumericFidelity": 0.5})[0].path
          == "minNumericFidelity")

    # Ceiling — caps something permissive; raising it allows more drift.
    check("raising a ceiling is a downgrade",
          len(g({"maxOutOfScopeDbs": 1}, {"maxOutOfScopeDbs": 4})) == 1)
    check("lowering a ceiling is not a downgrade",
          g({"maxOutOfScopeDbs": 4}, {"maxOutOfScopeDbs": 1}) == [])
    check("an unrankable ceiling (inherits the admin threshold) is not guessed at",
          g({"maxOutOfScopeDbs": 1}, {"maxOutOfScopeDbs": None}) == [])

    # The honesty sufficiency bar rides the same mechanism.
    kd = c.find_downgrades({"knowledgeConfig": {"sufficiencyThreshold": 0.6}},
                           {"knowledgeConfig": {"sufficiencyThreshold": 0.2}})
    check("lowering the sufficiency bar is a downgrade", len(kd) == 1)
    check("sufficiency downgrade names knowledgeConfig", kd[0].key == "knowledgeConfig")
    check("retrieval tuning is never a downgrade",
          c.find_downgrades({"knowledgeConfig": {"maxDocs": 10}},
                            {"knowledgeConfig": {"maxDocs": 2}}) == [])
    check("booleans are not mistaken for numbers",
          c.find_downgrades({"groundingConfig": {"minNumericFidelity": True}},
                            {"groundingConfig": {"minNumericFidelity": False}}) == [])


def t_downgrade_authority():
    def dg(old, new):
        return c.find_downgrades({"authorityConfig": old}, {"authorityConfig": new})

    check("re-enabling autonomy is caught",
          len(dg({"autonomous": False}, {"autonomous": True})) == 1)
    check("keeping autonomy off is fine", dg({"autonomous": False}, {"autonomous": False}) == [])
    check("omitting autonomous re-enables it by default (caught)",
          len(dg({"autonomous": False}, {"deny_writes": True})) == 1)
    check("allowing writes is caught",
          len(dg({"deny_writes": True}, {"deny_writes": False})) == 1)
    check("shrinking denied_tools is caught",
          len(dg({"denied_tools": ["a", "b"]}, {"denied_tools": ["a"]})) == 1)
    check("growing denied_tools is fine",
          dg({"denied_tools": ["a"]}, {"denied_tools": ["a", "b"]}) == [])
    check("denied_tools order does not matter",
          dg({"denied_tools": ["a", "b"]}, {"denied_tools": ["b", "a"]}) == [])

    # allowed_connectors: [] means DENY ALL (strictest) — never collapse it to None.
    check("dropping a connector allow-list is caught",
          len(dg({"allowed_connectors": ["slack"]}, {"allowed_connectors": None})) == 1)
    check("widening a connector allow-list is caught",
          len(dg({"allowed_connectors": ["slack"]},
                 {"allowed_connectors": ["slack", "google"]})) == 1)
    check("narrowing a connector allow-list is fine",
          dg({"allowed_connectors": ["slack", "google"]}, {"allowed_connectors": ["slack"]}) == [])
    check("deny-all [] → a real list is a widening",
          len(dg({"allowed_connectors": []}, {"allowed_connectors": ["slack"]})) == 1)

    # max_cost_per_run: 0 is a real deny-all cap, not "uncapped".
    check("raising the budget cap is caught",
          len(dg({"max_cost_per_run": 1.0}, {"max_cost_per_run": 5.0})) == 1)
    check("removing the budget cap is caught",
          len(dg({"max_cost_per_run": 1.0}, {"max_cost_per_run": None})) == 1)
    check("lowering the budget cap is fine",
          dg({"max_cost_per_run": 5.0}, {"max_cost_per_run": 1.0}) == [])
    check("a zero cap is a real cap, and raising it is caught",
          len(dg({"max_cost_per_run": 0}, {"max_cost_per_run": 2.0})) == 1)
    check("adding a cap where there was none is fine",
          dg({"max_cost_per_run": None}, {"max_cost_per_run": 1.0}) == [])



def t_empty_selection_equals_absent():
    """An EMPTY tool/prompt selection and an ABSENT one are the same agent.

    This is not theoretical: `project_to_manifest` drops `[]` because it is falsy, so a
    signed agent's own export produces the absent form. Before this, forking a signed
    pack left every expert `unsigned` — the publisher held `prompts: []`, the copy held
    nothing, the capability hashes differed, and the membrane correctly refused to hand
    over a signature it could not match. Only the coordinator, which selects neither,
    reinstated.
    """
    empty = {"tag": "A", "tools": [], "prompts": []}
    absent = {"tag": "A"}
    nulled = {"tag": "A", "tools": None, "prompts": None}
    sig = c.signature_for
    check("empty selection == absent selection",
          sig(empty, "capability") == sig(absent, "capability"))
    check("null selection == absent selection",
          sig(nulled, "capability") == sig(absent, "capability"))

    # ...and a REAL selection still differs, or the rule would erase capability.
    real = {"tag": "A", "tools": ["base_readQuery"]}
    check("a real selection still changes the signature",
          sig(real, "capability") != sig(absent, "capability"))

    # Order and duplication are not capability.
    a = {"tag": "A", "tools": ["b", "a"]}
    b = {"tag": "A", "tools": ["a", "b"]}
    dup = {"tag": "A", "tools": ["a", "b", "a"]}
    check("selection order does not change the signature",
          sig(a, "capability") == sig(b, "capability"))
    check("a duplicate entry does not change the signature",
          sig(dup, "capability") == sig(b, "capability"))

    # Dropping a tool must still be visible — the whole point of signing capability.
    fewer = {"tag": "A", "tools": ["a"]}
    check("removing a tool changes the signature",
          sig(fewer, "capability") != sig(b, "capability"))


def t_selection_getters_return_a_list():
    """`get_profile_enabled_tools/prompts` promise a list; a present-but-null value
    must not leak None to a caller that iterates or measures it."""
    import inspect
    from trusted_data_agent.core import config_manager as cm
    for name in ("get_profile_enabled_tools", "get_profile_enabled_prompts"):
        src = inspect.getsource(getattr(cm.ConfigManager, name))
        check(f"{name} coerces a non-list to []",
              "isinstance(selection, (list, tuple))" in src)


def t_new_signed_key_flips_nothing():
    """A signed key ADDED to the registry after an agent was signed must not move that agent's
    signature while the agent does not use the key. Found on production 2026-09-20: S5's
    `turnBudget` and S7's `outcomeConfig` joined the registry at their defaults and every
    hardened agent flipped to CHANGED — 19 of 19 on the demo account. The rule: a key outside
    the attested baseline is OMITTED while at its defaults; the baseline keys keep the
    projection every existing attestation was taken with (the first fix omitted THOSE too and
    moved every signature on production the same way — proven live before this test was written)."""
    hardened = {"tag": "A", "knowledgeConfig": {"sufficiencyThreshold": 0.4},
                "groundingConfig": {"mode": "enforce"}}
    with_new_keys_defaulted = {**hardened,
                               "turnBudget": {"steps": None, "tokens": None, "seconds": None},
                               "outcomeConfig": {"enabled": False, "maxRevisions": None}}
    sig = c.posture_signature(hardened)
    check("a hardened agent's signature carries no defaulted post-baseline key",
          "turnBudget" not in sig and "outcomeConfig" not in sig and "groundingConfig" in sig)
    check("writing the new keys at their defaults changes nothing (byte-identical baseline)",
          c.posture_signature(with_new_keys_defaulted) == sig)
    # THE ATTESTATION-ERA FORMULA IS PRESERVED: with the post-baseline keys removed from the
    # registry entirely (= the registry every existing attestation was taken against) an agent
    # that does not use them hashes identically to today.
    saved = c.REGISTRY
    try:
        c.REGISTRY = tuple(sp for sp in saved if sp.key in c._ATTESTED_BASELINE_KEYS or not sp.signed)
        # compute the era signatures, then compare against the current registry
        era = [(c.posture_signature(pf), c.capability_signature(pf, id_to_tag=lambda i: "T")) for pf in (hardened, {"tag": "B"})]
    finally:
        c.REGISTRY = saved
    now = [(c.posture_signature(pf), c.capability_signature(pf, id_to_tag=lambda i: "T")) for pf in (hardened, {"tag": "B"})]
    check("posture: attestation-era signatures == current signatures for agents not using the new keys",
          [e[0] for e in era] == [n[0] for n in now])
    check("capability: attestation-era signatures == current signatures", [e[1] for e in era] == [n[1] for n in now])
    # The attestation-era projection, as a LITERAL (independent of the code under test — an
    # era/now comparison that runs the same function on both sides cannot see a formula change;
    # the first fix passed such a comparison and moved every production signature).
    era_posture = {"knowledgeConfig": {"sufficiencyThreshold": 0.4, "requireSignedKnowledge": False},
                   "postureConfig": {"mode": "observe"},
                   "groundingConfig": {"mode": "enforce", "corpus": {"mode": None}, "maxOutOfScopeDbs": None, "minNumericFidelity": None},
                   "guardrailConfig": {"inbound": {"mode": "observe", "injection": True}, "outbound": {"mode": "observe", "pii": True, "toxicity": True, "exfil": True}},
                   "honestyConfig": {"mode": "observe", "grainJudge": False}}
    era_capability = {"profile_type": None, "session_primer": None, "tools": None, "prompts": None,
                      "useMcpTools": False, "useKnowledgeCollections": False, "genieConfig": None,
                      "dualModelConfig": None, "componentConfig": []}
    check("the hardened agent's posture signature is byte-equal to its attestation-era literal",
          sig == era_posture)
    check("…and its capability signature", c.capability_signature(hardened) == era_capability)
    # and the property for the NEXT key: registering a fresh signed key changes nothing
    try:
        c.REGISTRY = saved + (c.ConfigKeySpec(key="futureConfig", signed=True,
                                              signed_defaults=((("on",), False),)),)
        check("registering a fresh signed key leaves a hardened agent's signature unchanged",
              c.posture_signature(hardened) == sig)
        check("…and the bare profile's", c.posture_signature({"tag": "B"}) == now[1][0])
        check("…but an agent that USES the fresh key moves",
              c.posture_signature({**hardened, "futureConfig": {"on": True}}) != sig)
    finally:
        c.REGISTRY = saved
    # a real value in a post-baseline key DOES enter the hash
    check("a configured turn budget alters the signature",
          c.posture_signature({**hardened, "turnBudget": {"steps": 6}}) != sig)
    check("an enabled outcome loop alters the signature",
          c.posture_signature({**hardened, "outcomeConfig": {"enabled": True, "maxRevisions": 1}}) != sig)


def t_attested_baseline_is_frozen():
    """The baseline is the set of keys signed when the 2026-09-20 attestations were taken. It
    may only ever LOSE members (a retired key), never gain one: adding a key here is exactly
    the flip this rule exists to prevent. Pinned by name so the edit fails loudly."""
    pinned = {"profile_type", "session_primer", "tools", "prompts", "useMcpTools",
              "useKnowledgeCollections", "genieConfig", "dualModelConfig", "knowledgeConfig",
              "componentConfig", "postureConfig", "groundingConfig", "guardrailConfig", "honestyConfig"}
    check("the attested baseline holds no key beyond the pinned set (a key was ADDED — remove it)",
          c._ATTESTED_BASELINE_KEYS <= pinned)
    check("every baseline key is a signed key", c._ATTESTED_BASELINE_KEYS <= set(c.signed_keys()))
    check("the S5/S7 keys that caused the flip are NOT in the baseline",
          not ({"turnBudget", "outcomeConfig"} & c._ATTESTED_BASELINE_KEYS))
    check("the baseline is immutable", isinstance(c._ATTESTED_BASELINE_KEYS, frozenset))


def main():
    print("=" * 70)
    print("Portable profile-config contract — module tests")
    print("=" * 70)
    for t in (t_registry, t_projection_roundtrip, t_instance_local_refs,
              t_alias, t_absent_equals_defaults,
              t_empty_selection_equals_absent, t_selection_getters_return_a_list,
              t_posture_is_acting, t_downgrade_modes, t_downgrade_thresholds,
              t_downgrade_authority, t_new_signed_key_flips_nothing,
              t_attested_baseline_is_frozen):
        t()
    print(f"\n{_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
