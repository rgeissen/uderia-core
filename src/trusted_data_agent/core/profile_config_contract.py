"""The single declarative contract for portable profile configuration.

Root cause this addresses: which profile config keys travel with an agent pack was encoded
as **four independent hand-maintained allow-lists** in ``core/agent_pack_manager.py`` (the
export ``prof_entry`` builder, the import ``_build_profile``, the fork/subscribe
``_to_manifest_entry``, and the v1.0→v1.1 ``_normalize_v10_to_v11``). A key missing from any
one of them silently vanished. That defect recurred three times — ``componentConfig``, then
``skillsConfig`` in the v1.0 normalizer, then the whole trust-posture block — so the fix is
to delete the allow-lists, not to extend them again.

One registry, four behaviours
-----------------------------
A shared *list* would have fixed the instance but not the class, because the requirements
are per-key policy, not membership:

===================  ==========================================================
``portable``         does the key travel with a pack at all
``signed``           is the key part of the agent's signed **safety posture**
``mode_paths``       which sub-paths carry an enforcement-ladder mode
``flag_paths``       which sub-paths are booleans where ``True`` is the safe state
``aliases``          legacy key names still read on the source side
===================  ==========================================================

Adding a future profile config key is one row, and it is structurally impossible for it to
be portable-but-unsigned by accident.

What is signed, and what deliberately is not
--------------------------------------------
Only the three **answer gates** — grounding, guardrails, honesty — enter the agent's
signature. They determine *what the agent will and will not say*, which is intrinsic to the
agent and must be identical wherever it runs.

``authorityConfig`` is **portable but not signed**. It governs what the agent may *do* on
whose behalf, is a strict subset of the deploying owner's permissions, and legitimately
differs per deployment — hashing it would make the "changed" badge fire as often for a
correct adaptation as for tampering, destroying the signal. It is still covered by the
downgrade guard, and by fail-closed enforcement at every tool chokepoint plus audit
attribution. ``knowledgeConfig.maxSensitivity`` is excluded for the same reason: it is a
retrieval-access ceiling whose meaning depends on the local collections' own labels.

``knowledgeConfig.sufficiencyThreshold`` **is** signed, despite living in an otherwise
unsigned block: the honesty gate reads it to decide when the agent must answer "the
knowledge base does not cover this" instead of asserting specifics. That is *what the
agent will and will not say* — the criterion above — so it is intrinsic, not deployment-
bound. The rest of ``knowledgeConfig`` (retrieval tuning: ``maxDocs``, ``maxTokens``,
``minRelevanceScore``) stays out of the signature so ordinary tuning cannot flip a badge,
and *which* repositories are bound is covered far better than a hash could: they are
compositional ingredients in their own right (see ``core/trust/composition``).

``groundingConfig.allowOutOfScope`` is deliberately **not** part of the signature: it is
parsed and stored but never read by the gate, so including it would flip the badge on a
change that alters no behaviour.

Placement: ``core/trust/`` is part of the physically-isolatable Ontology Suite, so this
contract lives in ``core/`` and trust imports it — never the reverse.

Pure: stdlib only, zero I/O, no platform imports beyond the equally-pure mode ladder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
# TC-62: `Callable` is used in annotations — saved only by `from __future__ import
# annotations` until any tool calls `typing.get_type_hints` on these classes.
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple  # noqa: F401

from trusted_data_agent.core import posture_modes as _modes


def config_block(profile: Any, *path: str) -> Dict[str, Any]:
    """A profile's config block at `path`, ALWAYS a dict — never a raise.

    Every gate resolves its posture by walking into a profile's config
    (`profile["guardrailConfig"]["outbound"]["mode"]`). Those walks assumed dict shape at
    every level, so a block that is not a dict raised `AttributeError` — outside the
    resolver's own guard, and therefore inside the gate's fail-open `except`.

    The consequence was not one broken gate. A single malformed block took down
    **all four gates on the same turn**: two real turns recorded
    `inbound=error · outbound=error · grounding=error · honesty=error` together, the
    protections silently off, and the run still delivered its answer. A profile shape is
    exactly the kind of thing that drifts across an editor change or a config migration,
    so the resolvers must degrade to their defaults rather than take every gate with them.

    Absent, null, or wrong-typed at any level ⇒ `{}`, which each resolver already reads
    as "no configuration, use the safe default". Never raises, for any input.
    """
    node: Any = profile
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


# ── Registry ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConfigKeySpec:
    """Policy for one top-level profile config key."""
    key: str
    portable: bool = True
    signed: bool = False
    #: Legacy key names read (in order) when the canonical key is absent, source side only.
    aliases: Tuple[str, ...] = ()
    #: Sub-paths carrying an enforcement-ladder mode string.
    mode_paths: Tuple[Tuple[str, ...], ...] = ()
    #: Sub-paths that are booleans where ``True`` is the protective state.
    flag_paths: Tuple[Tuple[str, ...], ...] = ()
    #: Numeric sub-paths that are a **minimum bar the agent must clear** — lowering one,
    #: or dropping a configured one entirely, relaxes protection.
    floor_paths: Tuple[Tuple[str, ...], ...] = ()
    #: Numeric sub-paths that **cap something permissive** — raising one allows more.
    #: Compared only when both sides are numeric: for these, absent means "inherit the
    #: admin threshold", which cannot be ranked without reading admin config.
    ceiling_paths: Tuple[Tuple[str, ...], ...] = ()
    #: Documented runtime default for each signed sub-path, so an ABSENT block hashes
    #: identically to one explicitly written at its defaults. Without this the badge would
    #: flip amber merely because the profile editor materialises absent blocks on save.
    #: The empty path ``()`` means *the key's own value* — that is how a scalar key
    #: (``profile_type``, ``tools``, ``useMcpTools``) participates without a wrapper dict.
    signed_defaults: Tuple[Tuple[Tuple[str, ...], Any], ...] = ()
    #: Which signed dimension this key belongs to. ``posture`` = what the agent will and
    #: will not *say* (the answer gates); ``capability`` = what it can *do* (its type, its
    #: standing instructions, its tool/prompt surface, its components). Both are signed and
    #: both are portable; they are projected separately so the badge can say *which* kind of
    #: change occurred, and so the vocabulary in the docs stays true.
    signed_dimension: str = "posture"
    #: Sub-paths holding **profile ids**, which a marketplace install remaps. They are
    #: hashed in *tag space* via an injected resolver so a coordinator survives publish →
    #: install; hashing raw ids would make every installed pack permanently 🟠.
    #: A path resolves either a list of ids or a dict *keyed* by id.
    #: They are also INSTANCE-LOCAL for transport — see ``local_ref_paths``.
    id_ref_paths: Tuple[Tuple[str, ...], ...] = ()
    #: Sub-paths holding ids of OTHER instance-local artefacts (collections, …) that a
    #: marketplace install remaps but that are not profile ids, so they must not be pushed
    #: through the profile-id tag resolver when hashing. For TRANSPORT they behave exactly
    #: like ``id_ref_paths``.
    #:
    #: THE TRANSPORT RULE — an instance-local path is never carried by this contract in
    #: either direction: ``apply_to_manifest`` does not emit it, ``apply_from_manifest``
    #: does not write it. The remap belongs to the caller that owns it (the coordinator's
    #: ``child_tags`` → new ids, the collection ``collection_refs`` → new ids), and both
    #: directions MERGE rather than replace so that remap can run before or after this
    #: contract without the two clobbering each other. Declaring a path here is what makes
    #: a key safe to be portable at all; the alternative — remembering to exclude it at
    #: every call site — is the defect this module exists to end.
    local_ref_paths: Tuple[Tuple[str, ...], ...] = ()
    #: Optional pure reducer from the raw stored value to its **effective** value, applied
    #: before hashing. Used where "absent" and "present but inert" must hash identically
    #: and no single documented default expresses that (a disabled session primer injects
    #: nothing, so it is equivalent to no primer). Must be deterministic and side-effect
    #: free; it never sees ids that have not already been mapped to tags.
    canonicalize: Optional[Callable[[Any], Any]] = None
    #: A key this contract does NOT carry (``portable=False``) but that a pack still carries —
    #: by NAME, resolved to this instance's ids by the importer, exactly like ``mcpServerId``
    #: travels as ``mcpServerName``. Such a key must be an import binding
    #: (``IMPORT_BINDING_KEYS``); declaring it here is what lets a key be signed without being
    #: contract-portable (a signed key that did not travel at all could never be reinstated).
    travels_as_binding: bool = False


def _canon_session_primer(v: Any) -> Any:
    """A primer that is disabled or has no statements injects nothing → hashes as absent.

    Without this, an agent saved through the editor (which materialises
    ``{enabled: false, mode: …, statements: []}``) would hash differently from one that
    never had a primer, flipping a badge for a change that alters no behaviour.
    """
    if not isinstance(v, dict):
        return None
    statements = [s for s in (v.get("statements") or []) if str(s).strip()]
    if not v.get("enabled") or not statements:
        return None
    canon = {"mode": v.get("mode"), "statements": [str(s) for s in statements]}
    # Whether the primer turn may ACT is part of what the agent does (a warm-up runs
    # queries on session start; a briefing cannot). Signed only in its non-default
    # form so every agent signed before the key existed hashes byte-identically.
    if v.get("allow_tools") is False:
        canon["allow_tools"] = False
    # `collapsed` (render the primer turn folded in the chat log) is PRESENTATION: it
    # changes nothing about what the agent does or says, so it never enters the hash —
    # a badge must not flip because someone tidied the transcript.
    return canon


def _canon_selection(v: Any) -> Any:
    """A tool/prompt selection: an EMPTY selection and an ABSENT key are the same agent.

    `get_profile_enabled_tools` / `…_prompts` treat an absent key as an empty selection,
    so a profile that stores `[]` and one that stores nothing run with exactly the same
    capability. And a signed agent's own export produces the absent form:
    `project_to_manifest` drops `[]` because it is falsy, deliberately, so that an absent
    key keeps its "preserve what is already there" meaning on import.

    Without this the two forms hash differently and a faithful copy cannot take the
    publisher's signature. Observed live: forking a signed pack left every expert
    `unsigned` while the coordinator — which selects neither tools nor prompts — 
    reinstated cleanly, because only the experts had a selection to lose.

    Duplication is not capability either — the same tool listed twice is the same agent —
    so the selection is normalised to a set. (Ordering was already handled downstream:
    ``signature_for`` sorts any list value. The sort here is redundant with that and kept
    only so this function is correct read on its own.)
    """
    if not isinstance(v, (list, tuple)):
        return None
    items = sorted({str(x) for x in v if str(x).strip()})
    return items or None


def _canon_additional_servers(v: Any) -> Any:
    """A multi-server agent's ADDITIONAL MCP servers as capability: each server's alias (the
    names its tools carry, ``<alias>__…``) and its own tool/prompt selection — never the server
    id, which is instance-local (the deployment half of the trust envelope hashes each server's
    resolved catalog). Order-independent (sorted by alias). A single-server agent — absent or
    empty — hashes as absent, so no existing signature moves (implementation plan stage 2)."""
    if not isinstance(v, list):
        return None
    out = []
    for e in v:
        if not isinstance(e, dict) or not e.get("alias"):
            continue
        out.append({"alias": str(e["alias"]), "tools": _canon_selection(e.get("tools")),
                    "prompts": _canon_selection(e.get("prompts"))})
    return sorted(out, key=lambda x: x["alias"]) or None


def _canon_component_config(v: Any) -> Any:
    """The set of ENABLED component ids — the capability surface, order-independent.

    `intensity` is deliberately excluded: it tunes instruction verbosity, not what the
    agent can do, and signing it would flip badges on a tuning dial.
    """
    if not isinstance(v, dict):
        return []
    out = [cid for cid, cfg in v.items() if isinstance(cfg, dict) and cfg.get("enabled")]
    # Model B: `knowledge_graph.groundOn == "released"` changes WHICH ontology the agent
    # reads — signed. Any other value hashes as absent, so every existing profile is
    # byte-identical (a tuning-dial flip on the wrong key must not move a badge).
    kg = v.get("knowledge_graph") if isinstance(v.get("knowledge_graph"), dict) else None
    if kg and str(kg.get("groundOn") or "").lower() == "released":
        out.append("knowledge_graph:groundOn=released")
    # P1 · the Database Navigator's `query` opt-in lets the agent run its OWN SQL — a new
    # capability, so signed. Only when enabled: every existing profile hashes as before.
    # WHICH connections it may query is instance-local and lives in the deployment half.
    nav = v.get("database_navigator") if isinstance(v.get("database_navigator"), dict) else None
    q = nav.get("query") if nav else None
    if nav and nav.get("enabled") and isinstance(q, dict) and q.get("enabled") is True:
        out.append("database_navigator:query")
    # Platform roles decide which of the platform's operations the agent can use — capability,
    # so signed: each held role with a digest of what it grants, so a widened role moves the
    # signature of every agent holding it (core/platform_roles.canonical).
    try:
        from trusted_data_agent.core.platform_roles import canonical as _platform_roles
        out.extend(_platform_roles(v))
    except Exception:  # noqa: BLE001 — a hash never fails for want of the roles module
        pass
    return sorted(out)


def _canon_genie_config(v: Any) -> Any:
    """Coordinator routing that changes behaviour: which experts, their per-expert
    settings, and whether trusted-expert routing is required. `temperature` and
    concurrency are tuning and stay out. Expects ids ALREADY mapped to tags."""
    if not isinstance(v, dict):
        return None
    slaves = sorted(str(s) for s in (v.get("slaveProfiles") or []))
    settings = v.get("slaveProfileSettings") or {}
    # TC-61 — absent must equal inert: a per-slave entry whose every flag is falsy (the
    # documented default, e.g. {"fullResultPassthrough": false} or {}) is behaviourally
    # identical to no entry, so it must hash identically. Without this, merely touching
    # the "Full data" checkbox off/on in the editor flipped a coordinator's badge.
    canon_settings = {}
    for k in sorted(settings):
        entry = settings.get(k)
        if not isinstance(entry, dict):
            continue
        kept = {sk: entry[sk] for sk in sorted(entry) if entry[sk]}
        if kept:
            canon_settings[str(k)] = kept
    if not slaves and not canon_settings and not v.get("requireTrustedExperts"):
        return None
    return {"slaveProfiles": slaves, "slaveProfileSettings": canon_settings,
            "requireTrustedExperts": bool(v.get("requireTrustedExperts"))}


def _canon_dual_model(v: Any) -> Any:
    """Dual-model *shape* without instance-local config ids.

    Whether a second model is used, and in which role, is intrinsic; *which stored LLM
    configuration* fills that role is deployment-bound and belongs to the deployment
    signature, so any ``…Id`` key is dropped here.
    """
    if not isinstance(v, dict):
        return None
    kept = {k: v[k] for k in sorted(v)
            if not str(k).lower().endswith("id") and v[k] is not None}
    return kept or None


REGISTRY: Tuple[ConfigKeySpec, ...] = (
    # ── capability: what the agent can DO (all portable — they travel in the manifest) ──
    ConfigKeySpec(key="profile_type", signed=True, signed_dimension="capability",
                  signed_defaults=(((), None),)),
    ConfigKeySpec(key="session_primer", signed=True, signed_dimension="capability",
                  signed_defaults=(((), None),), canonicalize=_canon_session_primer),
    ConfigKeySpec(key="tools", signed=True, signed_dimension="capability",
                  signed_defaults=(((), None),), canonicalize=_canon_selection),
    ConfigKeySpec(key="prompts", signed=True, signed_dimension="capability",
                  signed_defaults=(((), None),), canonicalize=_canon_selection),
    # Stage 2 (multi-source): the agent's OTHER MCP servers are part of what it can do — the
    # tools its plans name (`<alias>__…`). Not carried by this contract (the entries hold
    # instance-local server ids): a pack manifest carries them by NAME (`mcpServers`) and the
    # importer resolves them — an import BINDING, like `mcpServerId`. Signed in its id-free form.
    ConfigKeySpec(key="additionalMcpServers", portable=False, travels_as_binding=True, signed=True,
                  signed_dimension="capability", signed_defaults=(((), None),),
                  canonicalize=_canon_additional_servers),
    ConfigKeySpec(key="useMcpTools", signed=True, signed_dimension="capability",
                  signed_defaults=(((), False),)),
    ConfigKeySpec(key="useKnowledgeCollections", signed=True, signed_dimension="capability",
                  signed_defaults=(((), False),)),
    ConfigKeySpec(key="genieConfig", signed=True, signed_dimension="capability",
                  signed_defaults=(((), None),),
                  id_ref_paths=(("slaveProfiles",), ("slaveProfileSettings",)),
                  canonicalize=_canon_genie_config),
    ConfigKeySpec(key="dualModelConfig", signed=True, signed_dimension="capability",
                  signed_defaults=(((), None),), canonicalize=_canon_dual_model),
    ConfigKeySpec(
        key="knowledgeConfig",
        # Signed for exactly ONE sub-path — the honesty gate's sufficiency bar. Only
        # paths named in `signed_defaults` enter the signature, so retrieval tuning and
        # the access ceiling stay out (see the module docstring).
        signed=True,
        # K5 (OBDA-READ scope §1.1): `requireSignedKnowledge` is the knowledge-modality
        # analogue of the data surface's `exclusive` — it restricts what the agent
        # will CONSUME (only integrity-signed corpora back its answers), which is
        # *what the agent will and will not say* — so it is signed AND
        # downgrade-guarded: a pack republish cannot silently relax it.
        signed_defaults=((("sufficiencyThreshold",), None),
                         (("requireSignedKnowledge",), False)),
        floor_paths=(("sufficiencyThreshold",),),
        flag_paths=(("requireSignedKnowledge",),),
        # Bound collections are instance-local: they travel as `collection_refs` and are
        # remapped to the importing user's collection ids by the rag_focused branch of
        # `_build_profile`. Declared here so the transport rule covers them by construction
        # instead of via a hand-maintained exclusion at each call site.
        local_ref_paths=(("collections",),),
    ),
    # skillsConfig is NOT signed here: the *set of active skills* is already covered
    # compositionally — each active skill is an ingredient, so activating one changes the
    # composition hash. Signing the block too would double-count and flip on inert edits.
    ConfigKeySpec(key="skillsConfig"),
    ConfigKeySpec(key="componentConfig", signed=True, signed_dimension="capability",
                  signed_defaults=(((), None),), canonicalize=_canon_component_config),
    ConfigKeySpec(key="recommenderConfig"),
    # S2 — the Attested Cache: per-agent prompt-cache opt-out + cache lifetime. Portable
    # but UNSIGNED: it changes what the stable prefix COSTS, never what the agent says or
    # does (the recommenderConfig / retrieval-tuning reasoning).
    ConfigKeySpec(key="cacheConfig"),
    # S6 — the governed tool surface {mode: all|governed}: portable (a pack carries the
    # author's choice), deliberately UNSIGNED — it decides what the model SEES first, never
    # what it MAY do (the profile's enabled tools + Agent Authority stay the boundary), so a
    # governed↔all toggle must not flip a badge.
    ConfigKeySpec(key="toolSurface"),
    # S7 — the outcome loop {enabled, maxRevisions, revisableKinds}: whether an answer that
    # fails the gate is revised before delivery is posture (signed); `enabled` is a flag and
    # `maxRevisions` a FLOOR (fewer revisions = weaker) under the downgrade guard; absent hashes
    # as {enabled: false, maxRevisions: null} so an untouched agent flips no badge.
    ConfigKeySpec(
        key="outcomeConfig",
        signed=True,
        flag_paths=(("enabled",),),
        floor_paths=(("maxRevisions",),),
        signed_defaults=((("enabled",), False), (("maxRevisions",), None)),
    ),
    # S5 — the turn budget {steps, tokens, seconds}: how much an agent may SPEND on one turn is
    # posture (signed); a raised ceiling permits more, so each unit is guarded as a ceiling; an
    # absent block hashes as one written at its defaults (all None) so the editor's save cannot
    # flip a badge.
    ConfigKeySpec(
        key="turnBudget",
        signed=True,
        ceiling_paths=(("steps",), ("tokens",), ("seconds",)),
        signed_defaults=((("steps",), None), (("tokens",), None), (("seconds",), None)),
    ),
    # Per-profile JUDGED-station config (PROP-002 follow-up): opt this agent's turns
    # in/out of the LLM-judge layer, override the sample rate, and control whether a
    # judge condemnation files an Assurance-Inbox incident. Portable but UNSIGNED —
    # it configures MEASUREMENT of the agent, not what the agent will say or do
    # (same reasoning as recommenderConfig and knowledgeConfig's retrieval tuning).
    ConfigKeySpec(key="judgeConfig"),
    # S9 — attachment sources {transcribeImages}: whether an image nothing could read is
    # transcribed by the model (recorded UNVERIFIED, never evidence). Portable, UNSIGNED —
    # it adds an unverified reading the assurance surface names as such; it never changes
    # what the agent may do or which evidence the gate holds the answer to.
    ConfigKeySpec(key="attachmentConfig"),
    # Agent-level posture default (the five-gates-one-intent consolidation): a gate
    # whose own block carries no mode inherits ``postureConfig.mode`` (composites
    # ``annotate``/``review`` included). Resolution lives in ONE place —
    # ``core/posture_modes.effective_posture`` — consumed by the runtimes, the REST
    # ``posture`` stamp, and the signing verdict alike. Signed + downgrade-guarded:
    # weakening the default weakens every inheriting gate at once, which is exactly
    # the "protection silently weakened by an import" class the guard exists for.
    # ``signed_defaults`` makes an absent block hash identically to an explicit
    # ``{mode: "observe"}``, so the editor materialising the default flips no badge.
    ConfigKeySpec(
        key="postureConfig",
        signed=True,
        mode_paths=(("mode",),),
        signed_defaults=((("mode",), "observe"),),
    ),
    ConfigKeySpec(
        key="groundingConfig",
        signed=True,
        mode_paths=(("mode",), ("corpus", "mode")),
        # These two were signed but unguarded: a pack could keep the gate in `enforce`
        # while quietly relaxing the numbers the gate fires on.
        floor_paths=(("minNumericFidelity",),),
        ceiling_paths=(("maxOutOfScopeDbs",),),
        signed_defaults=(
            (("mode",), "observe"),
            (("corpus", "mode"), None),          # None = inherit the gate mode
            (("maxOutOfScopeDbs",), None),       # None = inherit the admin threshold
            (("minNumericFidelity",), None),     # None = inherit the admin threshold
        ),
    ),
    ConfigKeySpec(
        key="guardrailConfig",
        signed=True,
        mode_paths=(("inbound", "mode"), ("outbound", "mode")),
        flag_paths=(("inbound", "injection"), ("outbound", "pii"),
                    ("outbound", "toxicity"), ("outbound", "exfil")),
        signed_defaults=(
            (("inbound", "mode"), "observe"),
            (("inbound", "injection"), True),
            (("outbound", "mode"), "observe"),
            (("outbound", "pii"), True),
            (("outbound", "toxicity"), True),
            (("outbound", "exfil"), True),
        ),
    ),
    ConfigKeySpec(
        key="honestyConfig",
        signed=True,
        mode_paths=(("mode",),),
        # `grainJudge` absent-hashes-as-False (the requireSignedKnowledge precedent):
        # an explicit False written by REST is semantically identical to absent and
        # must not flip an attestation.
        signed_defaults=((("mode",), "observe"), (("grainJudge",), False)),
    ),
    ConfigKeySpec(
        key="authorityConfig",
        # Portable, but NOT signed — see the module docstring.
        aliases=("identityConfig",),
        # `dataSurface.exclusive` joins `deny_writes` under the downgrade guard: a
        # republish that silently drops surface-only confinement is a protection
        # downgrade exactly as a dropped read-only flag is (OBDA-READ Phase C, E1).
        flag_paths=(("deny_writes",), ("dataSurface", "exclusive"),
                    ("enforce_interactive",)),
        # The agents it may ask (TDA_Platform `ask`) are this instance's profile ids — never
        # carried by a pack: the receiving owner decides whom an installed agent may ask.
        local_ref_paths=(("platformSurface", "ask"),),
    ),
)

_BY_KEY: Dict[str, ConfigKeySpec] = {s.key: s for s in REGISTRY}


# ── the import boundary ─────────────────────────────────────────────────────
#
# A pack import may write THREE kinds of key onto a profile, and only three:
#   * the registry's PORTABLE keys — what the agent IS, carried by the manifest;
#   * the INSTANCE BINDINGS resolved at import time — which LLM, which MCP server,
#     which planner repository, the identity fields;
#   * nothing else. In particular never DEPLOYMENT-LOCAL state: the classification
#     snapshot is this instance's reachable catalog (the deployment half of the
#     trust envelope hashes it) and the reclassification flag is set by a rule, not
#     shipped. `_build_profile` emitted the snapshot EMPTY and the in-place merge
#     wrote it over every consumer's live one (2026-09-07); the filter in
#     `_upsert_pack_profile` reads THIS declaration, so the next stray key cannot
#     ride along by being forgotten at one site.
IMPORT_BINDING_KEYS: Tuple[str, ...] = (
    "id", "tag", "name", "description",
    "llmConfigurationId", "mcpServerId", "classification_mode",
    "contextWindowTypeId", "ragCollections",
    # S4 — the SIGNED fallback LLM: an instance binding like the primary (never portable —
    # a subscriber has their own configurations); its resolved identity enters the
    # DEPLOYMENT half of the trust envelope, so "which fallback" is itself attested.
    "llmFallbackConfigurationId",
    # Stage 2 (multi-source): the agent's additional MCP servers, resolved from the manifest's
    # `mcpServers` names to this instance's server ids at import (never carried as ids).
    "additionalMcpServers",
)
# PROP-006 phase 2: an ADDITIONAL MCP server's snapshot is this instance's catalog exactly like the
# primary's (CL1) — see core/agent_mcp_servers.py.
DEPLOYMENT_LOCAL_KEYS: Tuple[str, ...] = ("classification_results", "needs_reclassification",
                                          "additionalMcpClassification")


def import_writable_keys() -> Tuple[str, ...]:
    """Every key an agent-pack import may write onto an EXISTING profile."""
    return IMPORT_BINDING_KEYS + tuple(k for k in portable_keys() if k not in IMPORT_BINDING_KEYS)


def spec_for(key: str) -> Optional[ConfigKeySpec]:
    return _BY_KEY.get(key)


def portable_keys() -> Tuple[str, ...]:
    return tuple(s.key for s in REGISTRY if s.portable)


def binding_keys() -> Tuple[str, ...]:
    """Keys a pack carries as an import BINDING (by name, resolved on import), not by this
    contract. Each is in ``IMPORT_BINDING_KEYS``."""
    return tuple(s.key for s in REGISTRY if s.travels_as_binding)


def signed_keys() -> Tuple[str, ...]:
    return tuple(s.key for s in REGISTRY if s.signed)


# ── Nested access helpers ─────────────────────────────────────────────────────────

_MISSING = object()


def _get_path(block: Any, path: Sequence[str]) -> Any:
    node = block
    for step in path:
        if not isinstance(node, dict) or step not in node:
            return _MISSING
        node = node[step]
    return node


def _set_path(block: Dict, path: Sequence[str], value: Any) -> None:
    node = block
    for step in path[:-1]:
        node = node.setdefault(step, {})
    node[path[-1]] = value


def _read_block(profile: Any, spec: ConfigKeySpec) -> Any:
    """The block for ``spec`` off ``profile``, honouring legacy aliases.

    Alias precedence matches the *enforcement* path (``core/agent_authority.py``): the
    canonical key wins whenever it is present at all, even as an empty dict. Two reporting
    sites historically used ``or`` instead, which resolves an empty canonical block
    differently — the enforcement semantics are authoritative.
    """
    if not isinstance(profile, dict):
        return None
    if spec.key in profile and profile[spec.key] is not None:
        return profile[spec.key]
    for alias in spec.aliases:
        if profile.get(alias) is not None:
            return profile[alias]
    return None


# ── Portability: the two calls that replace four allow-lists ──────────────────────

def instance_local_paths(spec: ConfigKeySpec) -> Tuple[Tuple[str, ...], ...]:
    """Every sub-path of ``spec`` that holds an id only this instance can resolve."""
    return tuple(spec.id_ref_paths) + tuple(spec.local_ref_paths)


def _strip_local_refs(block: Any, spec: ConfigKeySpec) -> Any:
    """``block`` without its instance-local sub-paths (a shallow copy; input untouched)."""
    paths = instance_local_paths(spec)
    if not paths or not isinstance(block, dict):
        return block
    out = dict(block)
    for path in paths:
        parent = out
        for step in path[:-1]:
            nxt = parent.get(step)
            if not isinstance(nxt, dict):
                parent = None
                break
            parent[step] = dict(nxt)      # copy on the way down — never mutate the input
            parent = parent[step]
        if isinstance(parent, dict):
            parent.pop(path[-1], None)
    return out


def _merge_block(target: Dict[str, Any], key: str, block: Any,
                 spec: ConfigKeySpec) -> None:
    """Write ``block`` at ``key``, preserving whatever sits at the instance-local paths.

    For a key with no instance-local paths this is the historical whole-block replace.
    For one that has them the write MERGES, so the caller's remap (which owns those paths)
    survives regardless of whether it ran before or after this contract.
    """
    stripped = _strip_local_refs(block, spec)
    existing = target.get(key)
    if not instance_local_paths(spec) or not isinstance(existing, dict) or not isinstance(stripped, dict):
        if stripped or not instance_local_paths(spec):
            target[key] = stripped
        return
    merged = dict(existing)
    merged.update(stripped)
    # A NESTED instance-local path (``platformSurface.ask``) sits inside a block the update
    # above replaced wholesale — put what this instance held there back.
    for path in instance_local_paths(spec):
        if len(path) < 2:
            continue
        held = _get_path(existing, path)
        if held is _MISSING:
            continue
        parent = merged
        for step in path[:-1]:
            nxt = parent.get(step)
            parent[step] = dict(nxt) if isinstance(nxt, dict) else {}
            parent = parent[step]
        parent[path[-1]] = held
    target[key] = merged


def project_to_manifest(profile: Any, *, keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """The portable config of ``profile``, with instance-local ids removed.

    Returns only keys that are present and non-empty, so a manifest never carries noise and
    an absent key keeps its "preserve what is already there" meaning on import. Prefer
    :func:`apply_to_manifest` at a call site that also writes the instance-local paths
    itself — ``dict.update`` with this result would replace those keys wholesale.
    """
    out: Dict[str, Any] = {}
    wanted = set(keys) if keys is not None else None
    for spec in REGISTRY:
        if not spec.portable or (wanted is not None and spec.key not in wanted):
            continue
        block = _strip_local_refs(_read_block(profile, spec), spec)
        if block:
            out[spec.key] = block
    return out


def apply_to_manifest(profile: Any, entry: Dict[str, Any], *,
                      keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Source side (export / fork / v1.0 normalize): live profile → manifest ``entry``.

    The mirror of :func:`apply_from_manifest`. Instance-local ids are not emitted, and the
    write merges, so a caller that has already placed portable references (a coordinator's
    tag-keyed ``slaveProfileSettings``) keeps them.
    """
    if not isinstance(profile, dict):
        return entry
    wanted = set(keys) if keys is not None else None
    for spec in REGISTRY:
        if not spec.portable or (wanted is not None and spec.key not in wanted):
            continue
        block = _read_block(profile, spec)
        if block:
            _merge_block(entry, spec.key, block, spec)
    return entry


def apply_from_manifest(entry: Any, target: Dict[str, Any], *,
                        keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Sink side (``_build_profile``): copy portable config from a manifest entry.

    A key absent from the manifest is NOT written, preserving the long-standing fail-safe
    that absence means "leave the existing profile's value alone" on an update-in-place.
    Instance-local ids in the entry are ignored and whatever the caller already resolved at
    those paths is preserved — the manifest's ids belong to the publisher's instance.
    """
    if not isinstance(entry, dict):
        return target
    wanted = set(keys) if keys is not None else None
    for spec in REGISTRY:
        if not spec.portable or (wanted is not None and spec.key not in wanted):
            continue
        block = _read_block(entry, spec)
        if block:
            _merge_block(target, spec.key, block, spec)
    return target


# ── Signed posture canonicalization ───────────────────────────────────────────────

def _map_ids_to_tags(value: Any, id_to_tag: Optional[Callable[[str], str]]) -> Any:
    """Rewrite profile ids to tags, in a list of ids or a dict keyed by id.

    An id that cannot be resolved is kept verbatim — dropping it would silently shrink a
    coordinator's expert set in the hash, which is exactly the kind of quiet weakening the
    signature exists to surface.
    """
    if id_to_tag is None:
        return value
    if isinstance(value, list):
        return [id_to_tag(str(v)) or str(v) for v in value]
    if isinstance(value, dict):
        return {(id_to_tag(str(k)) or str(k)): v for k, v in value.items()}
    return value


def signature_for(profile: Any, dimension: str = "posture",
                  id_to_tag: Optional[Callable[[str], str]] = None) -> Dict[str, Any]:
    """The canonical, order-independent signed projection of one dimension.

    Every signed sub-path is resolved to an explicit value — an absent block yields exactly
    the same structure as one written at its documented defaults, so merely opening and
    saving a profile in the editor can never flip a green badge to amber. Modes are
    normalized through the shared ladder, so ``review`` and ``enforce`` are distinguished
    (they differ in retention) while unrecognised strings collapse to the same default the
    runtime would use.

    Two dimensions share this machinery: ``posture`` (what the agent will and will not say)
    and ``capability`` (what it can do). ``id_to_tag`` maps profile ids into tag space for
    keys declaring ``id_ref_paths``, so a coordinator's routing hashes identically before
    and after a marketplace id remap.

    **A signed key registered after the attestation baseline is OMITTED at its defaults.**
    Found on production 2026-09-20: S5's ``turnBudget`` and S7's ``outcomeConfig`` joined the
    registry at their defaults and every hardened agent — 19 of 19 on the demo account —
    flipped to CHANGED, because a defaulted block still entered the hash. The per-key
    comments promised "an absent block flips no badge"; that held for a SAVE of an existing
    key and failed for the REGISTRATION of a new one. The fix cannot simply omit every key
    at its defaults: the attestations already taken contain the default projections of the
    keys signed at the time (a corpus-only agent's attestation carries
    ``groundingConfig: {mode: observe, …}``), so changing their formula would move every
    signature the same way. Hence the split — the keys in ``_ATTESTED_BASELINE_KEYS`` keep the
    projection they were attested with; any key outside it enters the signature only when the
    agent actually USES it (a non-default value somewhere under the key). The baseline is
    FROZEN; registering a key never touches it, so the next key flips nothing by construction.
    """
    out: Dict[str, Any] = {}
    for spec in REGISTRY:
        if not spec.signed or spec.signed_dimension != dimension:
            continue
        proj = _project_spec(profile, spec, dimension, id_to_tag)
        if spec.key not in _ATTESTED_BASELINE_KEYS:
            # the projection of the SAME spec over an empty profile: what "at its defaults" means
            if proj == _project_spec({}, spec, dimension, id_to_tag):
                continue
        out[spec.key] = proj[spec.key]
    return out


# The signed keys whose DEFAULT projection is part of every attestation taken before the
# 2026-09-20 production deploy. Their formula is preserved verbatim so those signatures keep
# verifying. FROZEN BY CONTRACT: a key is never added to this set — a key registered later is
# omitted from the signature while at its defaults, which is what makes registering it safe.
# Gate: `test_profile_config_contract.t_attested_baseline_is_frozen` pins the membership.
_ATTESTED_BASELINE_KEYS: frozenset = frozenset({
    "profile_type", "session_primer", "tools", "prompts", "useMcpTools",
    "useKnowledgeCollections", "genieConfig", "dualModelConfig", "knowledgeConfig",
    "componentConfig", "postureConfig", "groundingConfig", "guardrailConfig", "honestyConfig",
})


def _project_spec(profile: Any, spec: "ConfigKeySpec", dimension: str,
                  id_to_tag: Optional[Callable[[str], str]]) -> Dict[str, Any]:
    """The canonical projection of ONE signed key — the body `signature_for` had inline."""
    out: Dict[str, Any] = {}
    raw_block = _read_block(profile, spec)
    # A scalar key (`signed_defaults` path `()`) may legitimately be False/0/"", so the
    # block is only defaulted when genuinely absent — `or {}` would erase `False`.
    block = {} if raw_block is None else raw_block
    for p in spec.id_ref_paths:
        found = _get_path(block, p)
        if found is not _MISSING and isinstance(block, dict):
            block = {**block, p[-1]: _map_ids_to_tags(found, id_to_tag)}
    if spec.canonicalize is not None:
        block = spec.canonicalize(block if raw_block is not None else None)
    canon: Dict[str, Any] = {}
    mode_paths = set(spec.mode_paths)
    is_scalar = False
    for path, default in spec.signed_defaults:
        if not path:                       # scalar: the key's own (canonical) value
            is_scalar = True
            value = default if (raw_block is None and spec.canonicalize is None) else block
            out[spec.key] = (sorted(str(v) for v in value)
                             if isinstance(value, list) else value)
            break
        raw = _get_path(block, path)
        value = default if raw is _MISSING else raw
        if path in mode_paths and value is not None and not _modes.is_known(value):
            # Only UNRECOGNISED modes are collapsed — to whatever the runtime would
            # actually use, so a typo cannot manufacture a distinct signature. Every
            # recognised mode is preserved verbatim, which keeps ``review``
            # distinguishable from ``enforce``: they behave identically on content but
            # differ in human-oversight retention, and losing that retention is a real
            # posture change the badge must surface.
            value = _modes.normalize(
                value, context=f"{spec.key}.{'.'.join(path)}")
        if isinstance(value, list):
            value = sorted(str(v) for v in value)
        _set_path(canon, path, value)
    if not is_scalar:
        out[spec.key] = canon
    return out


def posture_signature(profile: Any) -> Dict[str, Any]:
    """The signed **posture** projection — what the agent will and will not say."""
    return signature_for(profile, "posture")


def capability_signature(profile: Any,
                         id_to_tag: Optional[Callable[[str], str]] = None) -> Dict[str, Any]:
    """The signed **capability** projection — what the agent can do.

    Portable by construction: every key travels in the pack manifest, and profile ids are
    mapped into tag space, so a published agent hashes identically after a marketplace
    install. Deployment-bound capability (which LLM, which MCP server's catalog, which
    connectors are opted into) is deliberately NOT here — it cannot reproduce across
    instances and lives in the separate deployment signature.
    """
    return signature_for(profile, "capability", id_to_tag=id_to_tag)


def posture_is_acting(profile: Any) -> Dict[str, bool]:
    """Per signed block: does it alter delivered content in ANY way (disclosure incl.)?

    Retained for the acting-vs-measuring distinction; the SIGNING verdict reads
    ``posture_is_protecting`` instead — disclosure informs but stops nothing, and
    counting it made an annotate-only agent sign "posture enforced" while the Trust
    Circle honestly read "watching" (two instruments, one fact, two answers).
    """
    sig = posture_signature(profile)
    acting: Dict[str, bool] = {}
    for spec in REGISTRY:
        # "Acting" is a property of an enforcement ladder, so only blocks that HAVE one
        # are answer gates. A signed block without a ladder (knowledgeConfig, signed for
        # a single threshold) is not a gate and must not be reported as an idle one.
        # postureConfig is the DEFAULT the gates inherit, not a gate itself.
        if not spec.signed or not spec.mode_paths or spec.key == "postureConfig":
            continue
        block = sig.get(spec.key) or {}
        acting[spec.key] = any(
            _modes.is_acting(_get_path(block, path)) for path in spec.mode_paths)
    return acting


def posture_is_protecting(profile: Any) -> Dict[str, bool]:
    """Per answer gate block: does it actually SUPPRESS harm (enforce/review) once
    agent-level inheritance is resolved?

    Drives the ``agent`` trust validator's clean/partial band. Two deliberate choices:
    (1) ``is_protecting``, not ``is_acting`` — an annotate-only posture watches and
    discloses but withholds nothing, and the seal must not claim more than the ring
    shows; (2) EFFECTIVE modes via ``gate_raw_or_default`` — an agent whose gates all
    inherit an agent-level ``enforce`` protects at runtime, and reporting it
    "observe-only" would be the exact instrument-disagreement this module exists to end.
    Reads the RAW profile (behavioural truth), not the canonicalized signature.
    """
    prof = profile if isinstance(profile, dict) else {}
    # THE resolver — never a local re-derivation: the corpus family inherits the
    # grounding gate's EFFECTIVE mode first (its documented chain), which a naive
    # per-path agent-default fallback gets wrong (an explicit grounding `observe`
    # under an agent `enforce` would leak protection in through the corpus path).
    eff = _modes.effective_posture(prof)
    p = lambda gate: _modes.is_protecting((eff.get(gate) or {}).get("mode"))  # noqa: E731
    return {
        "groundingConfig": p("grounding") or p("corpus"),
        "guardrailConfig": p("outbound") or p("inbound"),
        "honestyConfig": p("honesty"),
    }


# ── Downgrade guard ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Downgrade:
    """One way an incoming manifest would reduce an existing profile's protection."""
    key: str
    path: str
    current: Any
    incoming: Any

    def describe(self) -> str:
        return f"{self.key}.{self.path}: {self.current!r} → {self.incoming!r}"


def _is_number(v: Any) -> bool:
    """True for a real numeric value — booleans excluded (``bool`` is an ``int``)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _list_shrinks(current: Any, incoming: Any) -> bool:
    """True when a restriction list loses entries (e.g. ``denied_tools``)."""
    if not isinstance(current, (list, tuple, set)):
        return False
    if not isinstance(incoming, (list, tuple, set)):
        return bool(current)
    return bool(set(map(str, current)) - set(map(str, incoming)))


def find_downgrades(existing: Any, incoming: Any) -> List[Downgrade]:
    """Every way ``incoming`` would WEAKEN ``existing``'s protection.

    Called before an ``update_in_place`` overwrites a profile. The backend merge is a
    shallow ``dict.update``, so a manifest block replaces the stored one wholesale — without
    this guard a pack shipping ``observe`` would silently switch off a hardened agent's
    enforcement. A key absent from ``incoming`` is never a downgrade (absence preserves).
    """
    found: List[Downgrade] = []
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return found

    for spec in REGISTRY:
        new_block = _read_block(incoming, spec)
        if not new_block:
            continue                      # absent ⇒ preserved, never a downgrade
        old_block = _read_block(existing, spec) or {}

        for path in spec.mode_paths:
            old = _get_path(old_block, path)
            new = _get_path(new_block, path)
            if old is _MISSING:
                continue                  # nothing configured before ⇒ nothing to weaken
            if new is _MISSING:
                # TC-2: the backend merge is a shallow ``dict.update`` — a PRESENT incoming
                # block replaces the stored one wholesale, so a mode dropped from it reverts
                # to the ladder's runtime default (the weakest, non-acting value). If the
                # stored mode was acting (enforce/review/annotate), that is a real downgrade
                # the old ``continue`` let through. Floors already handle absence this way.
                if _modes.is_acting(old):
                    found.append(Downgrade(spec.key, ".".join(path), old, None))
                continue
            if _modes.is_weaker(new, old):
                found.append(Downgrade(spec.key, ".".join(path), old, new))

        for path in spec.flag_paths:
            old = _get_path(old_block, path)
            new = _get_path(new_block, path)
            if old is _MISSING:
                continue
            if new is _MISSING:
                # TC-2: a protective flag dropped from a present block reverts to its (falsy)
                # runtime default — a downgrade if it was set.
                if bool(old):
                    found.append(Downgrade(spec.key, ".".join(path), old, None))
                continue
            if bool(old) and not bool(new):
                found.append(Downgrade(spec.key, ".".join(path), old, new))

        for path in spec.floor_paths:
            old = _get_path(old_block, path)
            new = _get_path(new_block, path)
            if old is _MISSING or not _is_number(old):
                continue                  # no bar configured before ⇒ nothing to relax
            # Dropping a configured bar is a relaxation of the profile's stated posture,
            # so an explicit null counts. The guard errs toward flagging; a deliberate
            # change is still permitted via `allow_posture_downgrade`.
            if new is _MISSING or new is None or (_is_number(new) and float(new) < float(old)):
                found.append(Downgrade(spec.key, ".".join(path), old,
                                       None if new is _MISSING else new))

        for path in spec.ceiling_paths:
            old = _get_path(old_block, path)
            new = _get_path(new_block, path)
            if not _is_number(old) or not _is_number(new):
                continue                  # absent ⇒ inherits the admin threshold: unrankable
            if float(new) > float(old):
                found.append(Downgrade(spec.key, ".".join(path), old, new))

        if spec.key == "authorityConfig":
            found.extend(_authority_downgrades(old_block, new_block))

    return found


def _authority_downgrades(old: Dict, new: Dict) -> List[Downgrade]:
    """Authority is not signed, but it is still guarded: a pack must not silently widen an
    agent's reach on an update. Every field here only ever *restricts*, so "weaker" means
    "restricts less"."""
    out: List[Downgrade] = []
    k = "authorityConfig"

    # TDA_Platform write mode (off < review < allow): letting an agent change more of the
    # platform without the user's approval is a widening.
    _wr = {"off": 0, "review": 1, "allow": 2}

    def _wm(block):
        ps = (block or {}).get("platformSurface") if isinstance(block, dict) else None
        m = str((ps or {}).get("writes") or "off").lower() if isinstance(ps, dict) else "off"
        return m if m in _wr else "off"
    if _wr[_wm(new)] > _wr[_wm(old)]:
        out.append(Downgrade(k, "platformSurface.writes", _wm(old), _wm(new)))

    # The agents it may ask (TDA_Platform `ask`): a name added to the list is a widening.
    def _asks(block):
        ps = (block or {}).get("platformSurface") if isinstance(block, dict) else None
        v = (ps or {}).get("ask") if isinstance(ps, dict) else None
        return {str(x) for x in v} if isinstance(v, list) else set()
    _added = sorted(_asks(new) - _asks(old))
    if _added:
        out.append(Downgrade(k, "platformSurface.ask", sorted(_asks(old)), sorted(_asks(new))))

    # autonomous defaults to True, so only an explicit False→True is a widening.
    if old.get("autonomous") is False and new.get("autonomous", True) is not False:
        out.append(Downgrade(k, "autonomous", False, new.get("autonomous", True)))

    # PROP-011 — the input policy. `interactive: ask → never` and `raisers: enforce → observe`
    # each let the agent go ahead where it would have stopped to ask: a weakening.
    def _ip(block, key, default):
        ip = (block or {}).get("inputPolicy") if isinstance(block, dict) else None
        v = str((ip or {}).get(key) or default).lower() if isinstance(ip, dict) else default
        return v
    _ask = {"ask": 1, "never": 0}
    if _ask.get(_ip(new, "interactive", "ask"), 1) < _ask.get(_ip(old, "interactive", "ask"), 1):
        out.append(Downgrade(k, "inputPolicy.interactive", _ip(old, "interactive", "ask"), _ip(new, "interactive", "ask")))
    _rp = {"enforce": 1, "observe": 0}
    if _rp.get(_ip(new, "raisers", "enforce"), 1) < _rp.get(_ip(old, "raisers", "enforce"), 1):
        out.append(Downgrade(k, "inputPolicy.raisers", _ip(old, "raisers", "enforce"), _ip(new, "raisers", "enforce")))

    if _list_shrinks(old.get("denied_tools"), new.get("denied_tools")):
        out.append(Downgrade(k, "denied_tools",
                             old.get("denied_tools"), new.get("denied_tools")))

    # allowed_connectors: None = every connector allowed, a list = an allow-list. Going
    # from a list to None (or to a superset) widens reach. NB an EMPTY list means "deny
    # all" and is the strictest value — never collapse it to None.
    old_conn, new_conn = old.get("allowed_connectors"), new.get("allowed_connectors")
    if isinstance(old_conn, (list, tuple, set)):
        if not isinstance(new_conn, (list, tuple, set)):
            out.append(Downgrade(k, "allowed_connectors", old_conn, new_conn))
        elif set(map(str, new_conn)) - set(map(str, old_conn)):
            out.append(Downgrade(k, "allowed_connectors", old_conn, new_conn))

    # max_cost_per_run: None = uncapped. 0 is a valid deny-all cap, never "no cap".
    old_cap, new_cap = old.get("max_cost_per_run"), new.get("max_cost_per_run")
    if isinstance(old_cap, (int, float)) and not isinstance(old_cap, bool):
        if not isinstance(new_cap, (int, float)) or isinstance(new_cap, bool):
            out.append(Downgrade(k, "max_cost_per_run", old_cap, new_cap))
        elif new_cap > old_cap:
            out.append(Downgrade(k, "max_cost_per_run", old_cap, new_cap))

    return out
