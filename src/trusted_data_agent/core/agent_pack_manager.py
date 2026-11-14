"""
Agent Pack Manager — orchestrates import, export, create, uninstall, and listing
of agent packs.

Supports both v1.0 (coordinator + experts) and v1.1 (unified profiles array)
manifest formats. v1.0 manifests are normalised to v1.1 internally before
processing so all code paths are unified.

Operates at the internal Python API level, reusing the same ConfigManager,
CollectionDatabase, and ChromaDB functions that the existing REST endpoints use.
No REST-to-REST calls, no duplicated logic.
"""

import io
import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
from trusted_data_agent.core import profile_config_contract as _pcc  # the ONE portable-config registry
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

app_logger = logging.getLogger("quart.app")

# Valid profile types and roles
VALID_PROFILE_TYPES = {"rag_focused", "tool_enabled", "llm_only", "genie"}
VALID_ROLES = {"coordinator", "expert", "standalone"}


class VectorStoreGovernanceError(ValueError):
    """Raised when a pack import/fork would skip knowledge collections because the
    importing user's tier blocks the required vector store backend(s).

    Carries the recovery information the REST layer needs to drive a structured
    409 response: which collections would be lost, which backends the user's tier
    actually allows, and which of the user's existing VS configs match those
    allowed backends. ``needs_vs_configuration`` is True when the user has no
    allowed VS config at all (must configure one before re-trying).
    """

    def __init__(self, message: str, blocked_collections: list, allowed_backends: list, user_allowed_configs: list):
        super().__init__(message)
        self.blocked_collections = blocked_collections
        self.allowed_backends = allowed_backends
        self.user_allowed_configs = user_allowed_configs
        self.needs_vs_configuration = len(user_allowed_configs) == 0


def _build_skill_zip(skill_id: str, manifest: dict, content: str,
                     library_files: dict | None = None) -> bytes:
    """Build an in-memory .skill zip containing skill.json + markdown + SKILL.md
    (+ the progressive library/ subtree when provided — PROP-005).

    Serialization is delegated to the ONE shared serializer
    (`skills/package.py:build_skill_zip`); this site keeps only its POLICY —
    the manifest cleaning and its SKILL.md frontmatter dialect.
    """
    from trusted_data_agent.skills.package import build_skill_zip as _pkg_build

    clean_manifest = {k: v for k, v in manifest.items() if not k.startswith("_")}
    clean_manifest.pop("export_format_version", None)
    clean_manifest.pop("exported_at", None)

    description_safe = clean_manifest.get("description", "").replace("\n", " ")
    skill_md = (
        f"---\nname: {skill_id}\ndescription: {description_safe}\n"
        f"user-invokable: true\n---\n\n{content}"
    )
    return _pkg_build(clean_manifest, f"{skill_id}.md", content,
                      skill_md=skill_md, library_files=library_files)


class PackExportIncomplete(RuntimeError):
    """A member the pack DECLARES could not be exported.

    Raised rather than warned. A pack whose profile references a collection is a pack
    that declares it; shipping without it produces a consumer whose agent is missing a
    signed ingredient — its composition no longer matches what the publisher signed, so
    it reads `unsigned` and takes the pack seal down with it (bottom-up).

    Found live 2026-09-08: a planner repository whose vector store had never been
    created failed `export_collection_to_zip`, the failure was logged as a warning, the
    export continued, and the consumer's Optimize twin arrived with
    `ragCollections: None`. The publisher's own badge stayed green throughout, so
    nothing on either side pointed at the missing member. A refusal here is the only
    place the fault is still attributable.
    """


class SkillOwnershipConflict(ValueError):
    """A pack install refused to overwrite a skill whose DIFFERENT content was
    installed by another user's pack (best-practice step 3). Skills are
    instance-global while packs are per-user; a silent cross-owner clobber is
    the root cause this guard closes. Surfaced as an import warning."""


class BuiltinSkillShadowRefused(SkillOwnershipConflict):
    """A pack install refused to write a skill whose id the PLATFORM ships.

    An installed skill lands in the state directory, which takes precedence over
    `skills/builtin/` — so installing a builtin id silently demotes a platform
    skill to a user skill.  That is self-reinforcing: once it reads as a user
    skill the pack EXPORT auto-bundles it (the `_is_user` gate), and the next
    import re-writes the shadow.  Refused loudly instead; the pack's profiles
    keep working because skills are referenced by ID and the builtin resolves.

    A subclass of SkillOwnershipConflict so the import's existing refusal path
    surfaces it as a warning rather than swallowing it as a generic failure."""



def _stamp_manifest_servers(entry: dict, profile: dict, user_uuid: str) -> None:
    """Stamp a multi-server agent's full server list (``mcpServers``, by NAME) onto its
    manifest entry. Absent for a single-server agent, so its manifest is unchanged.
    Best-effort: a name lookup failure never fails an export."""
    try:
        from trusted_data_agent.core.agent_mcp_servers import (
            MANIFEST_SERVERS_KEY, manifest_servers)
        from trusted_data_agent.core.config_manager import get_config_manager
        names = {s.get("id"): s.get("name")
                 for s in get_config_manager().get_mcp_servers(user_uuid) if s.get("id")}
        listed = manifest_servers(profile, names)
        if listed:
            entry[MANIFEST_SERVERS_KEY] = listed
    except Exception as exc:
        app_logger.warning(f"Agent pack: could not stamp the MCP server list of "
                           f"@{profile.get('tag')}: {exc}")

class AgentPackManager:
    """Manages agent pack install, export, create, uninstall, and listing."""

    def __init__(self, db_path: str = "tda_auth.db"):
        self.db_path = db_path

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate_manifest(self, manifest: dict) -> list[str]:
        """Validate manifest schema. Returns list of error strings (empty = valid)."""
        version = manifest.get("format_version")
        if version == "1.0":
            return self._validate_manifest_v10(manifest)
        elif version in ("1.1", "1.2", "1.3", "1.4"):
            return self._validate_manifest_v11(manifest)
        else:
            return [f"Unsupported format_version: {version}"]

    def _validate_manifest_v10(self, manifest: dict) -> list[str]:
        """Validate v1.0 manifest (coordinator + experts + collections)."""
        errors = []
        required = {"format_version", "name", "coordinator", "experts", "collections"}
        missing = required - set(manifest.keys())
        if missing:
            errors.append(f"Missing required fields: {', '.join(sorted(missing))}")
            return errors

        # Validate coordinator
        coord = manifest["coordinator"]
        if not coord.get("tag"):
            errors.append("Coordinator must have a 'tag'")
        if coord.get("profile_type") != "genie":
            errors.append("Coordinator must be profile_type 'genie'")

        # Validate experts
        experts = manifest["experts"]
        if not isinstance(experts, list) or len(experts) == 0:
            errors.append("'experts' must be a non-empty list")
        else:
            seen_tags = set()
            for i, expert in enumerate(experts):
                tag = expert.get("tag")
                if not tag:
                    errors.append(f"Expert {i} missing 'tag'")
                elif tag in seen_tags:
                    errors.append(f"Duplicate expert tag: {tag}")
                else:
                    seen_tags.add(tag)

                profile_type = expert.get("profile_type")
                if profile_type not in ("rag_focused", "tool_enabled", "llm_only"):
                    errors.append(f"Expert {i} (@{tag}): invalid profile_type '{profile_type}'")

                collection_ref = expert.get("collection_ref")
                if collection_ref:
                    collection_refs = {c["ref"] for c in manifest.get("collections", [])}
                    if collection_ref not in collection_refs:
                        errors.append(f"Expert {i} (@{tag}): collection_ref '{collection_ref}' not found in collections")

        # Validate collections
        collections = manifest["collections"]
        if not isinstance(collections, list):
            errors.append("'collections' must be a list")
        else:
            seen_refs = set()
            for i, coll in enumerate(collections):
                ref = coll.get("ref")
                if not ref:
                    errors.append(f"Collection {i} missing 'ref'")
                elif ref in seen_refs:
                    errors.append(f"Duplicate collection ref: {ref}")
                else:
                    seen_refs.add(ref)

                if not coll.get("file"):
                    errors.append(f"Collection {i} ({ref}): missing 'file'")

                repo_type = coll.get("repository_type")
                if repo_type not in ("knowledge", "planner"):
                    errors.append(f"Collection {i} ({ref}): invalid repository_type '{repo_type}'")

        return errors

    def _validate_manifest_v11(self, manifest: dict) -> list[str]:
        """Validate v1.1 manifest (unified profiles array)."""
        errors = []
        required = {"format_version", "name", "profiles"}
        missing = required - set(manifest.keys())
        if missing:
            errors.append(f"Missing required fields: {', '.join(sorted(missing))}")
            return errors

        profiles = manifest.get("profiles")
        if not isinstance(profiles, list) or len(profiles) == 0:
            errors.append("'profiles' must be a non-empty list")
            return errors

        seen_tags = set()
        all_tags = {p.get("tag") for p in profiles if p.get("tag")}

        for i, prof in enumerate(profiles):
            tag = prof.get("tag")
            if not tag:
                errors.append(f"Profile {i} missing 'tag'")
            elif tag in seen_tags:
                errors.append(f"Duplicate profile tag: {tag}")
            else:
                seen_tags.add(tag)

            profile_type = prof.get("profile_type")
            if profile_type not in VALID_PROFILE_TYPES:
                errors.append(f"Profile {i} (@{tag}): invalid profile_type '{profile_type}'")

            role = prof.get("role")
            if role not in VALID_ROLES:
                errors.append(f"Profile {i} (@{tag}): invalid role '{role}'")

            # Genie profiles must have child_tags
            if profile_type == "genie":
                child_tags = prof.get("child_tags", [])
                if not isinstance(child_tags, list) or len(child_tags) == 0:
                    errors.append(f"Profile {i} (@{tag}): genie profiles must have non-empty 'child_tags'")
                else:
                    for ct in child_tags:
                        if ct not in all_tags:
                            errors.append(f"Profile {i} (@{tag}): child_tag '{ct}' not found in profiles")

            # Validate collection_refs reference existing collections
            collection_refs = prof.get("collection_refs", [])
            if collection_refs:
                manifest_coll_refs = {c.get("ref") for c in manifest.get("collections", [])}
                for cr in collection_refs:
                    if cr not in manifest_coll_refs:
                        errors.append(f"Profile {i} (@{tag}): collection_ref '{cr}' not found in collections")

        # Validate collections (can be empty for llm_only-only packs)
        collections = manifest.get("collections", [])
        if not isinstance(collections, list):
            errors.append("'collections' must be a list")
        else:
            seen_refs = set()
            for i, coll in enumerate(collections):
                ref = coll.get("ref")
                if not ref:
                    errors.append(f"Collection {i} missing 'ref'")
                elif ref in seen_refs:
                    errors.append(f"Duplicate collection ref: {ref}")
                else:
                    seen_refs.add(ref)

                if not coll.get("file"):
                    errors.append(f"Collection {i} ({ref}): missing 'file'")

                repo_type = coll.get("repository_type")
                if repo_type not in ("knowledge", "planner"):
                    errors.append(f"Collection {i} ({ref}): invalid repository_type '{repo_type}'")

        return errors

    # ── v1.0 → v1.1 Normaliser ────────────────────────────────────────────────

    def _normalize_v10_to_v11(self, manifest: dict) -> dict:
        """Convert a v1.0 manifest to v1.1 format for unified processing."""
        profiles = []

        # Convert experts
        for expert in manifest.get("experts", []):
            entry = {
                "tag": expert["tag"],
                "name": expert.get("name"),
                "description": expert.get("description"),
                "profile_type": expert["profile_type"],
                "classification_mode": expert.get("classification_mode", "light"),
                "role": "expert",
                "collection_refs": [expert["collection_ref"]] if expert.get("collection_ref") else [],
            }
            if expert.get("requires_mcp"):
                entry["requires_mcp"] = True
            if expert.get("synthesisPromptOverride"):
                entry["synthesisPromptOverride"] = expert["synthesisPromptOverride"]
            # MCP tool-calling fields (llm_only + useMcpTools experts, e.g. an
            # external-search expert) — previously dropped here, which silently
            # degraded such experts to pure-LLM profiles on import.
            if expert.get("useMcpTools"):
                entry["useMcpTools"] = True
            if expert.get("mcpServerName"):
                entry["mcpServerName"] = expert["mcpServerName"]
            if expert.get("allowed_tools") or expert.get("tools"):
                entry["allowed_tools"] = expert.get("allowed_tools") or expert.get("tools")
            # Portable profile config via the shared contract. This site historically had
            # its own hand-rolled subset and silently dropped skillsConfig/componentConfig,
            # making a v1.0 pack's experts inherit the platform's permissive component
            # defaults on import (6 companion skills auto-inject). Driving it from the one
            # registry means a new key can never be forgotten here again.
            _pcc.apply_from_manifest(expert, entry)
            profiles.append(entry)

        # Convert coordinator
        coord = manifest["coordinator"]
        coord_entry = {
            "tag": coord["tag"],
            "name": coord.get("name"),
            "description": coord.get("description"),
            "profile_type": "genie",
            "role": "coordinator",
            "classification_mode": coord.get("classification_mode", "light"),
            "child_tags": [e["tag"] for e in manifest.get("experts", [])],
            "genieConfig": coord.get("genieConfig", {}),
        }
        _pcc.apply_from_manifest(coord, coord_entry)
        profiles.append(coord_entry)

        result = {**manifest, "format_version": "1.1", "profiles": profiles}
        # Remove old keys
        result.pop("coordinator", None)
        result.pop("experts", None)
        return result

    # ── Import ─────────────────────────────────────────────────────────────────

    async def import_pack(
        self,
        zip_path: Path,
        user_uuid: str,
        mcp_server_id: str | None = None,
        llm_configuration_id: str | None = None,
        conflict_strategy: str | None = None,
        vector_store_config_id: str | None = None,
        force_drop_tags: bool = False,
        dry_run: bool = False,
        allow_posture_downgrade: bool = False,
        mcp_bindings: dict | None = None,
    ) -> dict:
        """Import an agent pack: validate → import collections → create profiles → record.

        ``mcp_bindings`` is an optional explicit per-agent MCP map
        ``{profile_tag: mcp_server_id}``; an explicit binding wins over the
        manifest's ``mcpServerName`` auto-match and over the pack-level
        ``mcp_server_id`` (see ``_build_profile``). A binding to a server the
        user does not own is refused (ValueError) rather than silently dropped.

        Supports both v1.0 and v1.1 manifest formats (v1.0 is normalised to v1.1).

        Args:
            conflict_strategy: How to handle tag conflicts.
                - None: raise ValueError (default, backward compatible)
                - "replace": delete existing profiles with conflicting tags
                - "expand": auto-rename conflicting tags (e.g. TAG → TAG2)
                - "update_in_place": diff & patch existing pack — preserves
                  installation_id, marketplace listing id, profile ids, and
                  collection ids so subscribers stay valid. Refuses to drop
                  manifest-missing tags unless force_drop_tags=True.
            force_drop_tags: Only meaningful for update_in_place. When True, allow
                the new manifest to omit tags that the old pack had (those profiles
                are removed and subscribers' affected sessions archived).
            dry_run: Only meaningful for update_in_place. When True, validate and
                compute the diff plan but commit nothing; returns
                ``{"status": "dry_run", "would_preserve": {...}, "would_change": {...}}``.
            allow_posture_downgrade: Only meaningful for update_in_place. When True, accept
                a manifest that would REDUCE an existing profile's trust posture (a weaker
                grounding/guardrail/honesty mode, a disabled detector, or a widened agent
                authority). Refused by default so a republish cannot silently switch off a
                hardened agent's enforcement.

        Returns:
            {
                "installation_id": int,
                "name": str,
                "coordinator_tag": str | None,
                "coordinator_profile_id": str | None,
                "profiles_created": int,
                "collections_created": int,
                "tag_remap": dict | None,
            }

        Raises:
            ValueError: Validation errors (bad manifest, tag conflicts, missing MCP server).
            RuntimeError: Import failures.
        """
        import shutil
        from trusted_data_agent.core.collection_utils import import_collection_from_zip
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.agent.rag_retriever import get_rag_retriever

        config_manager = get_config_manager()

        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        try:
            # Step 1: Extract .agentpack ZIP
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(temp_path)

            # Read manifest
            manifest_path = temp_path / "manifest.json"
            if not manifest_path.exists():
                raise ValueError("Invalid agent pack: manifest.json missing")

            with open(manifest_path, 'r') as f:
                manifest = json.load(f)

            # Validate manifest (handles both v1.0 and v1.1)
            errors = self.validate_manifest(manifest)
            if errors:
                raise ValueError(f"Invalid manifest: {'; '.join(errors)}")

            # Normalise v1.0 → v1.1 for unified processing
            if manifest.get("format_version") == "1.0":
                manifest = self._normalize_v10_to_v11(manifest)

            profiles = manifest["profiles"]
            collections = manifest.get("collections", [])

            # Explicit per-agent MCP bindings (keyed by manifest tag). Validated
            # against the importing user's OWN servers — refuse, never silently unbind.
            mcp_tag_bindings, _rejected = self._normalize_mcp_bindings(user_uuid, mcp_bindings)
            if _rejected:
                raise ValueError(
                    "mcp_bindings names MCP server(s) that are not configured for this user "
                    f"(@{', @'.join(_rejected)})."
                )

            # Step 2: Check for tag conflicts and resolve based on strategy
            existing_profiles = config_manager.get_profiles(user_uuid)
            existing_tags = {p.get("tag") for p in existing_profiles if p.get("tag")}

            conflicting_tags = [
                prof["tag"] for prof in profiles
                if prof["tag"] in existing_tags
            ]

            tag_remap = {}

            # Track if we need to restore default profile after replace
            replaced_default_tag = None

            # Short-circuit: update_in_place runs its own end-to-end pipeline
            # (validate → diff & patch → reconcile) and returns without touching
            # the rest of import_pack. It REQUIRES at least one matching profile
            # tag — fresh installs must use a different strategy.
            if conflict_strategy == "update_in_place":
                # A tag the LEDGER owns counts as installed even when the live profile
                # is gone. Gating purely on user_preferences made the one repair path
                # for a half-lost pack unreachable: the pack row and its resources
                # survive, the profiles do not, and the strategy built to preserve
                # those very IDs refuses to run. See AgentPackDB.get_installations_by_profile_tags.
                from trusted_data_agent.core.agent_pack_db import AgentPackDB as _APD
                _ledger_tags = _APD(self.db_path).get_installations_by_profile_tags(
                    [p["tag"] for p in profiles], user_uuid
                )
                for _t in _ledger_tags:
                    if _t not in conflicting_tags:
                        conflicting_tags.append(_t)
                        app_logger.warning(
                            f"update_in_place: @{_t} is owned by pack installation "
                            f"{_ledger_tags[_t]['installation_id']} but has no live profile — "
                            f"reconciling from the ledger (id={_ledger_tags[_t]['profile_id']})."
                        )
                if not conflicting_tags:
                    raise ValueError(
                        "update_in_place: no existing profiles match any manifest "
                        "tags. This strategy is only valid for re-importing an "
                        "already-installed pack. Use a different strategy for "
                        "fresh installs."
                    )
                return await self._handle_update_in_place(
                    mcp_tag_bindings=mcp_tag_bindings,
                    manifest=manifest,
                    temp_path=temp_path,
                    existing_profiles=existing_profiles,
                    conflicting_tags=conflicting_tags,
                    config_manager=config_manager,
                    user_uuid=user_uuid,
                    mcp_server_id=mcp_server_id,
                    llm_configuration_id=llm_configuration_id,
                    vector_store_config_id=vector_store_config_id,
                    force_drop_tags=force_drop_tags,
                    dry_run=dry_run,
                    allow_posture_downgrade=allow_posture_downgrade,
                )

            if conflicting_tags:
                if conflict_strategy == "replace":
                    # Delete existing profiles with conflicting tags AND their
                    # orphaned collections (cascade cleanup).
                    from trusted_data_agent.agent.rag_retriever import get_rag_retriever
                    from trusted_data_agent.core.agent_pack_db import AgentPackDB

                    retriever = get_rag_retriever()
                    pack_db = AgentPackDB(self.db_path)

                    # Check if any profile being replaced is the default
                    default_profile_id = config_manager.get_default_profile_id(user_uuid)

                    # Collect all collection IDs referenced by profiles being replaced
                    orphan_collection_ids = set()
                    replaced_profile_ids = []

                    for existing_prof in existing_profiles:
                        if existing_prof.get("tag") not in conflicting_tags:
                            continue

                        replaced_profile_ids.append(existing_prof["id"])

                        # Track if this is the default profile (by tag, for later restoration)
                        if existing_prof["id"] == default_profile_id:
                            replaced_default_tag = existing_prof.get("tag")
                            app_logger.info(
                                f"  Default profile @{replaced_default_tag} will be replaced — "
                                f"will restore default after new profile is created"
                            )

                        # Planner collections (ragCollections: list of int IDs)
                        for cid in existing_prof.get("ragCollections", []):
                            if cid != '*':
                                orphan_collection_ids.add(int(cid))

                        # Knowledge collections (knowledgeConfig.collections: list of {id, name})
                        for kc in existing_prof.get("knowledgeConfig", {}).get("collections", []):
                            if kc.get("id"):
                                orphan_collection_ids.add(int(kc["id"]))

                    # Remove the profiles
                    for existing_prof in existing_profiles:
                        if existing_prof.get("tag") in conflicting_tags:
                            config_manager.remove_profile(existing_prof["id"], user_uuid)
                            app_logger.info(
                                f"  Replaced existing profile @{existing_prof['tag']} "
                                f"(id={existing_prof['id']})"
                            )

                    # Delete orphaned pack installation records whose profiles
                    # were all just replaced (prevents name dedup creating "(2)", "(3)").
                    # Must run BEFORE the orphan collection check so that
                    # is_pack_managed() returns False for collections that were only
                    # held by these now-replaced packs (not by any other pack).
                    old_pack_ids = set()
                    for pid in replaced_profile_ids:
                        for pack_info in pack_db.get_packs_for_resource("profile", str(pid)):
                            old_pack_ids.add(pack_info["id"])
                    for old_id in old_pack_ids:
                        remaining_resources = pack_db.get_resources_for_pack(old_id)
                        profile_resources = [
                            r for r in remaining_resources
                            if r["resource_type"] == "profile"
                        ]
                        # Only delete if all profiles in this pack were replaced
                        all_replaced = all(
                            r["resource_id"] in [str(p) for p in replaced_profile_ids]
                            for r in profile_resources
                        )
                        if all_replaced:
                            pack_db.remove_pack_resources(old_id)
                            conn_tmp = sqlite_util.connect(self.db_path)
                            try:
                                conn_tmp.execute(
                                    "DELETE FROM marketplace_agent_packs WHERE source_installation_id = ?",
                                    (old_id,),
                                )
                                conn_tmp.execute(
                                    "DELETE FROM agent_pack_installations WHERE id = ?",
                                    (old_id,),
                                )
                                conn_tmp.commit()
                                app_logger.info(
                                    f"  Deleted old pack installation id={old_id} (marketplace listing auto-removed)"
                                )
                            finally:
                                conn_tmp.close()

                    # After profile removal and pack record cleanup, delete collections
                    # that are now truly orphaned (not referenced by any profile or other pack).
                    if orphan_collection_ids:
                        remaining_profiles = config_manager.get_profiles(user_uuid)
                        still_used_ids = set()
                        for rp in remaining_profiles:
                            for cid in rp.get("ragCollections", []):
                                if cid != '*':
                                    still_used_ids.add(int(cid))
                            for kc in rp.get("knowledgeConfig", {}).get("collections", []):
                                if kc.get("id"):
                                    still_used_ids.add(int(kc["id"]))

                        for coll_id in orphan_collection_ids:
                            if coll_id in still_used_ids:
                                app_logger.info(f"  Kept collection id={coll_id} (still referenced by another profile)")
                                continue
                            # Check if referenced by a pack other than the ones just replaced
                            if pack_db.is_pack_managed("collection", str(coll_id)):
                                app_logger.info(f"  Kept collection id={coll_id} (still referenced by another pack)")
                                continue
                            if retriever:
                                try:
                                    success = await retriever.remove_collection(coll_id, user_id=user_uuid)
                                    if success:
                                        app_logger.info(f"  Deleted orphaned collection id={coll_id}")
                                    else:
                                        app_logger.warning(f"  Collection id={coll_id} not found or already deleted")
                                except Exception as e:
                                    app_logger.warning(f"  Failed to delete collection {coll_id}: {e}")

                    # Refresh existing_tags after deletions
                    existing_profiles = config_manager.get_profiles(user_uuid)
                    existing_tags = {p.get("tag") for p in existing_profiles if p.get("tag")}

                elif conflict_strategy == "expand":
                    tag_remap = self._compute_tag_expansion(
                        conflicting_tags, existing_tags, profiles
                    )
                    # the KG entries reference profiles BY TAG too — pass them, or a
                    # second fork's graphs are all skipped (see _apply_tag_remap)
                    self._apply_tag_remap(
                        profiles, tag_remap, manifest.get("knowledge_graphs") or [])
                    # The consumer's explicit per-agent bindings were keyed on the tags
                    # the pack SHIPPED; the profiles now carry the expanded tags — follow.
                    mcp_tag_bindings = self._remap_bindings(mcp_tag_bindings, tag_remap)
                    app_logger.info(f"  Tag expansion remap: {tag_remap}")

                else:
                    # Default: raise error (backward compatible)
                    raise ValueError(
                        f"Tag conflict: tags {', '.join('@' + t for t in conflicting_tags)} "
                        f"are already in use"
                    )

            # Step 2b: Deduplicate pack name
            pack_name = manifest["name"]
            existing_pack_names = self._get_existing_pack_names(user_uuid)
            if pack_name in existing_pack_names:
                counter = 2
                while f"{pack_name} ({counter})" in existing_pack_names:
                    counter += 1
                manifest["name"] = f"{pack_name} ({counter})"
                app_logger.info(
                    f"  Pack name '{pack_name}' already exists, "
                    f"renamed to '{manifest['name']}'"
                )

            # Step 3: Check if MCP server is needed. A profile-level mcpServerName
            # that resolves against the importing user's configured servers (by
            # name, case-insensitive) satisfies the requirement — only refuse if
            # some requires_mcp profile has NO resolvable binding at all.
            needs_mcp = any(p.get("requires_mcp") for p in profiles)
            if needs_mcp and not mcp_server_id:
                _name_map = self._mcp_name_map(user_uuid)
                _unresolved = [
                    p.get("tag") for p in profiles
                    if p.get("requires_mcp")
                    and not self._tag_binding(mcp_tag_bindings, p.get("tag"))
                    and (p.get("mcpServerName") or "").strip().lower() not in _name_map
                ]
                if _unresolved:
                    raise ValueError(
                        "This agent pack contains profiles that require an MCP server "
                        f"(@{', @'.join(_unresolved)}). Please provide mcp_server_id, "
                        "or configure MCP server(s) matching the pack's referenced name(s)."
                    )

            # Step 4: Resolve LLM configuration.
            #
            # Each candidate is checked against the user's OWN configurations before it
            # is accepted.  A pointer that is merely non-empty is not a usable answer:
            # the bootstrap template shipped an `active_llm_configuration_id` that named
            # no configuration in its own list, so every brand-new user inherited a
            # dangling pointer, the old code took it on faith because it was truthy, and
            # the fallback that exists for exactly this case was never reached.  Result:
            # no agent pack could be installed by any new account, with an error blaming
            # a deletion that never happened.
            all_configs = config_manager.get_llm_configurations(user_uuid) or []
            valid_ids = {c.get("id") for c in all_configs}

            explicit = llm_configuration_id
            if explicit and explicit not in valid_ids:
                # An id the CALLER chose is different: honour their intent by failing
                # loudly rather than silently substituting a model they did not ask for.
                raise ValueError(
                    f"LLM configuration '{explicit}' not found. It may have been deleted."
                )

            llm_config_id = explicit
            if not llm_config_id:
                candidate = config_manager.get_active_llm_configuration_id(user_uuid)
                if candidate in valid_ids:
                    llm_config_id = candidate
            if not llm_config_id and all_configs:
                llm_config_id = all_configs[0].get("id")

            if not llm_config_id:
                raise ValueError("No LLM configuration available. Please add one in Uderia first.")

            # Step 4b: Import vector store configurations (v1.2+ packs)
            vs_config_map = {}  # ref → local config ID
            import_warnings = []  # governance warnings (non-blocking)

            # Resolve importing user's tier for VS governance checks
            try:
                from trusted_data_agent.vectorstore.settings import get_allowed_backends
                from trusted_data_agent.auth.admin import get_user_tier
                from trusted_data_agent.auth.middleware import get_current_user
                _importing_user = get_current_user()
                _importing_tier = get_user_tier(_importing_user) if _importing_user else "user"
                _allowed_backends = get_allowed_backends(_importing_tier)
            except Exception:
                _allowed_backends = ["chromadb", "teradata", "qdrant"]

            if vector_store_config_id:
                # Override: map ALL pack VS refs to the user-selected config
                # Validate the selected config's backend is allowed for this user's tier
                override_config = next(
                    (c for c in config_manager.get_vector_store_configurations(user_uuid)
                     if c.get("id") == vector_store_config_id), None
                )
                if override_config and override_config.get("backend_type") not in _allowed_backends:
                    raise ValueError(
                        f"Vector store backend '{override_config.get('backend_type')}' "
                        f"is restricted for your tier. Choose a different backend."
                    )
                pack_vs_configs = manifest.get("vector_store_configurations", [])
                for vs_entry in pack_vs_configs:
                    vs_config_map[vs_entry["ref"]] = vector_store_config_id
                app_logger.info(f"Vector store override: all collections → config '{vector_store_config_id}'")
            else:
                # No override: match/create configs from pack manifest (original behavior)
                pack_vs_configs = manifest.get("vector_store_configurations", [])
                if pack_vs_configs:
                    app_logger.info(f"Importing {len(pack_vs_configs)} vector store configurations...")
                    existing_vs_configs = config_manager.get_vector_store_configurations(user_uuid)
                    for vs_entry in pack_vs_configs:
                        match = next((c for c in existing_vs_configs
                                      if c.get("backend_type") == vs_entry.get("backend_type")
                                      and c.get("name") == vs_entry.get("name")), None)
                        if match:
                            vs_config_map[vs_entry["ref"]] = match["id"]
                            app_logger.info(f"  Matched existing vector store config '{match['name']}'")
                        else:
                            import random, string
                            suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                            new_vs_config = {
                                "id": f"vs-{int(datetime.now(timezone.utc).timestamp() * 1000)}-{suffix}",
                                "name": vs_entry.get("name", "Imported Vector Store"),
                                "backend_type": vs_entry.get("backend_type", "chromadb"),
                                "backend_config": vs_entry.get("backend_config", {}),
                                "credentials": {},
                                "created_at": datetime.now(timezone.utc).isoformat()
                            }
                            config_manager.add_vector_store_configuration(new_vs_config, user_uuid)
                            vs_config_map[vs_entry["ref"]] = new_vs_config["id"]
                            app_logger.info(f"  Created vector store config '{new_vs_config['name']}'")

                        # Check governance: block import of restricted backend configs
                        _backend = vs_entry.get("backend_type", "chromadb")
                        if _backend not in _allowed_backends:
                            # Remove from vs_config_map so collections using this ref are skipped
                            vs_config_map.pop(vs_entry["ref"], None)
                            import_warnings.append(
                                f"Vector store backend '{_backend}' (config: {vs_entry.get('name', 'unknown')}) "
                                f"is restricted for your tier. Associated knowledge collections were skipped."
                            )
                            app_logger.warning(
                                f"  VS governance block: backend '{_backend}' restricted for tier '{_importing_tier}', "
                                f"ref '{vs_entry['ref']}' removed from import"
                            )

            # Step 4c: Governance preflight — fail fast if knowledge collections
            # would be silently dropped due to tier-blocked vector store backends.
            # Bypassed when the caller explicitly chose a vs_config override
            # (that override path validated its own backend at line ~576).
            if not vector_store_config_id:
                blocked = []
                for coll in collections:
                    if coll.get("repository_type") != "knowledge":
                        continue
                    vs_ref = coll.get("vector_store_config_ref")
                    if vs_ref and vs_ref not in vs_config_map:
                        blocked.append({
                            "ref": coll["ref"],
                            "name": coll.get("name", coll["ref"]),
                            "backend_type": coll.get("backend_type"),
                        })
                if blocked:
                    user_configs = config_manager.get_vector_store_configurations(user_uuid)
                    user_allowed_configs = [
                        {"id": c["id"], "name": c.get("name", ""), "backend_type": c.get("backend_type", "")}
                        for c in user_configs
                        if c.get("backend_type") in _allowed_backends
                    ]
                    blocked_backends = sorted({b["backend_type"] for b in blocked if b.get("backend_type")})
                    msg = (
                        f"{len(blocked)} knowledge collection(s) in this pack use vector store "
                        f"backend(s) restricted for your tier ({', '.join(blocked_backends) or 'unknown'}). "
                        + (
                            "Choose an allowed backend to use instead."
                            if user_allowed_configs
                            else "You have no vector store backend configured that your tier permits — "
                                 "configure one under Anatomy → Vector Stores, then try again."
                        )
                    )
                    raise VectorStoreGovernanceError(
                        msg,
                        blocked_collections=blocked,
                        allowed_backends=list(_allowed_backends),
                        user_allowed_configs=user_allowed_configs,
                    )

            # Step 5: Import collections
            app_logger.info(f"Importing {len(collections)} collections...")
            ref_to_collection_id = {}

            for coll_entry in collections:
                ref = coll_entry["ref"]
                coll_file = coll_entry["file"]
                coll_zip_path = temp_path / coll_file

                if not coll_zip_path.exists():
                    raise ValueError(f"Collection ZIP not found in pack: {coll_file}")

                is_planner = coll_entry.get("repository_type") == "planner"
                coll_mcp = self._planner_mcp_for_ref(
                    ref, manifest, self._mcp_name_map(user_uuid),
                    mcp_tag_bindings, mcp_server_id,
                ) if is_planner else None

                # Resolve VS config for this collection (override or mapped)
                coll_vs_config_id = None
                if not is_planner:
                    if vector_store_config_id:
                        coll_vs_config_id = vector_store_config_id
                    else:
                        vs_ref = coll_entry.get("vector_store_config_ref")
                        if vs_ref and vs_ref in vs_config_map:
                            coll_vs_config_id = vs_config_map[vs_ref]
                        elif vs_ref and vs_ref not in vs_config_map:
                            # Defensive guard: the preflight at Step 4c should have raised
                            # VectorStoreGovernanceError before reaching this loop. If we land
                            # here it means a logic gap was introduced — fail loudly rather
                            # than silently dropping a knowledge collection.
                            raise RuntimeError(
                                f"Internal error: knowledge collection '{coll_entry.get('name', ref)}' "
                                f"reached import with blocked VS ref '{vs_ref}' — preflight was bypassed."
                            )

                result = await import_collection_from_zip(
                    zip_path=coll_zip_path,
                    user_uuid=user_uuid,
                    display_name=coll_entry.get("name"),
                    mcp_server_id=coll_mcp,
                    skip_reload=True,
                    populate_knowledge_docs=True,
                    vector_store_config_id=coll_vs_config_id,
                )

                new_coll_id = result["collection_id"]
                if is_planner:
                    # Stamp on CREATE too: the update path can only remove what the pack
                    # owns, so an unstamped install leaves cases it can never clean.
                    _n = await self._stamp_planner_ownership(
                        new_coll_id,
                        {"collection_name": result.get("collection_name")},
                        self._pack_ref(manifest))
                    app_logger.info(
                        f"  Planner '{ref}' -> id={new_coll_id}: stamped {_n} case(s) "
                        f"as pack-owned; keyed to {coll_mcp or 'NO SERVER'}")
                    self._apply_planner_lock(new_coll_id, coll_entry)

                # Link collection to imported vector store config (skip if already set via override)
                vs_ref = coll_entry.get("vector_store_config_ref")
                if not coll_vs_config_id and vs_ref and vs_ref in vs_config_map:
                    try:
                        from trusted_data_agent.core.collection_db import get_collection_db
                        coll_db = get_collection_db()
                        coll_db.update_collection(new_coll_id, {"vector_store_config_id": vs_config_map[vs_ref]})
                    except Exception as e:
                        app_logger.warning(f"Could not link collection {new_coll_id} to vector store config: {e}")

                ref_to_collection_id[ref] = {
                    "id": new_coll_id,
                    "name": result["collection_name"],
                }
                app_logger.info(f"  Imported collection '{ref}' -> id={new_coll_id} ({result['document_count']} docs)")

            # Step 5b: Install bundled skills (v1.3+ packs)
            _installed_skill_ids = self._install_pack_skills(
                manifest, temp_path, owner_user_uuid=user_uuid,
                warnings_sink=import_warnings)

            # Step 6: Create profiles — non-genie first, then genie (so child IDs are available)
            app_logger.info(f"Creating {len(profiles)} profiles...")

            non_genie = [p for p in profiles if p["profile_type"] != "genie"]
            genie_profiles = [p for p in profiles if p["profile_type"] == "genie"]

            tag_to_profile_id = {}
            created_profile_ids = []
            mcp_name_to_id = self._mcp_name_map(user_uuid)

            for prof in non_genie:
                profile_data = self._build_profile(
                    prof, ref_to_collection_id, llm_config_id, mcp_server_id,
                    tag_to_profile_id=tag_to_profile_id,
                    mcp_name_to_id=mcp_name_to_id, warnings=import_warnings,
                    mcp_tag_bindings=mcp_tag_bindings,
                )
                success = config_manager.add_profile(profile_data, user_uuid)
                if not success:
                    raise RuntimeError(f"Failed to create profile @{prof['tag']}")

                tag_to_profile_id[prof["tag"]] = profile_data["id"]
                created_profile_ids.append(profile_data["id"])
                app_logger.info(f"  Created @{prof['tag']} (id={profile_data['id']})")

            for prof in genie_profiles:
                profile_data = self._build_profile(
                    prof, ref_to_collection_id, llm_config_id, mcp_server_id,
                    tag_to_profile_id=tag_to_profile_id,
                    mcp_name_to_id=mcp_name_to_id, warnings=import_warnings,
                    mcp_tag_bindings=mcp_tag_bindings,
                )
                success = config_manager.add_profile(profile_data, user_uuid)
                if not success:
                    raise RuntimeError(f"Failed to create profile @{prof['tag']}")

                tag_to_profile_id[prof["tag"]] = profile_data["id"]
                created_profile_ids.append(profile_data["id"])
                app_logger.info(f"  Created coordinator @{prof['tag']} (id={profile_data['id']})")

            # Step 6b: Restore default profile if it was replaced
            if replaced_default_tag and replaced_default_tag in tag_to_profile_id:
                new_default_id = tag_to_profile_id[replaced_default_tag]
                config_manager.set_default_profile_id(new_default_id, user_uuid)
                app_logger.info(
                    f"  Restored default profile: @{replaced_default_tag} (new id={new_default_id})"
                )

            # Step 6c: Import Knowledge Graphs (needs profile IDs from Step 6)
            pack_kgs = manifest.get("knowledge_graphs", [])
            _kgs_imported = 0
            _imported_kg_ids: list[str] = []
            _imported_kg_names: list[str] = []
            if pack_kgs:
                import uuid as _uuid_kg
                import sqlite3 as _sq_kg
                from components.builtin.knowledge_graph.graph_store import GraphStore as _GS_import
                app_logger.info(f"Importing {len(pack_kgs)} knowledge graph(s)...")

                for _kg_entry in pack_kgs:
                    _kg_profile_tag = _kg_entry.get("profile_tag")
                    _kg_target_profile_id = tag_to_profile_id.get(_kg_profile_tag)
                    if not _kg_target_profile_id:
                        app_logger.warning(
                            f"  KG '{_kg_entry.get('name')}': profile @{_kg_profile_tag} not found, skipping"
                        )
                        continue

                    _kg_file_path = temp_path / _kg_entry["file"]
                    if not _kg_file_path.exists():
                        app_logger.warning(f"  KG file not found in pack: {_kg_entry['file']}")
                        continue

                    _kg_data = json.loads(_kg_file_path.read_text())
                    _new_kg_id = str(_uuid_kg.uuid4())

                    # Determine the KG owner on this system.
                    # Preference order:
                    #   1. owner_profile_tag from manifest (when original owner is in this pack)
                    #   2. Genie coordinator of this pack (neutral owner for cross-pack KGs)
                    #   3. Active-assignment profile (fallback, current behaviour for single profiles)
                    _owner_profile_tag = _kg_entry.get("owner_profile_tag")
                    _actual_owner_pid = tag_to_profile_id.get(_owner_profile_tag) if _owner_profile_tag else None
                    if not _actual_owner_pid:
                        _coord_tag = next(
                            (p["tag"] for p in genie_profiles if p.get("role") == "coordinator"), None
                        )
                        _actual_owner_pid = tag_to_profile_id.get(_coord_tag) if _coord_tag else None
                    if not _actual_owner_pid:
                        _actual_owner_pid = _kg_target_profile_id

                    # Check if the owner already has an active KG — don't displace it
                    _sq_conn = _sq_kg.connect(self.db_path)
                    try:
                        _has_active = _sq_conn.execute(
                            "SELECT 1 FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? AND is_active = 1 LIMIT 1",
                            (_actual_owner_pid, user_uuid),
                        ).fetchone() is not None
                    finally:
                        _sq_conn.close()

                    try:
                        _store = _GS_import(
                            profile_id=_actual_owner_pid,
                            user_uuid=user_uuid,
                            kg_id=_new_kg_id,
                        )
                        _store.set_kg_metadata(
                            name=_kg_data.get("name") or _kg_entry.get("name", "Imported KG"),
                            database_name=_kg_data.get("database_name") or _kg_entry.get("database_name"),
                            description=_kg_data.get("description") or _kg_entry.get("description"),
                            is_active=not _has_active,
                        )
                        _bulk_result = _store.import_bulk(
                            entities=_kg_data.get("entities", []),
                            relationships=_kg_data.get("relationships", []),
                        )
                        self._stamp_pack_kg_validation(_store, _kg_data)
                        app_logger.info(
                            f"  Imported KG '{_kg_entry.get('name')}' -> profile @{_kg_profile_tag} "
                            f"(id={_new_kg_id}, active={not _has_active}, "
                            f"entities={_bulk_result['entities_added']}, "
                            f"rels={_bulk_result['relationships_added']})"
                        )
                        _kgs_imported += 1
                        _imported_kg_ids.append(_new_kg_id)
                        _imported_kg_names.append(_kg_entry.get("name", ""))

                        # Create kg_profile_assignments rows for all assigned pack profiles
                        _assigned_profiles_manifest = _kg_entry.get("assigned_profiles", [])
                        if _assigned_profiles_manifest:
                            _sq_assign = _sq_kg.connect(self.db_path)
                            try:
                                for _ap in _assigned_profiles_manifest:
                                    _ap_tag = _ap.get("tag")
                                    _ap_active_src = bool(_ap.get("is_active", False))
                                    _ap_pid = tag_to_profile_id.get(_ap_tag)
                                    if not _ap_pid:
                                        continue
                                    if _ap_active_src:
                                        _ap_already_active = _sq_assign.execute(
                                            "SELECT 1 FROM kg_profile_assignments "
                                            "WHERE assigned_profile_id=? AND user_uuid=? AND is_active=1 LIMIT 1",
                                            (_ap_pid, user_uuid),
                                        ).fetchone() is not None
                                        _ap_activate = not _ap_already_active
                                    else:
                                        _ap_activate = False
                                    _sq_assign.execute(
                                        "INSERT OR IGNORE INTO kg_profile_assignments "
                                        "(kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active) "
                                        "VALUES (?, ?, ?, ?, ?)",
                                        (_new_kg_id, _actual_owner_pid, _ap_pid, user_uuid,
                                         1 if _ap_activate else 0),
                                    )
                                _sq_assign.commit()
                            except Exception as _ae:
                                app_logger.warning(
                                    f"  Failed to create KG assignments for '{_kg_entry.get('name')}': {_ae}"
                                )
                            finally:
                                _sq_assign.close()
                    except Exception as _e:
                        app_logger.warning(f"  Failed to import KG '{_kg_entry.get('name')}': {_e}")

            # Step 7: Reload retriever once
            retriever = get_rag_retriever()
            if retriever:
                try:
                    retriever.reload_collections_for_mcp_server()
                    app_logger.info("Reloaded RAG collections after agent pack import")
                except Exception as e:
                    app_logger.warning(f"Failed to reload collections: {e}")

            # Step 8: Determine pack type and coordinator info
            coordinator_tag = None
            coordinator_profile_id = None
            for prof in genie_profiles:
                if prof["role"] == "coordinator":
                    coordinator_tag = prof["tag"]
                    coordinator_profile_id = tag_to_profile_id.get(prof["tag"])
                    break

            has_genie = len(genie_profiles) > 0
            if has_genie:
                pack_type = "genie"
            elif len(profiles) > 1:
                pack_type = "bundle"
            else:
                pack_type = "single"

            # Step 9: Record installation in database.
            # ids, tags and roles come from ONE walk keyed by tag (see
            # `_aligned_recording`): profiles are CREATED non-genie first and the
            # coordinator last, so zipping the creation-ordered id list with the
            # manifest-ordered tag list mislabelled two profiles whenever a manifest
            # did not list its coordinator last — and every later update_in_place
            # then wrote each manifest entry onto the wrong profile.
            profile_ids, profile_tags, profile_roles = self._aligned_recording(
                profiles, tag_to_profile_id
            )

            installation_id = self._record_installation(
                manifest=manifest,
                coordinator_tag=coordinator_tag,
                coordinator_profile_id=coordinator_profile_id,
                pack_type=pack_type,
                profile_ids=profile_ids,
                profile_tags=profile_tags,
                profile_roles=profile_roles,
                collection_ids=[info["id"] for info in ref_to_collection_id.values()],
                collection_refs=list(ref_to_collection_id.keys()),
                kg_ids=_imported_kg_ids,
                kg_names=_imported_kg_names,
                skill_ids=_installed_skill_ids,
                user_uuid=user_uuid,
            )

            # Trust Membrane: reinstate each member's captured attestation onto its
            # materialised copy — ONE step shared with update_in_place (see the method).
            self._reinstate_member_trust(manifest, installation_id)

            result = {
                "installation_id": installation_id,
                "name": manifest["name"],
                "coordinator_tag": coordinator_tag,
                "coordinator_profile_id": coordinator_profile_id,
                "profiles_created": len(created_profile_ids),
                "collections_created": len(ref_to_collection_id),
                "kgs_created": _kgs_imported,
                # Legacy compat
                "experts_created": len(non_genie),
                "tag_remap": tag_remap if tag_remap else None,
                "warnings": import_warnings if import_warnings else None,
            }

            app_logger.info(f"Agent pack '{manifest['name']}' installed successfully (id={installation_id})")
            return result

        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            app_logger.error(f"Failed to import agent pack: {e}", exc_info=True)
            raise RuntimeError(f"Failed to import agent pack: {e}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # ── Export (legacy — single coordinator) ───────────────────────────────────

    async def export_pack(
        self,
        coordinator_profile_id: str | None = None,
        user_uuid: str = "",
        profile_ids: list[str] | None = None,
        pack_name: str = "",
        pack_description: str = "",
    ) -> Path:
        """Export profiles + collections as .agentpack (v1.1 manifest).

        Can be called in two ways:
        - Legacy: coordinator_profile_id only (exports genie + children)
        - New: profile_ids list (exports any selection of profiles)

        Returns: Path to the created .agentpack file.
        """
        import shutil
        from trusted_data_agent.core.collection_utils import export_collection_to_zip
        from trusted_data_agent.core.config_manager import get_config_manager

        config_manager = get_config_manager()

        # Resolve profile list
        if profile_ids is None and coordinator_profile_id:
            profile_ids = [coordinator_profile_id]

        if not profile_ids:
            raise ValueError("No profiles specified for export")

        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        try:
            # Load all selected profiles
            selected_profiles = []
            selected_ids = set(profile_ids)

            for pid in profile_ids:
                profile = config_manager.get_profile(pid, user_uuid)
                if profile:
                    selected_profiles.append(profile)
                else:
                    app_logger.warning(f"Profile {pid} not found, skipping")

            if not selected_profiles:
                raise ValueError("No valid profiles found")

            # Fix 3B: BFS expansion of genie children with cycle guard.
            # Using a queue so that nested genie coordinators (genie-of-genies) also
            # have their children expanded. selected_ids acts as the visited set.
            bfs_queue = [p for p in selected_profiles if p.get("profile_type") == "genie"]
            while bfs_queue:
                current = bfs_queue.pop(0)
                child_ids = current.get("genieConfig", {}).get("slaveProfiles", [])
                for child_id in child_ids:
                    if child_id in selected_ids:
                        continue  # cycle guard: already visited
                    child = config_manager.get_profile(child_id, user_uuid)
                    if child:
                        selected_profiles.append(child)
                        selected_ids.add(child_id)
                        app_logger.info(
                            f"  Auto-included child @{child.get('tag')} "
                            f"from genie @{current.get('tag')}"
                        )
                        # If the child is also a genie, enqueue it for further expansion
                        if child.get("profile_type") == "genie":
                            bfs_queue.append(child)

            # Build tag set for genie child references
            genie_child_ids = set()
            for profile in selected_profiles:
                if profile.get("profile_type") == "genie":
                    genie_child_ids.update(profile.get("genieConfig", {}).get("slaveProfiles", []))

            # Export collections and build refs
            collections_dir = temp_path / "collections"
            collections_dir.mkdir()

            manifest_collections = []
            manifest_profiles = []
            exported_collection_ids = set()
            ref_counter = 0

            for profile in selected_profiles:
                collection_refs = []

                # Collect collections from knowledgeConfig
                knowledge_config = profile.get("knowledgeConfig", {})
                profile_collections = knowledge_config.get("collections", [])

                for coll_info in profile_collections:
                    coll_id = coll_info.get("id")
                    if not coll_id or coll_id in exported_collection_ids:
                        # Already exported — find existing ref
                        existing_ref = next(
                            (mc["ref"] for mc in manifest_collections if mc.get("_source_id") == coll_id),
                            None
                        )
                        if existing_ref:
                            collection_refs.append(existing_ref)
                        continue

                    ref_counter += 1
                    ref_name = f"collection_{ref_counter}"
                    safe_ref = ref_name.replace(" ", "_").lower()

                    try:
                        exported_zip = await export_collection_to_zip(
                            collection_id=coll_id,
                            user_uuid=user_uuid,
                            output_path=collections_dir,
                        )

                        final_name = f"{safe_ref}.zip"
                        final_path = collections_dir / final_name
                        if exported_zip != final_path:
                            exported_zip.rename(final_path)

                        from trusted_data_agent.core.collection_db import CollectionDatabase
                        db = CollectionDatabase()
                        coll_meta = db.get_collection_by_id(coll_id)

                        mc_entry = {
                            "ref": safe_ref,
                            "file": f"collections/{final_name}",
                            "name": coll_meta["name"] if coll_meta else coll_info.get("name", ""),
                            "repository_type": coll_meta.get("repository_type", "knowledge") if coll_meta else "knowledge",
                            "description": coll_meta.get("description", "") if coll_meta else "",
                            "backend_type": coll_meta.get("backend_type", "chromadb") if coll_meta else "chromadb",
                            "_source_id": coll_id,  # Internal tracking, stripped before writing
                        }
                        if coll_meta and coll_meta.get("vector_store_config_id"):
                            mc_entry["vector_store_config_ref"] = coll_meta["vector_store_config_id"]
                        manifest_collections.append(mc_entry)

                        collection_refs.append(safe_ref)
                        exported_collection_ids.add(coll_id)

                    except Exception as e:
                        raise PackExportIncomplete(
                            f"collection {coll_id} is referenced by @{profile.get('tag')} "
                            f"but could not be exported: {e}"
                        ) from e

                # Also check ragCollections (planner repos for tool-using profiles only)
                profile_type = profile.get("profile_type", "")
                uses_tools = (
                    profile_type == "tool_enabled"
                    or (profile_type == "llm_only" and profile.get("useMcpTools"))
                )
                rag_collection_ids = profile.get("ragCollections", []) if uses_tools else []
                for coll_id in rag_collection_ids:
                    if coll_id in exported_collection_ids:
                        existing_ref = next(
                            (mc["ref"] for mc in manifest_collections if mc.get("_source_id") == coll_id),
                            None
                        )
                        if existing_ref:
                            collection_refs.append(existing_ref)
                        continue

                    ref_counter += 1
                    ref_name = f"collection_{ref_counter}"
                    safe_ref = ref_name.replace(" ", "_").lower()

                    try:
                        exported_zip = await export_collection_to_zip(
                            collection_id=coll_id,
                            user_uuid=user_uuid,
                            output_path=collections_dir,
                        )

                        final_name = f"{safe_ref}.zip"
                        final_path = collections_dir / final_name
                        if exported_zip != final_path:
                            exported_zip.rename(final_path)

                        from trusted_data_agent.core.collection_db import CollectionDatabase
                        db = CollectionDatabase()
                        coll_meta = db.get_collection_by_id(coll_id)

                        mc_entry = {
                            "ref": safe_ref,
                            "file": f"collections/{final_name}",
                            "name": coll_meta["name"] if coll_meta else "",
                            "repository_type": coll_meta.get("repository_type", "planner") if coll_meta else "planner",
                            "description": coll_meta.get("description", "") if coll_meta else "",
                            "backend_type": coll_meta.get("backend_type", "chromadb") if coll_meta else "chromadb",
                            "_source_id": coll_id,
                        }
                        if coll_meta and coll_meta.get("repository_type") == "planner":
                            # The lock travels as the publisher's intent (portable, and
                            # unlocking on a consumer is a posture downgrade).
                            mc_entry["locked"] = bool(coll_meta.get("trust_locked"))
                        if coll_meta and coll_meta.get("vector_store_config_id"):
                            mc_entry["vector_store_config_ref"] = coll_meta["vector_store_config_id"]
                        manifest_collections.append(mc_entry)

                        collection_refs.append(safe_ref)
                        exported_collection_ids.add(coll_id)

                    except Exception as e:
                        raise PackExportIncomplete(
                            f"planner repository {coll_id} is referenced by "
                            f"@{profile.get('tag')} but could not be exported: {e}"
                        ) from e

                # Determine role
                if profile.get("profile_type") == "genie":
                    role = "coordinator"
                elif profile["id"] in genie_child_ids:
                    role = "expert"
                else:
                    role = "standalone"

                # Build profile entry
                prof_entry = {
                    "tag": profile.get("tag"),
                    "name": profile.get("name"),
                    "description": profile.get("description"),
                    "profile_type": profile.get("profile_type"),
                    "role": role,
                    "classification_mode": profile.get("classification_mode", "light"),
                    "contextWindowTypeId": profile.get("contextWindowTypeId"),
                }

                # Deduplicate collection_refs while preserving order
                if collection_refs:
                    seen = set()
                    deduped = []
                    for cr in collection_refs:
                        if cr not in seen:
                            seen.add(cr)
                            deduped.append(cr)
                    prof_entry["collection_refs"] = deduped

                if profile.get("profile_type") == "genie":
                    # child_tags: tags of children in this pack
                    child_ids = profile.get("genieConfig", {}).get("slaveProfiles", [])
                    child_tags = []
                    for cid in child_ids:
                        child_prof = next((p for p in selected_profiles if p["id"] == cid), None)
                        if child_prof:
                            child_tags.append(child_prof.get("tag"))
                    prof_entry["child_tags"] = child_tags

                    genie_config = profile.get("genieConfig", {})
                    gc_copy = {k: v for k, v in genie_config.items()
                               if k not in ("slaveProfiles", "slaveProfileSettings")}
                    # Remap slaveProfileSettings keys from profile IDs → profile tags
                    # so they survive export/import without being tied to runtime IDs.
                    slave_settings = genie_config.get("slaveProfileSettings", {})
                    if slave_settings:
                        settings_by_tag = {}
                        for pid, settings in slave_settings.items():
                            child_prof = next((p for p in selected_profiles if p["id"] == pid), None)
                            if child_prof:
                                settings_by_tag[child_prof.get("tag")] = settings
                        if settings_by_tag:
                            gc_copy["slaveProfileSettings"] = settings_by_tag
                    if gc_copy:
                        prof_entry["genieConfig"] = gc_copy

                elif profile.get("profile_type") == "tool_enabled":
                    prof_entry["requires_mcp"] = True

                # Preserve conversation capabilities for llm_only (Ideate) profiles so a
                # FORK round-trip (export -> import) keeps tool calling / knowledge. Without
                # this, forked Ideate slaves lose useMcpTools and arrive with no MCP tools.
                if profile.get("profile_type") == "llm_only":
                    if profile.get("useMcpTools"):
                        prof_entry["useMcpTools"] = True
                        prof_entry["requires_mcp"] = True   # so the importer binds an MCP server
                    if profile.get("useKnowledgeCollections"):
                        prof_entry["useKnowledgeCollections"] = True

                # Stamp the bound MCP server's NAME (portable across instances) so the
                # importer can auto-resolve it against the target user's servers by name
                # (tool_enabled and llm_only+useMcpTools alike).
                bound_mcp = profile.get("mcpServerId")
                if bound_mcp:
                    try:
                        _srv = next((s for s in config_manager.get_mcp_servers(user_uuid)
                                     if s.get("id") == bound_mcp), None)
                        if _srv and _srv.get("name"):
                            prof_entry["mcpServerName"] = _srv["name"]
                    except Exception:
                        pass  # name stamp is best-effort — export must never fail on it
                    # A multi-server agent carries ALL its servers, by name (stage 2).
                    _stamp_manifest_servers(prof_entry, profile, user_uuid)

                # Preserve the pre-qualified MCP tool set (tool_enabled and llm_only+useMcpTools).
                # Live profiles store it under "tools"; the manifest/import key is "allowed_tools".
                exported_tools = profile.get("tools") or profile.get("allowed_tools")
                if exported_tools:
                    prof_entry["allowed_tools"] = exported_tools

                # Portable profile config — ONE declarative contract
                # (core/profile_config_contract.py) instead of a hand-maintained allow-list.
                # Covers knowledgeConfig, skillsConfig, componentConfig, recommenderConfig
                # and the trust posture (grounding / guardrail / honesty / authority), and
                # resolves the legacy identityConfig alias. Historically each key had to be
                # added here AND at three other sites, and a key missing from any one of them
                # silently vanished on export — see the contract's module docstring.
                # Instance-local ids (knowledgeConfig.collections, genieConfig.slaveProfiles)
                # are dropped by the contract itself and merged, never replaced, so the
                # collection_refs / child_tags remap above survives.
                _pcc.apply_to_manifest(profile, prof_entry)

                # Include synthesis prompt override
                synthesis_prompt = knowledge_config.get("synthesisPromptOverride")
                if synthesis_prompt:
                    prof_entry["synthesisPromptOverride"] = synthesis_prompt

                # Preserve the session primer (auto-run kickoff) — notably the genie
                # coordinator's design-session driver — so it survives export/fork.
                session_primer = profile.get("session_primer")
                if session_primer:
                    prof_entry["session_primer"] = session_primer

                manifest_profiles.append(prof_entry)

            # Collect user skills that are auto-enabled (active=True) in any profile's skillsConfig
            skills_to_bundle = {}  # skill_id → {manifest, content}
            try:
                from trusted_data_agent.skills.manager import get_skill_manager
                skill_manager = get_skill_manager()
                for profile in selected_profiles:
                    for skill_entry in profile.get("skillsConfig", {}).get("skills", []):
                        if not skill_entry.get("active", False):
                            continue
                        skill_id = skill_entry.get("id")
                        if not skill_id or skill_id in skills_to_bundle:
                            continue
                        s_manifest = skill_manager.get_skill_manifest(skill_id)
                        if s_manifest and s_manifest.get("_is_user", False):
                            s_content = skill_manager.get_skill_full_content(skill_id)
                            if s_content:
                                # Progressive skills (PROP-005): the library payload
                                # travels with the pack, index-driven ({} for legacy).
                                s_library = skill_manager.get_skill_library_files(skill_id)
                                skills_to_bundle[skill_id] = {"manifest": s_manifest,
                                                              "content": s_content,
                                                              "library": s_library}
                                app_logger.info(
                                    f"  Will bundle user skill '{skill_id}' (auto-enabled"
                                    + (f", {len(s_library)} library files" if s_library else "")
                                    + ")")
            except Exception as e:
                app_logger.warning(f"Could not collect skills for agent pack export: {e}")

            # Collect active Knowledge Graphs owned by selected profiles
            manifest_kgs = []
            knowledge_graphs_dir = temp_path / "knowledge_graphs"
            kg_counter = 0
            exported_kg_ids: set = set()
            export_warnings: list = []  # Surfaced to caller (e.g. subscribed-KG skip)

            try:
                from components.builtin.knowledge_graph.graph_store import GraphStore as _GS_export
                import sqlite3 as _sqlite3_kg
                all_user_kgs = _GS_export.list_all_graphs(user_uuid)

                try:
                    from trusted_data_agent.core.config import APP_CONFIG
                    _kg_db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
                except Exception:
                    _kg_db_path = str(self.db_path)
                _kg_db = _sqlite3_kg.connect(_kg_db_path)
                _kg_db.row_factory = _sqlite3_kg.Row

                for _kg_profile in selected_profiles:
                    _pid = _kg_profile["id"]
                    # Primary: use kg_profile_assignments as source of truth for active KG per profile.
                    # kg_metadata.profile_id is the KG's owner profile which may differ from the
                    # assigned profile (e.g. after profile IDs change).
                    _profile_kgs = []
                    try:
                        _arows = _kg_db.execute(
                            "SELECT kg_id FROM kg_profile_assignments "
                            "WHERE assigned_profile_id = ? AND user_uuid = ? AND is_active = 1",
                            (_pid, user_uuid),
                        ).fetchall()
                        for _arow in _arows:
                            _akg = next(
                                (kg for kg in all_user_kgs
                                 if kg["kg_id"] == _arow[0] and _arow[0] not in exported_kg_ids),
                                None,
                            )
                            if _akg:
                                _profile_kgs.append(_akg)
                    except Exception:
                        pass  # kg_profile_assignments may not exist on very old installs

                    # Fallback: legacy kg_metadata ownership match (KGs created before
                    # kg_profile_assignments existed, or freshly created with no assignment row yet)
                    if not _profile_kgs:
                        _profile_kgs = [
                            kg for kg in all_user_kgs
                            if kg.get("profile_id") == _pid and kg.get("is_active")
                            and kg["kg_id"] not in exported_kg_ids
                        ]
                    _kg_refs_for_profile = []

                    for _kg_meta in _profile_kgs:
                        if _kg_meta["kg_id"] in exported_kg_ids:
                            continue  # safety guard against duplicates

                        # Subscription-export gate (Phase 6 of KG subs):
                        # subscribed KGs are bound to a marketplace listing the
                        # importer may not have access to.  Carrying them in a
                        # pack creates fragile installs.  Skip + warn; users
                        # who want to include the data should "Convert to Fork"
                        # first (creates an editable, pack-portable copy).
                        try:
                            _is_sub_row = _kg_db.execute(
                                "SELECT is_subscribed FROM kg_metadata WHERE kg_id = ?",
                                (_kg_meta["kg_id"],),
                            ).fetchone()
                        except Exception:
                            _is_sub_row = None  # column missing on pre-migration installs
                        if _is_sub_row and _is_sub_row[0]:
                            warning_msg = (
                                f"Skipped subscribed KG '{_kg_meta.get('kg_name')}' "
                                f"(profile {_kg_profile.get('tag') or _pid}) — "
                                "convert to a Fork to include it in the pack."
                            )
                            app_logger.info(f"[Pack export] {warning_msg}")
                            export_warnings.append(warning_msg)
                            continue

                        kg_counter += 1
                        _kg_ref = f"kg_{kg_counter}"
                        _kg_file_name = f"{_kg_ref}.json"

                        try:
                            _store = _GS_export(
                                profile_id=_pid,
                                user_uuid=user_uuid,
                                kg_id=_kg_meta["kg_id"],
                            )
                            # await — these became async in the May-2026 kgstore
                            # abstraction; unawaited they return coroutine objects and
                            # the except below swallowed the failure, so every pack
                            # since then silently exported ZERO knowledge graphs
                            _entities = await _store.list_entities(limit=999999)
                            _relationships = await _store.list_relationships(limit=999999)
                        except Exception as _e:
                            app_logger.warning(
                                f"Failed to export KG '{_kg_meta.get('kg_name')}': {_e}"
                            )
                            continue
                        # PROP-008 A5: the authored layer travels as its PUBLISHED nodes only
                        # (a draft stays home), each stamped with its extension, beside the
                        # base — and every signed extension's claim travels with it
                        _kg_trust_extensions = {}
                        try:
                            from trusted_data_agent.core.trust import extensions as _trust_ext
                            _base_e, _base_r, _, _ = _trust_ext.split_layers(_entities, _relationships)
                            _pub_e, _pub_r = _trust_ext.portable_authored(_entities, _relationships, user_uuid)
                            _kg_trust_extensions = _trust_ext.capture_claims(_kg_meta["kg_id"], _entities)
                            _entities, _relationships = _base_e + _pub_e, _base_r + _pub_r
                        except Exception as _xe:  # noqa: BLE001 — the trust suite is removable
                            app_logger.debug(f"KG export: authored layer carried as-is ({_xe})")

                        if not knowledge_graphs_dir.exists():
                            knowledge_graphs_dir.mkdir()

                        # the source-validation stamp travels with the graph: a consumer's
                        # copy was validated where the publisher validated it, at that time
                        try:
                            _kg_validation = _store.get_source_validation() if hasattr(_store, "get_source_validation") else None
                        except Exception:  # noqa: BLE001 — a missing stamp never blocks an export
                            _kg_validation = None
                        _kg_json = {
                            "name": _kg_meta.get("kg_name", ""),
                            "database_name": _kg_meta.get("database_name", ""),
                            "description": _kg_meta.get("description", ""),
                            "source_validation": _kg_validation,
                            "trust_extensions": _kg_trust_extensions,
                            "entities": _entities,
                            "relationships": _relationships,
                        }
                        (knowledge_graphs_dir / _kg_file_name).write_text(
                            json.dumps(_kg_json, indent=2, ensure_ascii=False)
                        )

                        _pack_pid_to_tag = {p["id"]: p.get("tag") for p in selected_profiles}
                        _kg_owner_tag = _pack_pid_to_tag.get(_kg_meta.get("profile_id"))

                        _kg_assigned_profiles = []
                        try:
                            _assign_rows = _kg_db.execute(
                                "SELECT assigned_profile_id, is_active FROM kg_profile_assignments "
                                "WHERE kg_id = ? AND user_uuid = ?",
                                (_kg_meta["kg_id"], user_uuid),
                            ).fetchall()
                            for _ar in _assign_rows:
                                _atag = _pack_pid_to_tag.get(_ar["assigned_profile_id"])
                                if _atag:
                                    _kg_assigned_profiles.append(
                                        {"tag": _atag, "is_active": bool(_ar["is_active"])}
                                    )
                        except Exception:
                            pass

                        manifest_kgs.append({
                            "ref": _kg_ref,
                            "file": f"knowledge_graphs/{_kg_file_name}",
                            # the publisher's graph identity — a SUBSCRIPTION keys its copy to
                            # it so a later sync can find the source; a fork ignores it
                            "kg_id": _kg_meta["kg_id"],
                            # The OWNER when it is known: `_kg_profile` is whichever
                            # assigned profile this export walked first, which stops
                            # identifying the graph as soon as it grounds more than one
                            # agent. Consumers resolve by owner first regardless; this
                            # keeps the two fields from disagreeing in new manifests.
                            "profile_tag": _kg_owner_tag or _kg_profile.get("tag"),
                            "owner_profile_tag": _kg_owner_tag,
                            "name": _kg_meta.get("kg_name", ""),
                            "database_name": _kg_meta.get("database_name", ""),
                            "description": _kg_meta.get("description", ""),
                            "assigned_profiles": _kg_assigned_profiles,
                            # A5: the extension claims in the MANIFEST too, so the one
                            # reinstatement step reads them beside member_trust
                            "trust_extensions": _kg_trust_extensions,
                        })
                        _kg_refs_for_profile.append(_kg_ref)
                        exported_kg_ids.add(_kg_meta["kg_id"])
                        app_logger.info(
                            f"  Exported KG '{_kg_meta.get('kg_name')}' -> {_kg_file_name}"
                        )

                    # Attach kg_refs to the matching profile entry in manifest_profiles
                    if _kg_refs_for_profile:
                        for _mp in manifest_profiles:
                            if _mp["tag"] == _kg_profile.get("tag"):
                                _mp["kg_refs"] = _kg_refs_for_profile
                                break

                _kg_db.close()
            except Exception as e:
                app_logger.warning(f"Could not collect knowledge graphs for agent pack export: {e}")

            # Gather unique vector store configs referenced by pack collections
            manifest_vs_configs = []
            vs_config_ids = set()
            for mc in manifest_collections:
                vs_ref = mc.get("vector_store_config_ref")
                if vs_ref and vs_ref not in vs_config_ids:
                    vs_config_ids.add(vs_ref)
            if vs_config_ids:
                try:
                    from trusted_data_agent.core.config_manager import get_config_manager
                    config_manager = get_config_manager()
                    vs_configs = config_manager.get_vector_store_configurations(user_uuid)
                    for vs_id in vs_config_ids:
                        vs_config = next((c for c in vs_configs if c.get("id") == vs_id), None)
                        if vs_config:
                            manifest_vs_configs.append({
                                "ref": vs_id,
                                "name": vs_config.get("name", ""),
                                "backend_type": vs_config.get("backend_type", "chromadb"),
                                "backend_config": vs_config.get("backend_config", {}),
                            })
                except Exception as e:
                    app_logger.warning(f"Could not resolve vector store configs for agent pack export: {e}")

            # Strip internal tracking fields from collections
            for mc in manifest_collections:
                mc.pop("_source_id", None)

            # Determine pack name
            if not pack_name:
                genie_prof = next((p for p in selected_profiles if p.get("profile_type") == "genie"), None)
                pack_name = genie_prof.get("name", "Exported Agent Pack") if genie_prof else "Exported Agent Pack"

            # Build skill refs list
            skill_refs = [
                {
                    "id": sid,
                    "file": f"skills/{sid}.skill",
                    "name": data["manifest"].get("name", sid),
                    "description": data["manifest"].get("description", ""),
                }
                for sid, data in skills_to_bundle.items()
            ]

            # Determine manifest version: 1.4 if KGs, 1.3 if skills, 1.2 if VS configs, else 1.1
            if manifest_kgs:
                manifest_version = "1.4"
            elif skill_refs:
                manifest_version = "1.3"
            elif manifest_vs_configs:
                manifest_version = "1.2"
            else:
                manifest_version = "1.1"

            manifest = {
                "format_version": manifest_version,
                "name": pack_name,
                "description": pack_description or "",
                "author": "",
                "version": "1.0.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "tags": [],
                "profiles": manifest_profiles,
                "collections": manifest_collections,
            }
            if manifest_vs_configs:
                manifest["vector_store_configurations"] = manifest_vs_configs
            if skill_refs:
                manifest["skills"] = skill_refs
            if manifest_kgs:
                manifest["knowledge_graphs"] = manifest_kgs

            # Trust Membrane: capture each signed member's (content-bound) attestation so
            # trust survives the import. Claims are matched to materialised members by
            # content hash at import — no id mapping needed.
            try:
                from trusted_data_agent.core.trust import portability as _trust_portability
                from trusted_data_agent.core.trust import pack as _trust_pack
                _member_trust = []
                for _pid in (profile_ids or []):
                    _member_trust.append(_trust_portability.capture("agent", _pid,
                                                                    owner_uuid=user_uuid))
                for _cid in exported_collection_ids:
                    _member_trust.append(_trust_portability.capture(_trust_pack.collection_kind(_cid), str(_cid)))
                for _kid in exported_kg_ids:
                    _member_trust.append(_trust_portability.capture("ontology", str(_kid)))
                for _sid in skills_to_bundle.keys():
                    _member_trust.append(_trust_portability.capture("skill", _sid))
                _member_trust = [c for c in _member_trust if c]
                if _member_trust:
                    manifest["member_trust"] = _member_trust
            except Exception:
                pass

            # Write manifest
            manifest_file = temp_path / "manifest.json"
            with open(manifest_file, 'w') as f:
                json.dump(manifest, f, indent=2)

            # Bundle into .agentpack
            safe_name = (pack_name or "export").replace(" ", "_")[:50]
            agentpack_filename = f"{safe_name}.agentpack"
            agentpack_path = Path(temp_dir) / agentpack_filename

            with zipfile.ZipFile(agentpack_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(manifest_file, "manifest.json")
                for coll_file in collections_dir.rglob("*.zip"):
                    arcname = f"collections/{coll_file.name}"
                    zf.write(coll_file, arcname)
                for skill_id, skill_data in skills_to_bundle.items():
                    skill_zip_bytes = _build_skill_zip(
                        skill_id, skill_data["manifest"], skill_data["content"],
                        library_files=skill_data.get("library") or None)
                    zf.writestr(f"skills/{skill_id}.skill", skill_zip_bytes)
                if knowledge_graphs_dir.exists():
                    for _kg_file in knowledge_graphs_dir.rglob("*.json"):
                        zf.write(_kg_file, f"knowledge_graphs/{_kg_file.name}")

            app_logger.info(f"Exported agent pack to {agentpack_path} ({agentpack_path.stat().st_size / 1024 / 1024:.2f} MB)")
            return agentpack_path

        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            app_logger.error(f"Failed to export agent pack: {e}", exc_info=True)
            raise RuntimeError(f"Failed to export agent pack: {e}")

    # ── Create & Install (local) ─────────────────────────────────────────────

    async def create_and_install(
        self,
        profile_ids: list[str],
        user_uuid: str,
        pack_name: str = "",
        pack_description: str = "",
    ) -> dict:
        """Create an agent pack from existing profiles and register it locally.

        Unlike the export→import flow, this references existing profiles and
        collections with is_owned=0 so that uninstalling the pack does NOT
        delete the original resources.

        Returns: dict with installation details.
        """
        from trusted_data_agent.core.config_manager import get_config_manager

        config_manager = get_config_manager()

        # Export the .agentpack ZIP (for potential sharing/download later)
        zip_path = await self.export_pack(
            profile_ids=profile_ids,
            user_uuid=user_uuid,
            pack_name=pack_name,
            pack_description=pack_description,
        )

        # Read back the manifest to get profile metadata
        import zipfile as zf_mod
        with zf_mod.ZipFile(zip_path, 'r') as zf:
            manifest = json.loads(zf.read("manifest.json"))
        # Origin marker, on the RECORDED manifest only: this installation was
        # CREATED here, not subscribed. Pack-level ownership is derived, and
        # without this a created pack whose resources are all referenced
        # (is_owned=0 so uninstall keeps them) is indistinguishable from a
        # subscription — it rendered as "Subscribed" with no uninstall path.
        manifest["_registration_origin"] = "created"

        # Resolve actual profile objects
        selected_profiles = []
        selected_ids = set(profile_ids)
        for pid in profile_ids:
            profile = config_manager.get_profile(pid, user_uuid)
            if profile:
                selected_profiles.append(profile)

        # Auto-include genie children
        for profile in list(selected_profiles):
            if profile.get("profile_type") == "genie":
                child_ids = profile.get("genieConfig", {}).get("slaveProfiles", [])
                for child_id in child_ids:
                    if child_id not in selected_ids:
                        child = config_manager.get_profile(child_id, user_uuid)
                        if child:
                            selected_profiles.append(child)
                            selected_ids.add(child_id)

        # Build lists for _record_installation
        rec_profile_ids = []
        rec_profile_tags = []
        rec_profile_roles = []

        genie_child_ids = set()
        for profile in selected_profiles:
            if profile.get("profile_type") == "genie":
                genie_child_ids.update(profile.get("genieConfig", {}).get("slaveProfiles", []))

        coordinator_tag = None
        coordinator_profile_id = None

        for profile in selected_profiles:
            rec_profile_ids.append(profile["id"])
            rec_profile_tags.append(profile.get("tag", ""))

            if profile.get("profile_type") == "genie":
                role = "coordinator"
                coordinator_tag = profile.get("tag")
                coordinator_profile_id = profile["id"]
            elif profile["id"] in genie_child_ids:
                role = "expert"
            else:
                role = "standalone"
            rec_profile_roles.append(role)

        # Gather existing collection IDs from profiles
        rec_collection_ids = []
        rec_collection_refs = []
        rec_planner_ids = []
        seen_collection_ids = set()

        for i, profile in enumerate(selected_profiles):
            # Knowledge collections
            for kc in profile.get("knowledgeConfig", {}).get("collections", []):
                cid = kc.get("id")
                if cid and cid not in seen_collection_ids:
                    seen_collection_ids.add(cid)
                    rec_collection_ids.append(int(cid))
                    rec_collection_refs.append(kc.get("name", f"collection_{cid}"))

            # Planner collections (tool_enabled only)
            if profile.get("profile_type") == "tool_enabled" or (
                profile.get("profile_type") == "llm_only" and profile.get("useMcpTools")
            ):
                for cid in profile.get("ragCollections", []):
                    if cid and cid != '*' and cid not in seen_collection_ids:
                        seen_collection_ids.add(cid)
                        rec_collection_ids.append(int(cid))
                        rec_collection_refs.append(f"planner_{cid}")
                        rec_planner_ids.append(int(cid))

        # The knowledge graphs the pack SHIPS, read from the manifest the export
        # just wrote — so the publisher's ledger and every consumer's ledger name
        # the same members by construction. Without this the publisher recorded
        # profiles and collections only while the consumer's import recorded its KG
        # copies too, so the two sides counted DIFFERENT members: the composition
        # hash could never agree and the marketplace honesty gate correctly refused
        # to reinstate the pack's own seal on a fork (found live, 2026-09-08 — the
        # publisher declared 8 members, the fork 11, the extra 3 being the
        # ontologies). Ids differ between the two sides; the composition hash is
        # taken over each member's CONTENT, so that is exactly what makes it match.
        rec_kg_ids, rec_kg_names = self._manifest_kg_members(manifest)

        # Determine pack type
        has_genie = any(p.get("profile_type") == "genie" for p in selected_profiles)
        pack_type = "genie" if has_genie else "standalone"

        # Record installation with is_owned=False (references existing resources)
        # Registering the same pack again is an UPDATE, not a second installation.
        # Without this, every `build.py --register` run inserted a fresh ledger row,
        # so one set of agents ended up claimed by N rows — and each card then
        # rendered N identical pack chips for a pack installed once. A genuine
        # second install produces DIFFERENT profiles (`expand` renames the tags,
        # `replace` deletes the originals), so the same owner holding two
        # registrations of one pack name is always bookkeeping, never two packs.
        existing_registration_id = self._find_own_registration(user_uuid, manifest["name"])

        installation_id = self._record_installation(
            manifest=manifest,
            replace_installation_id=existing_registration_id,
            coordinator_tag=coordinator_tag,
            coordinator_profile_id=coordinator_profile_id,
            pack_type=pack_type,
            profile_ids=rec_profile_ids,
            profile_tags=rec_profile_tags,
            profile_roles=rec_profile_roles,
            collection_ids=rec_collection_ids,
            collection_refs=rec_collection_refs,
            kg_ids=rec_kg_ids,
            kg_names=rec_kg_names,
            user_uuid=user_uuid,
            is_owned=False,
        )

        # The PUBLISHER's planner repositories take the pack's ownership stamp too.
        # The consumer's copy is stamped on install and therefore hashes on the
        # baseline formula (pack-owned cases, canonical view); a publisher whose own
        # repository is unstamped signs the LEGACY formula (every file, raw) — two
        # formulas over the same content, so the signature could never travel, and
        # the consumer's agent read `unsigned` with nothing to reinstate (found live,
        # 2026-09-07). Same function on both sides, so both derive the same files.
        planner_cases_stamped = 0
        if rec_planner_ids:
            from trusted_data_agent.core.collection_db import get_collection_db as _gcd
            _cdb = _gcd()
            for _pid in rec_planner_ids:
                try:
                    planner_cases_stamped += await self._stamp_planner_ownership(
                        _pid, _cdb.get_collection_by_id(_pid) or {},
                        self._pack_ref(manifest))
                except Exception as _e:
                    app_logger.warning(
                        f"create_and_install: planner {_pid} not stamped: {_e}")

        # Store the ZIP path in manifest for potential download
        app_logger.info(
            f"Created and installed agent pack '{pack_name}' "
            f"(installation_id={installation_id}, "
            f"{len(rec_profile_ids)} profiles, "
            f"{len(rec_collection_ids)} collections, is_owned=False)"
        )

        return {
            "status": "success",
            "installation_id": installation_id,
            "name": manifest["name"],
            "profiles_created": len(rec_profile_ids),
            "collections_created": len(rec_collection_ids),
            "planner_cases_stamped": planner_cases_stamped,
            "zip_path": str(zip_path),
        }

    # ── Update Pack Profiles ───────────────────────────────────────────────────

    async def update_pack_profiles(
        self,
        installation_id: int,
        user_uuid: str,
        profile_ids: list[str],
    ) -> dict:
        """Replace the profiles referenced by an installed agent pack.

        Resolves coordinator/expert roles automatically (same logic as
        create_and_install). Existing resources keep their prior is_owned
        flag across the reconcile; newly added resources default to
        is_owned=0 (referenced, not created by this call).

        Returns: dict with updated coordinator_tag, pack_type, profile counts.
        """
        from trusted_data_agent.core.config_manager import get_config_manager

        config_manager = get_config_manager()

        # Validate ownership
        conn = sqlite_util.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM agent_pack_installations WHERE id = ? AND owner_user_id = ?",
                (installation_id, user_uuid),
            )
            if not cursor.fetchone():
                raise ValueError(f"Pack {installation_id} not found or access denied")
        finally:
            conn.close()

        # Resolve profiles + auto-include genie children
        selected_profiles = []
        selected_ids = set(profile_ids)
        for pid in profile_ids:
            profile = config_manager.get_profile(pid, user_uuid)
            if profile:
                selected_profiles.append(profile)

        for profile in list(selected_profiles):
            if profile.get("profile_type") == "genie":
                for child_id in profile.get("genieConfig", {}).get("slaveProfiles", []):
                    if child_id not in selected_ids:
                        child = config_manager.get_profile(child_id, user_uuid)
                        if child:
                            selected_profiles.append(child)
                            selected_ids.add(child_id)

        # Compute coordinator/expert roles
        genie_child_ids: set[str] = set()
        for profile in selected_profiles:
            if profile.get("profile_type") == "genie":
                genie_child_ids.update(profile.get("genieConfig", {}).get("slaveProfiles", []))

        rec_profile_ids: list[str] = []
        rec_profile_tags: list[str] = []
        rec_profile_roles: list[str] = []
        coordinator_tag = None
        coordinator_profile_id = None

        for profile in selected_profiles:
            rec_profile_ids.append(profile["id"])
            rec_profile_tags.append(profile.get("tag", ""))
            if profile.get("profile_type") == "genie":
                role = "coordinator"
                coordinator_tag = profile.get("tag")
                coordinator_profile_id = profile["id"]
            elif profile["id"] in genie_child_ids:
                role = "expert"
            else:
                role = "standalone"
            rec_profile_roles.append(role)

        # Gather collections from selected profiles
        rec_collection_ids: list[int] = []
        rec_collection_refs: list[str] = []
        seen_collection_ids: set = set()

        for profile in selected_profiles:
            for kc in profile.get("knowledgeConfig", {}).get("collections", []):
                cid = kc.get("id")
                if cid and cid not in seen_collection_ids:
                    seen_collection_ids.add(cid)
                    rec_collection_ids.append(int(cid))
                    rec_collection_refs.append(kc.get("name", f"collection_{cid}"))
            if profile.get("profile_type") == "tool_enabled" or (
                profile.get("profile_type") == "llm_only" and profile.get("useMcpTools")
            ):
                for cid in profile.get("ragCollections", []):
                    if cid and cid != "*" and cid not in seen_collection_ids:
                        seen_collection_ids.add(cid)
                        rec_collection_ids.append(int(cid))
                        rec_collection_refs.append(f"planner_{cid}")

        has_genie = any(p.get("profile_type") == "genie" for p in selected_profiles)
        pack_type = "genie" if has_genie else "standalone"

        # Persist changes
        conn = sqlite_util.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Update header fields
            cursor.execute(
                """UPDATE agent_pack_installations
                   SET coordinator_tag = ?, coordinator_profile_id = ?, pack_type = ?
                   WHERE id = ?""",
                (coordinator_tag, coordinator_profile_id, pack_type, installation_id),
            )

            # Snapshot existing is_owned per (resource_type, resource_id) so we
            # preserve ownership across the DELETE/INSERT reconcile. Newly added
            # resources default to is_owned=0 (referenced, not created here).
            cursor.execute(
                "SELECT resource_type, resource_id, is_owned FROM agent_pack_resources "
                "WHERE pack_installation_id = ?",
                (installation_id,),
            )
            prior_ownership = {
                (r[0], r[1]): int(r[2]) for r in cursor.fetchall()
            }

            # Replace all resource rows
            cursor.execute(
                "DELETE FROM agent_pack_resources WHERE pack_installation_id = ?",
                (installation_id,),
            )

            for pid, tag, role in zip(rec_profile_ids, rec_profile_tags, rec_profile_roles):
                is_owned = prior_ownership.get(("profile", pid), 0)
                cursor.execute(
                    """INSERT INTO agent_pack_resources
                       (pack_installation_id, resource_type, resource_id, resource_tag, resource_role, is_owned)
                       VALUES (?, 'profile', ?, ?, ?, ?)""",
                    (installation_id, pid, tag, role, is_owned),
                )

            for coll_id, ref in zip(rec_collection_ids, rec_collection_refs):
                is_owned = prior_ownership.get(("collection", str(coll_id)), 0)
                cursor.execute(
                    """INSERT INTO agent_pack_resources
                       (pack_installation_id, resource_type, resource_id, resource_tag, resource_role, is_owned)
                       VALUES (?, 'collection', ?, ?, 'collection', ?)""",
                    (installation_id, str(coll_id), ref, is_owned),
                )

            conn.commit()
        finally:
            conn.close()

        app_logger.info(
            f"Updated pack {installation_id} profiles: {len(rec_profile_ids)} profiles, "
            f"{len(rec_collection_ids)} collections, coordinator=@{coordinator_tag}"
        )

        return {
            "coordinator_tag": coordinator_tag,
            "pack_type": pack_type,
            "profiles_count": len(rec_profile_ids),
            "collections_count": len(rec_collection_ids),
        }

    # ── Uninstall ──────────────────────────────────────────────────────────────

    async def uninstall_pack(self, installation_id: int, user_uuid: str) -> dict:
        """Remove all resources created by an agent pack.

        Uses conditional deletion: only deletes resources that are not
        referenced by any other pack (many-to-many safe).

        Returns: {"profiles_deleted": int, "collections_deleted": int,
                  "profiles_kept": int, "collections_kept": int}
        """
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.agent.rag_retriever import get_rag_retriever
        from trusted_data_agent.core.agent_pack_db import AgentPackDB

        config_manager = get_config_manager()
        pack_db = AgentPackDB(self.db_path)

        # Verify installation exists and is owned by user
        conn = sqlite_util.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id, name, owner_user_id, "
                "       json_extract(manifest_json, '$._registration_origin') "
                "  FROM agent_pack_installations WHERE id = ?",
                (installation_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Agent pack installation {installation_id} not found")
            if row[2] != user_uuid:
                raise ValueError("You don't own this agent pack installation")
            pack_name = row[1]
            # A registration CREATED here (build.py --register / create-and-install)
            # references resources that already existed — its own docstring: "so that
            # uninstalling the pack does NOT delete the original resources". Every
            # other origin (import, fork, subscribe) MATERIALISED its resources and
            # deletes them as before.
            registration_created_here = (row[3] == "created")
        finally:
            conn.close()

        # Get all resources BEFORE removing junction rows
        resources = pack_db.get_resources_for_pack(installation_id)

        # Check if any profile being deleted is the default profile
        profile_resources = [r for r in resources if r["resource_type"] == "profile"]
        default_profile_id = config_manager.get_default_profile_id(user_uuid)

        for res in profile_resources:
            if res["resource_id"] == default_profile_id:
                # Check if still referenced by another pack
                if not pack_db.is_pack_managed(res["resource_type"], res["resource_id"]):
                    raise ValueError(
                        f"Cannot uninstall agent pack: profile '@{res['resource_tag']}' is set as "
                        f"the default profile. Please change the default profile first, then "
                        f"try uninstalling again."
                    )

        # Master classification constraint: block if any pack profile is a master for profiles
        # outside this pack (dependents not being deleted in this same operation)
        pack_profile_ids = {r["resource_id"] for r in profile_resources}
        for res in profile_resources:
            if pack_db.is_pack_managed(res["resource_type"], res["resource_id"]):
                continue  # still referenced by another pack, won't be deleted
            dependent_profiles = config_manager.get_dependent_profiles(res["resource_id"], user_uuid)
            external_dependents = [p for p in dependent_profiles if p["id"] not in pack_profile_ids]
            if external_dependents:
                dep_names = ", ".join(
                    f"@{p.get('tag', '?')} ({p.get('name', p.get('id'))})"
                    for p in external_dependents
                )
                raise ValueError(
                    f"Cannot uninstall agent pack '{pack_name}': profile "
                    f"'@{res['resource_tag']}' is the master classification profile for: "
                    f"{dep_names}. Remove the 'Inherit Classification' setting from those "
                    f"profiles first."
                )

        # Step 1: Remove junction rows for THIS pack
        pack_db.remove_pack_resources(installation_id)

        # Step 2: Delete resources that are no longer referenced by ANY pack
        profiles_deleted = 0
        collections_deleted = 0
        profiles_kept = 0
        collections_kept = 0
        total_sessions_archived = 0

        # Separate profiles, collections, and knowledge graphs; process profiles first
        profile_resources = [r for r in resources if r["resource_type"] == "profile"]
        collection_resources = [r for r in resources if r["resource_type"] == "collection"]
        kg_resources = [r for r in resources if r["resource_type"] == "knowledge_graph"]

        # Sort profiles: coordinator first, then others
        coordinators = [r for r in profile_resources if r["resource_role"] == "coordinator"]
        others = [r for r in profile_resources if r["resource_role"] != "coordinator"]

        for res in coordinators + others:
            still_referenced = pack_db.is_pack_managed(res["resource_type"], res["resource_id"])

            if not still_referenced and res.get("is_owned", 1):
                try:
                    # Archive sessions that use this profile before deleting it
                    from trusted_data_agent.core.session_manager import archive_sessions_by_profile
                    archive_result = await archive_sessions_by_profile(res["resource_id"], user_uuid)
                    if archive_result["archived_count"] > 0:
                        total_sessions_archived += archive_result["archived_count"]
                        app_logger.info(
                            f"  Archived {archive_result['archived_count']} sessions for profile "
                            f"@{res['resource_tag']} (including {archive_result['genie_children_archived']} Genie children)"
                        )

                    success = config_manager.remove_profile(res["resource_id"], user_uuid)
                    if success:
                        profiles_deleted += 1
                        app_logger.info(f"  Deleted profile @{res['resource_tag']} (id={res['resource_id']})")
                    else:
                        app_logger.warning(f"  Profile @{res['resource_tag']} not found or already deleted")
                except Exception as e:
                    app_logger.warning(f"  Failed to delete profile @{res['resource_tag']}: {e}")
            else:
                profiles_kept += 1
                app_logger.info(f"  Kept profile @{res['resource_tag']} (still referenced by another pack)")

        # Delete collections
        retriever = get_rag_retriever()
        for res in collection_resources:
            still_referenced = pack_db.is_pack_managed(res["resource_type"], res["resource_id"])

            if not still_referenced and res.get("is_owned", 1):
                try:
                    collection_id = int(res["resource_id"])

                    # Archive sessions that use this collection before deleting it
                    from trusted_data_agent.core.session_manager import archive_sessions_by_collection
                    archive_result = await archive_sessions_by_collection(str(collection_id), user_uuid)
                    if archive_result["archived_count"] > 0:
                        total_sessions_archived += archive_result["archived_count"]
                        app_logger.info(
                            f"  Archived {archive_result['archived_count']} sessions for collection id={collection_id}"
                        )

                    if retriever:
                        success = await retriever.remove_collection(collection_id, user_id=user_uuid)
                        if success:
                            collections_deleted += 1
                            app_logger.info(f"  Deleted collection id={collection_id}")
                        else:
                            app_logger.warning(f"  Collection id={collection_id} not found or already deleted")
                    else:
                        app_logger.warning(f"  RAG retriever not available, cannot delete collection {collection_id}")
                except Exception as e:
                    app_logger.warning(f"  Failed to delete collection {res['resource_id']}: {e}")
            else:
                collections_kept += 1
                app_logger.info(f"  Kept collection {res['resource_id']} (still referenced by another pack)")

        # Delete knowledge graphs
        kgs_deleted = 0
        kgs_kept = 0
        for res in kg_resources:
            kg_id = res["resource_id"]
            # A publisher's own source graphs are REFERENCED by its registration,
            # never created by it, so unregistering the pack must leave them alone —
            # the contract create_and_install already states for profiles and
            # collections, which this loop was the one member class not to honour.
            # Deliberately scoped to that origin rather than to is_owned: subscribe
            # also records copies with is_owned=0 while genuinely owning them, so a
            # blanket is_owned guard would leave a subscriber's copies behind.
            if registration_created_here:
                kgs_kept += 1
                app_logger.info(
                    f"  Kept KG id={kg_id} ('{res['resource_tag']}') — referenced by a "
                    f"registration made here, not created by it"
                )
                continue
            try:
                # Resolve profile_id + optional backend config from metadata
                _conn_meta = sqlite_util.connect(self.db_path)
                _conn_meta.row_factory = sqlite3.Row
                try:
                    _meta_row = _conn_meta.execute(
                        "SELECT profile_id, kg_store_config_id FROM kg_metadata WHERE kg_id = ?",
                        (kg_id,)
                    ).fetchone()
                except Exception:
                    # kg_store_config_id column may not exist on older schema
                    _meta_row = _conn_meta.execute(
                        "SELECT profile_id FROM kg_metadata WHERE kg_id = ?",
                        (kg_id,)
                    ).fetchone()
                _conn_meta.close()

                if _meta_row:
                    profile_id_kg = _meta_row["profile_id"]
                    cfg_id = dict(_meta_row).get("kg_store_config_id")
                    from components.builtin.knowledge_graph.graph_store import GraphStore as _GS_del
                    _store_del = _GS_del(
                        profile_id=profile_id_kg, user_uuid=user_uuid,
                        kg_id=kg_id, kg_store_config_id=cfg_id,
                    )
                    _store_del.clear_graph()  # removes entities + relationships via backend

                # Model B: a pack graph's released view dies with it.
                try:
                    from trusted_data_agent.core import kg_release as _kgrel
                    _kgrel.drop_released_shadow(kg_id, user_uuid, self.db_path)
                except Exception:  # noqa: BLE001
                    pass
                # Clean up SQLite-only metadata rows
                conn_kg = sqlite_util.connect(self.db_path)
                try:
                    conn_kg.execute("DELETE FROM kg_metadata WHERE kg_id = ?", (kg_id,))
                    conn_kg.execute("DELETE FROM kg_profile_assignments WHERE kg_id = ?", (kg_id,))
                    conn_kg.commit()
                finally:
                    conn_kg.close()
                # Derived state (version history, usage telemetry) dies with its KG. Like
                # the attestation below, this has to be explicit at each deletion site
                # because KG deletion has no single chokepoint — but WHAT is purged is
                # one registry (core.kg_derived_state), never a per-site list. Fail-soft.
                try:
                    from trusted_data_agent.core import kg_derived_state as _kgds
                    _r = _kgds.purge_for_kg(kg_id)
                    _gone = {t: n for t, n in _r["removed"].items() if n}
                    if _gone:
                        app_logger.info(f"  Purged derived state for KG {kg_id}: {_gone}")
                except Exception as _ve:  # noqa: BLE001
                    app_logger.warning(f"  Could not purge derived state for KG {kg_id}: {_ve}")

                # #30 — retire the KG's attestation with the KG (profiles and
                # collections retire at their own removal chokepoints; KG deletion has
                # no single chokepoint, so each deletion site retires explicitly).
                try:
                    from trusted_data_agent.core.trust import store as _trust_store
                    if _trust_store.retire(kg_id, ("ontology",)):
                        app_logger.info(f"  Retired trust attestation for deleted KG {kg_id}")
                except ImportError:
                    pass

                kgs_deleted += 1
                app_logger.info(f"  Deleted KG id={kg_id} ('{res['resource_tag']}')")
            except Exception as e:
                app_logger.warning(f"  Failed to delete KG id={kg_id}: {e}")

        # Step 3b: Skills are KEPT and MARKED ORPHANED (operator-settled semantics).
        # They may have been edited in the skill editor, or referenced by profiles
        # outside this pack — so deletion is never safe by default. A skill still
        # managed by ANOTHER installed pack is left untouched (the same conditional
        # rule profiles and collections use).
        skills_orphaned = 0
        for res in [r for r in resources if r["resource_type"] == "skill"]:
            sid = res["resource_id"]
            try:
                if pack_db.is_pack_managed("skill", sid):
                    app_logger.info(f"  Skill '{sid}' still managed by another pack — untouched")
                    continue
                from trusted_data_agent.skills.manager import get_skill_manager
                mgr = get_skill_manager()
                manifest = (getattr(mgr, "manifests", {}) or {}).get(sid)
                if not manifest:
                    continue
                origin = ((manifest.get("uderia") or {}).get("pack_origin") or {})
                origin["orphaned_at"] = datetime.now(timezone.utc).isoformat()
                origin.setdefault("pack_name", pack_name)
                clean = {k: v for k, v in manifest.items() if not k.startswith("_")}
                clean.setdefault("uderia", {})["pack_origin"] = origin
                content = mgr.get_skill_full_content(sid) or ""
                # library_files=None → the on-demand library is left exactly as-is
                mgr.save_skill(sid, content, clean)
                skills_orphaned += 1
                app_logger.info(f"  Skill '{sid}' kept and marked orphaned")
            except Exception as e:
                app_logger.warning(f"  Could not mark skill '{sid}' orphaned: {e}")

        # Step 3: Delete installation record
        conn = sqlite_util.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM agent_pack_installations WHERE id = ?", (installation_id,))
            conn.commit()
        finally:
            conn.close()

        # #30 — the pack's own seal dies with its installation row (the members
        # retire at their own chokepoints). Defensive import, fail-soft: a dangling
        # row is an artefact of a pack that no longer exists, never a reason to fail
        # the uninstall the user asked for.
        try:
            from trusted_data_agent.core.trust import store as _trust_store
            if _trust_store.retire(str(installation_id), ("agent_pack",)):
                app_logger.info(f"  Retired trust attestation for uninstalled pack {installation_id}")
        except ImportError:
            pass
        except Exception as _rt_err:  # noqa: BLE001
            app_logger.warning(f"  Could not retire pack attestation {installation_id}: {_rt_err}")

        app_logger.info(f"Uninstalled agent pack '{pack_name}' (id={installation_id}): "
                       f"{profiles_deleted} profiles deleted, {profiles_kept} kept, "
                       f"{collections_deleted} collections deleted, {collections_kept} kept, "
                       f"{kgs_deleted} KGs deleted, {kgs_kept} KGs kept, "
                       f"{skills_orphaned} skills kept+orphaned, "
                       f"{total_sessions_archived} sessions archived")

        return {
            "profiles_deleted": profiles_deleted,
            "collections_deleted": collections_deleted,
            "kgs_deleted": kgs_deleted,
            "kgs_kept": kgs_kept,
            "skills_orphaned": skills_orphaned,
            "profiles_kept": profiles_kept,
            "collections_kept": collections_kept,
            "sessions_archived": total_sessions_archived,
        }

    # ── List ───────────────────────────────────────────────────────────────────

    async def list_packs(self, user_uuid: str) -> list[dict]:
        """List agent packs for user (owned + subscribed via sharing grants)."""
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()

        conn = sqlite_util.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT i.id, i.name, i.description, i.version, i.author, "
                "i.coordinator_tag, i.coordinator_profile_id, i.pack_type, i.installed_at, "
                "i.manifest_json, "
                # is_owned: any owned resource (created/forked/imported), OR an
                # explicit created-here origin marker. The marker covers packs
                # registered over REFERENCED resources (create-and-install:
                # is_owned=0 so uninstall keeps the user's originals) — without
                # it those rendered as subscriptions of themselves, offering
                # only an Unsubscribe that needs a marketplace id they lack.
                "CASE WHEN EXISTS("
                "  SELECT 1 FROM agent_pack_resources r "
                "  WHERE r.pack_installation_id = i.id AND r.is_owned = 1"
                ") OR json_extract(i.manifest_json, '$._registration_origin') = 'created' "
                "THEN 1 ELSE 0 END AS is_owned "
                "FROM agent_pack_installations i "
                "LEFT JOIN marketplace_sharing_grants msg "
                "  ON msg.resource_type = 'agent_pack' "
                "  AND msg.resource_id = CAST(i.id AS TEXT) "
                "  AND msg.grantee_user_id = ? "
                "WHERE i.owner_user_id = ? OR ("
                # Include sharing-grant rows only when the user has NO subscriber-side
                # installation that already covers this source installation.
                "  msg.id IS NOT NULL AND NOT EXISTS ("
                "    SELECT 1 FROM agent_pack_installations sub_i "
                "    WHERE sub_i.owner_user_id = ? "
                "    AND json_extract(sub_i.manifest_json, '$._source_installation_id') = i.id"
                "  )"
                ") "
                "ORDER BY i.installed_at DESC",
                (user_uuid, user_uuid, user_uuid)
            )
            rows = cursor.fetchall()

            packs = []
            for row in rows:
                # Count resources
                cursor.execute(
                    "SELECT resource_type, COUNT(*) FROM agent_pack_resources "
                    "WHERE pack_installation_id = ? GROUP BY resource_type",
                    (row["id"],)
                )
                counts = dict(cursor.fetchall())
                # The member tags, so an installed pack's card names its agents exactly
                # as the marketplace listing does. Without this the card fell back to the
                # coordinator tag alone and one pack read as six agents in one place and
                # one agent in the other.
                cursor.execute(
                    "SELECT resource_tag FROM agent_pack_resources "
                    "WHERE pack_installation_id = ? AND resource_type = 'profile' "
                    "AND resource_tag IS NOT NULL AND resource_tag <> '' ORDER BY id",
                    (row["id"],)
                )
                profile_tags = [r[0] for r in cursor.fetchall()]
                # Extract source installation id for subscriber-side installations
                manifest_data = {}
                try:
                    manifest_data = json.loads(row["manifest_json"] or "{}")
                    _source_inst_id = manifest_data.get("_source_installation_id")
                except Exception:
                    _source_inst_id = None

                profile_count = counts.get("profile", 0)
                pack_type = row["pack_type"] or "genie"

                # For genie packs, derive experts_count from LIVE coordinator profile
                # (the resources table is a snapshot from install time and becomes stale)
                if pack_type == "genie" and row["coordinator_profile_id"]:
                    coord_prof = config_manager.get_profile(row["coordinator_profile_id"], user_uuid)
                    if coord_prof:
                        experts_count = len(coord_prof.get("genieConfig", {}).get("slaveProfiles", []))
                    else:
                        experts_count = max(profile_count - 1, 0)
                else:
                    experts_count = 0

                packs.append({
                    "installation_id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "version": row["version"],
                    "author": row["author"],
                    "coordinator_tag": row["coordinator_tag"],
                    "coordinator_profile_id": row["coordinator_profile_id"],
                    "pack_type": pack_type,
                    "profiles_count": profile_count,
                    "experts_count": experts_count,
                    "collections_count": counts.get("collection", 0),
                    # The ontologies the pack brought with it. Same key as the
                    # marketplace listing serves — one name for one fact.
                    "kg_count": counts.get("knowledge_graph", 0),
                    "profile_tags": profile_tags,
                    # {tag: profile_type} so the card colours each member chip by its
                    # IFOC class without needing the viewer to own those profiles.
                    "profile_classes": {
                        p["tag"]: p["profile_type"]
                        for p in (manifest_data.get("profiles") or [])
                        if isinstance(p, dict) and p.get("tag") and p.get("profile_type")
                    },
                    "installed_at": row["installed_at"],
                    "is_owned": bool(row["is_owned"]),
                    "_source_installation_id": _source_inst_id,
                })

            return packs
        finally:
            conn.close()

    # ── Details ────────────────────────────────────────────────────────────────

    async def get_pack_details(self, installation_id: int, user_uuid: str) -> dict:
        """Get full details of an installed agent pack."""
        conn = sqlite_util.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM agent_pack_installations WHERE id = ? AND owner_user_id = ?",
                (installation_id, user_uuid)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Agent pack installation {installation_id} not found")

            # Get resources
            cursor.execute(
                "SELECT resource_type, resource_id, resource_tag, resource_role, is_owned "
                "FROM agent_pack_resources WHERE pack_installation_id = ?",
                (installation_id,)
            )
            resources = [dict(r) for r in cursor.fetchall()]

            return {
                "installation_id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "version": row["version"],
                "author": row["author"],
                "coordinator_tag": row["coordinator_tag"],
                "coordinator_profile_id": row["coordinator_profile_id"],
                "pack_type": row["pack_type"] or "genie",
                "installed_at": row["installed_at"],
                "manifest": json.loads(row["manifest_json"]),
                "resources": resources,
            }
        finally:
            conn.close()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_existing_pack_names(self, user_uuid: str) -> set[str]:
        """Return set of all installed pack names for a user."""
        conn = sqlite_util.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM agent_pack_installations WHERE owner_user_id = ?",
                (user_uuid,),
            )
            return {row["name"] for row in cursor.fetchall()}
        finally:
            conn.close()

    def _compute_tag_expansion(
        self,
        conflicting_tags: list[str],
        existing_tags: set[str],
        pack_profiles: list[dict],
    ) -> dict[str, str]:
        """Compute unique tag names for conflicting tags.

        For each conflicting tag, tries TAG2, TAG3, ... until a name is found
        that doesn't collide with existing tags or other pack profile tags.

        Returns: {old_tag: new_tag} only for tags that needed renaming.
        """
        all_pack_tags = {p["tag"] for p in pack_profiles}
        used = existing_tags | all_pack_tags
        remap = {}

        for tag in conflicting_tags:
            counter = 2
            candidate = f"{tag}{counter}"
            while candidate in used:
                counter += 1
                candidate = f"{tag}{counter}"
            remap[tag] = candidate
            used.add(candidate)  # Reserve it for subsequent iterations

        return remap

    def _apply_tag_remap(
        self,
        profiles: list[dict],
        remap: dict[str, str],
        knowledge_graphs: list[dict] | None = None,
    ) -> None:
        """Apply tag remap to EVERY tag-keyed field of the manifest, in place.

        A profile tag is the manifest's join key: several sections reference a profile
        by tag rather than by id, precisely so a copy can be materialised under new
        ids. ``expand`` renames those tags — so every section that references one must
        follow, or it silently references a profile that no longer exists.

        Missing one is not a loud failure, which is why this list is exhaustive rather
        than "the ones we happened to need". Observed live (2026-09-08): forking a pack
        a SECOND time renamed the tags but left the knowledge-graph entries pointing at
        the originals, so every graph was skipped and the fork's agents came up with no
        ontology at all -- reported only as a quiet ``kgs_created: 0``. For this pack
        the ontology IS the product.

        Updates:
        1. ``prof["tag"]`` for renamed profiles
        2. ``child_tags`` arrays in genie profiles
        3. ``genieConfig.slaveProfileSettings`` keys, which the export writes in TAG
           space for exactly this reason
        4. each knowledge-graph entry's ``profile_tag``, ``owner_profile_tag`` and
           ``assigned_profiles[].tag``
        """
        if not remap:
            return

        for prof in profiles:
            # Rename the profile's own tag
            if prof["tag"] in remap:
                prof["tag"] = remap[prof["tag"]]

            # Update child_tags references in genie profiles
            if prof.get("child_tags"):
                prof["child_tags"] = [
                    remap.get(ct, ct) for ct in prof["child_tags"]
                ]

            # Per-slave settings are exported keyed by TAG (the same remap pattern as
            # child_tags); an un-remapped key silently drops that slave's settings.
            gc = prof.get("genieConfig")
            if isinstance(gc, dict) and isinstance(gc.get("slaveProfileSettings"), dict):
                gc["slaveProfileSettings"] = {
                    remap.get(k, k): v for k, v in gc["slaveProfileSettings"].items()
                }

        for kg in (knowledge_graphs or []):
            if not isinstance(kg, dict):
                continue
            for key in ("profile_tag", "owner_profile_tag"):
                if kg.get(key) in remap:
                    kg[key] = remap[kg[key]]
            for assigned in (kg.get("assigned_profiles") or []):
                if isinstance(assigned, dict) and assigned.get("tag") in remap:
                    assigned["tag"] = remap[assigned["tag"]]

    # Resource types the update_in_place path actually REBUILDS each run.
    # reconcile_pack_resources removes rows absent from its desired set, so any
    # type not in this set must be carried forward — otherwise every update
    # silently wipes its provenance rows (knowledge_graph; a skill whose zip
    # failed to install this round) and uninstall loses sight of them.
    _UPDATE_REBUILT_TYPES = frozenset({"profile", "collection"})

    #: blocks that only exist for one profile type. When a pack changes a profile's
    #: type in place, the previous type's blocks must not survive the shallow merge
    #: (`update_profile` is dict.update): an `llm_only` analyst carrying a stale
    #: `genieConfig` reads as a coordinator to every consumer that tests the block.
    _TYPE_EXCLUSIVE_KEYS = {"genie": ("genieConfig",)}

    @staticmethod
    def _pack_owned_absence(spec) -> bool:
        """Pure: may the pack REMOVE this key from a copy when its entry omits it?
        Yes for a portable key with no downgrade guard; no for a guarded (posture) key,
        whose absence in a manifest must never weaken a hardened copy."""
        if spec is None or not getattr(spec, "portable", False):
            return False
        return not any(getattr(spec, a, ()) for a in ("mode_paths", "flag_paths", "floor_paths", "ceiling_paths"))

    @staticmethod
    def _aligned_recording(profiles: list[dict], tag_to_profile_id: dict) -> tuple[list, list, list]:
        """Pure: (ids, tags, roles) for the installation ledger, aligned BY TAG.

        One walk over the manifest's profiles, each id looked up by the tag it was
        created under — so the three lists agree by construction, whatever order the
        profiles were created in. A manifest profile that was not created (no id) is
        omitted rather than shifting every later row."""
        ids, tags, roles = [], [], []
        for prof in profiles:
            pid = tag_to_profile_id.get(prof.get("tag"))
            if not pid:
                continue
            ids.append(pid)
            tags.append(prof["tag"])
            roles.append(prof.get("role", "standalone"))
        return ids, tags, roles

    @staticmethod
    def _heal_ledger_tags(old_resources: list[dict], live_profiles: list[dict],
                          manifest_tags: set) -> tuple[list[dict], list[str]]:
        """Pure: reconcile the ledger's profile tags to the live profiles.

        The ledger (`agent_pack_resources`) records tag -> id; the live profile carries
        the tag the user actually addresses. A pack-managed profile's tag cannot be
        changed through the API, so when the two disagree the LEDGER is the copy that
        drifted (a mislabelled install). The live tag is adopted whenever it is one of
        the tags the manifest ships — the only case where trusting the ledger would
        make the update write a manifest entry onto the wrong profile. A live tag the
        manifest does not know (an out-of-band rename) keeps the recorded tag, so the
        renamed profile is still updated as the tag it was installed under.

        Returns the healed rows (new dicts; input untouched) and one note per change."""
        live_by_id = {p.get("id"): p for p in (live_profiles or []) if p.get("id")}
        healed, notes = [], []
        for r in old_resources:
            row = dict(r)
            if row.get("resource_type") == "profile":
                live = live_by_id.get(str(row.get("resource_id")))
                live_tag = (live or {}).get("tag")
                recorded = row.get("resource_tag")
                if live_tag and live_tag != recorded and live_tag in manifest_tags:
                    notes.append(
                        f"ledger recorded @{recorded} for profile {row.get('resource_id')} "
                        f"but the live profile is @{live_tag}; ledger corrected"
                    )
                    row["resource_tag"] = live_tag
                    if live and live.get("profile_type") == "genie":
                        row["resource_role"] = "coordinator"
                    elif row.get("resource_role") == "coordinator":
                        row["resource_role"] = "expert"
            healed.append(row)
        # A row kept on its recorded tag must not shadow a corrected row.
        corrected = {r["resource_tag"] for r, o in zip(healed, old_resources)
                     if r.get("resource_type") == "profile" and r.get("resource_tag") != o.get("resource_tag")}
        for r, o in zip(healed, old_resources):
            if (r.get("resource_type") == "profile" and r.get("resource_tag") == o.get("resource_tag")
                    and r.get("resource_tag") in corrected):
                live = live_by_id.get(str(r.get("resource_id")))
                if live and live.get("tag"):
                    notes.append(
                        f"ledger recorded @{o.get('resource_tag')} for profile {r.get('resource_id')} "
                        f"but that tag belongs to another pack profile; the live tag @{live['tag']} is used"
                    )
                    r["resource_tag"] = live["tag"]
        return healed, notes

    @classmethod
    def _carry_forward_resources(cls, desired: list[dict],
                                 old_resources: list[dict]) -> list[dict]:
        """Return `desired` plus every old row of a non-rebuilt type not already present.

        Pure — the testable half of update_in_place's step 5e. A skill the
        current manifest installed IS in `desired` (rebuilt this run); a skill
        that failed to install, or a knowledge_graph imported by an earlier
        install, is not — and must survive the reconcile.
        """
        merged = list(desired)
        seen = {(d["resource_type"], str(d["resource_id"])) for d in desired}
        for r in old_resources:
            key = (r["resource_type"], str(r["resource_id"]))
            if r["resource_type"] not in cls._UPDATE_REBUILT_TYPES and key not in seen:
                merged.append({
                    "resource_type": r["resource_type"],
                    "resource_id": str(r["resource_id"]),
                    "resource_tag": r.get("resource_tag"),
                    "resource_role": r.get("resource_role"),
                    "is_owned": bool(r.get("is_owned", True)),
                })
        return merged

    def _install_pack_skills(self, manifest: dict, temp_path: Path,
                             owner_user_uuid: str | None = None,
                             warnings_sink: list | None = None) -> list[str]:
        """Install (or refresh) every skill bundled in a pack manifest.

        THE one skill-install loop, shared by the fresh-install path (step 5b)
        AND update_in_place. It existed only on the fresh path until Aug 2026,
        so an update_in_place import silently skipped bundled skills — an
        install whose seed copy later disappeared (or a republish carrying a
        skill update) left the pack's profiles referencing skills that no
        longer existed, with no error anywhere. save_skill overwrites in
        place, so re-running on an update is exactly the desired refresh.

        owner_user_uuid feeds the provenance stamp AND arms the cross-owner
        conflict guard; a refused skill lands in warnings_sink (never raises
        out of the import — the pack's other resources install normally).

        Returns the ids actually installed (for agent_pack_resources).
        """
        installed: list[str] = []
        pack_skills = manifest.get("skills", [])
        if not pack_skills:
            return installed
        app_logger.info(f"Installing {len(pack_skills)} skill(s) from agent pack...")
        for skill_ref in pack_skills:
            skill_id = skill_ref.get("id")
            skill_file = skill_ref.get("file")
            if not skill_id or not skill_file:
                continue
            skill_zip_path = temp_path / skill_file
            if not skill_zip_path.exists():
                app_logger.warning(f"  Skill file not found in pack: {skill_file}")
                continue
            try:
                self._install_skill_from_zip_bytes(
                    skill_id, skill_zip_path.read_bytes(),
                    pack_name=manifest.get("name"),
                    owner_user_uuid=owner_user_uuid,
                )
                installed.append(skill_id)
            except SkillOwnershipConflict as e:
                app_logger.warning(f"  {e}")
                if warnings_sink is not None:
                    warnings_sink.append(str(e))
            except Exception as e:
                app_logger.warning(f"  Failed to install skill '{skill_id}': {e}")
        # Reload so profiles can reference the newly installed skills
        try:
            from trusted_data_agent.skills.manager import get_skill_manager
            get_skill_manager().reload()
        except Exception as e:
            app_logger.warning(f"  Failed to reload skill manager after pack import: {e}")
        return installed

    def _install_skill_from_zip_bytes(self, skill_id: str, zip_bytes: bytes,
                                      pack_name: str | None = None,
                                      owner_user_uuid: str | None = None) -> None:
        """Install a skill from in-memory zip bytes into the user skills directory.

        Parsing goes through the ONE shared serializer (PROP-005): the selection
        order for legacy zips is preserved ({skill_id}.md → first non-SKILL .md →
        SKILL.md with frontmatter stripped), library payloads are extracted with
        zip-slip sanitization, and a progressive payload failing its index
        integrity check is REFUSED loudly (never a silent partial install).
        """
        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills.package import (
            parse_skill_zip, is_progressive, validate_progressive_payload,
        )

        parsed = parse_skill_zip(zip_bytes, skill_id_hint=skill_id)
        for w in parsed.warnings:
            app_logger.warning(f"  Skill '{skill_id}' package: {w}")

        if parsed.content is None:
            app_logger.warning(f"  No markdown content found in skill zip for '{skill_id}', skipping")
            return

        # The platform ships this id: writing it would shadow the builtin (see
        # BuiltinSkillShadowRefused). Checked BEFORE any write so a refusal
        # leaves the state directory untouched.
        if get_skill_manager().is_builtin_skill(skill_id):
            raise BuiltinSkillShadowRefused(
                f"Skill '{skill_id}' is a platform built-in — refusing to install "
                f"the pack's copy over it. The pack's profiles reference the skill "
                f"by id and resolve to the built-in, so nothing is lost; re-export "
                f"the pack from an instance where it is a built-in to drop it."
            )

        manifest_data = dict(parsed.manifest or {})
        manifest_data.pop("export_format_version", None)
        manifest_data.pop("exported_at", None)

        library = None
        if is_progressive(manifest_data):
            errors = validate_progressive_payload(manifest_data, parsed.library_files)
            if errors:
                raise ValueError(
                    f"Progressive skill '{skill_id}' payload failed integrity: {errors}")
            library = parsed.library_files

        skill_manager = get_skill_manager()

        # Cross-owner conflict guard (best-practice step 3): skills are
        # instance-global while packs are per-user, so before this guard one
        # user's pack install could silently overwrite DIFFERENT skill content
        # another user's pack had installed (observed live: a demo account's
        # marketplace fork rewrote the admin-installed policing skill). Now:
        #   same content            → allowed (idempotent; provenance appends)
        #   same recorded owner     → allowed (their own republish)
        #   owner unknown (legacy)  → allowed (the version archive still
        #                             snapshots what is replaced)
        #   different owner + diff  → REFUSED loudly; the caller surfaces it
        #                             as an import warning. Never a silent
        #                             clobber across a user boundary.
        existing_manifest = (getattr(skill_manager, "manifests", {}) or {}).get(skill_id)
        if existing_manifest is not None and owner_user_uuid:
            try:
                from trusted_data_agent.skills import versions as _versions
                prev_origin = ((existing_manifest.get("uderia") or {})
                               .get("pack_origin") or {})
                prev_owner = prev_origin.get("owner_user_uuid")
                if prev_owner and prev_owner != owner_user_uuid:
                    existing_dir = skill_manager.skill_dirs.get(skill_id)
                    live = _versions.live_skill_hash(Path(existing_dir)) if existing_dir else None
                    incoming = _versions.skill_content_hash(parsed.content, library)
                    if live is not None and live != incoming:
                        raise SkillOwnershipConflict(
                            f"Skill '{skill_id}' holds different content installed by "
                            f"another user's pack ('{prev_origin.get('pack_name')}') — "
                            f"refusing to overwrite it. The pack's other resources "
                            f"install normally; resolve the skill conflict explicitly."
                        )
            except SkillOwnershipConflict:
                raise
            except Exception as e:
                app_logger.warning(
                    f"  Skill '{skill_id}': conflict guard degraded ({e}) — proceeding")

        # Pack provenance stamp: a pack-delivered skill was previously
        # indistinguishable from a hand-authored one, and survived uninstall
        # untraceably. Stamped on the skill's OWN manifest (the `uderia` block
        # survives save_skill's whitelist — freeze gate B2) so it outlives the
        # pack row, which is exactly the orphan case. Trust-neutral: the skill
        # hash covers the markdown + library, never skill.json.
        #
        # Two layers (best-practice step 1): `pack_origin` = the CURRENT
        # pointer (back-compat: orphan marking and every existing reader), and
        # `pack_history` = the APPEND-ONLY event log — provenance is history,
        # and history does not get overwritten by the next writer.
        if pack_name:
            _ud = manifest_data.setdefault("uderia", {})
            if isinstance(_ud, dict):
                _event = {
                    "pack_name": pack_name,
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                }
                if owner_user_uuid:
                    _event["owner_user_uuid"] = owner_user_uuid
                _ud["pack_origin"] = dict(_event)  # fresh install clears orphaned_at
                _prev_ud = ((existing_manifest or {}).get("uderia") or {}) \
                    if isinstance(existing_manifest, dict) else {}
                _history = list(_prev_ud.get("pack_history") or [])
                if not _history and _prev_ud.get("pack_origin"):
                    # Backfill: a pre-history stamp becomes the first event.
                    _history.append(dict(_prev_ud["pack_origin"]))
                _history.append(_event)
                _ud["pack_history"] = _history[-20:]  # bounded, oldest dropped

        skill_manager.save_skill(skill_id, parsed.content, manifest_data,
                                 library_files=library)
        app_logger.info(
            f"  Installed skill '{skill_id}' from agent pack"
            + (f" ({len(library)} library files)" if library else ""))

    @staticmethod
    def _mcp_name_map(user_uuid: str) -> dict:
        """Case-insensitive name → id map of the user's configured MCP servers.

        Lets a manifest profile's `mcpServerName` auto-resolve against a server
        the importing user already has (e.g. a pack expert referencing
        "Google Search" binds to the installed server of that name). Fail-open:
        any error yields an empty map and the import proceeds unbound.
        """
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            return {
                (s.get("name") or "").strip().lower(): s.get("id")
                for s in get_config_manager().get_mcp_servers(user_uuid)
                if s.get("name") and s.get("id")
            }
        except Exception:
            return {}

    @staticmethod
    def _normalize_mcp_bindings(user_uuid: str, bindings: dict | None) -> tuple[dict, list[str]]:
        """Validate an explicit per-agent MCP binding map ``{profile_tag: server_id}``.

        Keys are manifest profile TAGS — the one identity stable across import, fork
        and subscribe. Values must be servers the TARGET user owns; any other id is
        returned in the rejected list (second element) so the caller can refuse, and
        is never written onto a profile. ``None``/empty → ``({}, [])``. A server
        lookup failure yields an empty map (fail-open, the import proceeds unbound).
        """
        if not bindings or not isinstance(bindings, dict):
            return {}, []
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            owned = {
                s.get("id") for s in get_config_manager().get_mcp_servers(user_uuid)
                if s.get("id")
            }
        except Exception:
            return {}, []
        valid: dict = {}
        rejected: list[str] = []
        for tag, sid in bindings.items():
            t = str(tag or "").strip()
            if not t:
                continue
            if isinstance(sid, dict):
                # A multi-server agent: {"primary": id, "<alias>": id, ...}. Every id is
                # checked; one foreign id rejects the row (named by tag, then alias).
                row: dict = {}
                for slot, v in sid.items():
                    slot = str(slot or "").strip()
                    if not slot or not v:
                        continue
                    if v in owned:
                        row[slot] = v
                    else:
                        rejected.append(t if slot == "primary" else f"{t}:{slot}")
                if row:
                    valid[t] = row
            elif sid and sid in owned:
                valid[t] = sid
            else:
                rejected.append(t)
        return valid, rejected

    @staticmethod
    def _remap_bindings(mcp_tag_bindings: dict | None, tag_remap: dict | None) -> dict:
        """Re-key an explicit binding map through a tag remap (expand: TAG → TAG2).

        A binding for a tag the remap does not touch is kept as is; the remap is
        applied exactly once (a chained TAG→TAG2→TAG3 is not a thing here).
        """
        if not mcp_tag_bindings:
            return {}
        remap = tag_remap or {}
        return {remap.get(tag, tag): sid for tag, sid in mcp_tag_bindings.items()}

    @staticmethod
    def _tag_binding_raw(mcp_tag_bindings: dict | None, tag: str | None):
        """The explicit binding value for a manifest tag (exact, then case-insensitive) —
        a server id, or ``{"primary": id, "<alias>": id}`` for a multi-server agent."""
        if not mcp_tag_bindings or not tag:
            return None
        t = str(tag).strip()
        hit = mcp_tag_bindings.get(t)
        if hit:
            return hit
        return next((v for k, v in mcp_tag_bindings.items() if k.lower() == t.lower()), None)

    @classmethod
    def _tag_binding(cls, mcp_tag_bindings: dict | None, tag: str | None) -> str | None:
        """The explicit binding for a tag's FIRST server (the one with plain tool names)."""
        hit = cls._tag_binding_raw(mcp_tag_bindings, tag)
        if isinstance(hit, dict):
            return hit.get("primary") or None
        return hit or None

    @classmethod
    def _tag_alias_bindings(cls, mcp_tag_bindings: dict | None, tag: str | None) -> dict:
        """The explicit bindings for a tag's ADDITIONAL servers, keyed by alias."""
        hit = cls._tag_binding_raw(mcp_tag_bindings, tag)
        if not isinstance(hit, dict):
            return {}
        return {k: v for k, v in hit.items() if k != "primary" and v}

    @staticmethod
    def profile_needs_mcp(prof: dict) -> bool:
        """Does this manifest profile need an MCP server binding at all?"""
        return bool(
            prof.get("requires_mcp")
            or prof.get("profile_type") == "tool_enabled"
            or prof.get("useMcpTools")
            or (prof.get("mcpServerName") or "").strip()
        )

    @classmethod
    def pack_mcp_requirements(
        cls, profiles: list[dict], name_map: dict, tag_bindings: dict | None = None,
    ) -> list[dict]:
        """One row per agent that needs an MCP server — what the binding dialog renders.

        Each row carries the agent's manifest server name (the dialog's default), the
        id it currently resolves to on this instance (explicit binding first, then the
        case-insensitive name match, else ``None``) and how it resolved. Pure.
        """
        rows: list[dict] = []
        for prof in profiles or []:
            if not cls.profile_needs_mcp(prof):
                continue
            tag = prof.get("tag")
            wanted = (prof.get("mcpServerName") or "").strip()
            explicit = cls._tag_binding(tag_bindings, tag)
            by_name = (name_map or {}).get(wanted.lower()) if wanted else None
            row = {
                "tag": tag,
                "name": prof.get("name") or tag,
                "profile_type": prof.get("profile_type"),
                "mcp_server_name": wanted or None,
                "resolved_mcp_server_id": explicit or by_name,
                "resolution": "binding" if explicit else ("name" if by_name else None),
            }
            extra = cls._manifest_additional_rows(prof, name_map, tag_bindings)
            if extra:
                row["additional_servers"] = extra
            rows.append(row)
        return rows

    @classmethod
    def _manifest_additional_rows(cls, prof: dict, name_map: dict,
                                  tag_bindings: dict | None) -> list[dict]:
        """One row per ADDITIONAL server a manifest agent carries (``mcpServers`` entries
        with an alias) — same shape as the agent row. Empty for a single-server agent."""
        entries = prof.get("mcpServers")
        if not isinstance(entries, list):
            return []
        binds = cls._tag_alias_bindings(tag_bindings, prof.get("tag"))
        out = []
        for e in entries:
            if not isinstance(e, dict) or not e.get("alias"):
                continue
            alias = str(e["alias"])
            wanted = (e.get("name") or "").strip()
            explicit = binds.get(alias)
            by_name = (name_map or {}).get(wanted.lower()) if wanted else None
            out.append({
                "alias": alias,
                "mcp_server_name": wanted or None,
                "resolved_mcp_server_id": explicit or by_name,
                "resolution": "binding" if explicit else ("name" if by_name else None),
            })
        return out

    def read_manifest_profiles(self, zip_path: Path) -> tuple[dict, list[dict]]:
        """Read + validate a pack's manifest WITHOUT importing (v1.0 normalised to v1.1)."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
        errors = self.validate_manifest(manifest)
        if errors:
            raise ValueError(f"Invalid manifest: {'; '.join(errors)}")
        if manifest.get("format_version") == "1.0":
            manifest = self._normalize_v10_to_v11(manifest)
        return manifest, list(manifest.get("profiles") or [])

    def _build_profile(
        self,
        prof: dict,
        ref_to_collection_id: dict,
        llm_config_id: str,
        mcp_server_id: str | None,
        tag_to_profile_id: dict | None = None,
        mcp_name_to_id: dict | None = None,
        warnings: list | None = None,
        mcp_tag_bindings: dict | None = None,
    ) -> dict:
        """Build a profile data dict from a v1.1 manifest profile entry.

        Handles all 4 profile types: rag_focused, tool_enabled, llm_only, genie.

        MCP binding precedence (per profile): an EXPLICIT per-agent binding
        (`mcp_tag_bindings[tag]`, the consumer's own choice in the binding dialog)
        wins over everything; else a manifest `mcpServerName` that matches one of
        the importing user's configured servers (case-insensitive, via
        `mcp_name_to_id`) wins over the pack-level `mcp_server_id`; the pack-level
        id remains the fallback. An unresolvable name is reported into `warnings`
        (when provided) — never a hard failure.
        """
        profile_id = f"profile-{uuid.uuid4()}"
        profile_type = prof["profile_type"]

        # Per-profile MCP server resolution (explicit binding → name reference → pack-level).
        prof_mcp_id = mcp_server_id
        wanted_mcp_name = (prof.get("mcpServerName") or "").strip()
        explicit_mcp_id = self._tag_binding(mcp_tag_bindings, prof.get("tag"))
        if explicit_mcp_id:
            prof_mcp_id = explicit_mcp_id
            app_logger.info(
                f"  @{prof.get('tag')}: MCP server bound explicitly → {explicit_mcp_id}"
            )
        elif wanted_mcp_name and mcp_name_to_id:
            hit = mcp_name_to_id.get(wanted_mcp_name.lower())
            if hit:
                prof_mcp_id = hit
                app_logger.info(
                    f"  @{prof.get('tag')}: resolved MCP server '{wanted_mcp_name}' → {hit}"
                )
        if not explicit_mcp_id and wanted_mcp_name and prof_mcp_id == mcp_server_id and (
            mcp_name_to_id is None or wanted_mcp_name.lower() not in (mcp_name_to_id or {})
        ):
            if warnings is not None:
                warnings.append(
                    f"Profile @{prof.get('tag')} references MCP server '{wanted_mcp_name}', "
                    f"which is not configured on this instance"
                    + (f" — bound to the pack-level MCP server instead." if mcp_server_id
                       else " — imported without an MCP server binding. "
                            "Bind one in the profile editor (AI Stack) to activate its tools.")
                )

        profile_data = {
            "id": profile_id,
            "tag": prof["tag"],
            "name": prof.get("name", prof["tag"]),
            "description": prof.get("description", ""),
            "profile_type": profile_type,
            "llmConfigurationId": llm_config_id,
            "classification_mode": prof.get("classification_mode", "light"),
            "contextWindowTypeId": prof.get("contextWindowTypeId") or "cwt-default-balanced",
            "classification_results": {
                "tools": {},
                "prompts": {},
                "resources": {},
                "last_classified": None,
                "classified_with_mode": None,
            },
        }

        collection_refs = prof.get("collection_refs", [])

        if profile_type == "genie":
            # Build genieConfig with resolved child profile IDs
            genie_config = prof.get("genieConfig", {}).copy()
            child_tags = prof.get("child_tags", [])

            if tag_to_profile_id:
                slave_ids = [tag_to_profile_id[ct] for ct in child_tags if ct in tag_to_profile_id]
                genie_config["slaveProfiles"] = slave_ids

                # Remap slaveProfileSettings keys from profile tags → new profile IDs
                settings_by_tag = genie_config.get("slaveProfileSettings", {})
                if settings_by_tag:
                    genie_config["slaveProfileSettings"] = {
                        tag_to_profile_id[tag]: settings
                        for tag, settings in settings_by_tag.items()
                        if tag in tag_to_profile_id
                    }

            profile_data["genieConfig"] = genie_config

        elif profile_type == "rag_focused":
            # Always get knowledgeConfig from manifest (preserves settings like maxDocs, maxTokens)
            knowledge_config = prof.get("knowledgeConfig", {}).copy()

            # Add collections if collection_refs exist
            if collection_refs:
                collections_list = []
                for cr in collection_refs:
                    collection_info = ref_to_collection_id.get(cr)
                    if collection_info:
                        collections_list.append({
                            "id": collection_info["id"],
                            "name": collection_info["name"],
                        })
                if collections_list:
                    knowledge_config["collections"] = collections_list

            # Add synthesis prompt override if exists
            synthesis_prompt = prof.get("synthesisPromptOverride")
            if synthesis_prompt:
                knowledge_config["synthesisPromptOverride"] = synthesis_prompt

            # Always set knowledgeConfig (even if no collection_refs)
            profile_data["knowledgeConfig"] = knowledge_config

        elif profile_type == "tool_enabled":
            if prof_mcp_id:
                profile_data["mcpServerId"] = prof_mcp_id

            # Link planner collections via ragCollections
            if collection_refs:
                rag_ids = []
                for cr in collection_refs:
                    collection_info = ref_to_collection_id.get(cr)
                    if collection_info:
                        rag_ids.append(collection_info["id"])
                if rag_ids:
                    profile_data["ragCollections"] = rag_ids

        elif profile_type == "llm_only":
            # llm_only profiles can optionally use MCP tools via LangChain
            if prof.get("useMcpTools"):
                profile_data["useMcpTools"] = True
                if prof_mcp_id:
                    profile_data["mcpServerId"] = prof_mcp_id
                elif warnings is not None and not wanted_mcp_name:
                    warnings.append(
                        f"Profile @{prof.get('tag')} enables MCP tools but no MCP server "
                        f"could be bound — bind one in the profile editor (AI Stack)."
                    )
            elif prof_mcp_id and prof.get("mcpServerName"):
                profile_data["mcpServerId"] = prof_mcp_id

        # Pre-qualify tool set — non-empty list prevents auto-init to all MCP tools
        # Applies to both tool_enabled and llm_only (useMcpTools) profiles
        allowed_tools = prof.get("allowed_tools")
        if allowed_tools:
            profile_data["tools"] = allowed_tools

        # A multi-server agent's ADDITIONAL servers (implementation plan stage 2): resolved
        # by explicit alias binding, else by server NAME — never guessed. Only once the first
        # server is bound (an additional server has no meaning without it).
        from trusted_data_agent.core.agent_mcp_servers import (
            ADDITIONAL_KEY as _ADD, primary_of as _first_of,
            resolve_manifest_servers as _resolve_srv)
        if prof.get("mcpServers") and _first_of(profile_data):
            _added, _problems = _resolve_srv(
                prof.get("mcpServers"), _first_of(profile_data), mcp_name_to_id or {},
                alias_bindings=self._tag_alias_bindings(mcp_tag_bindings, prof.get("tag")),
                owned=set((mcp_name_to_id or {}).values()) or None)
            if _added:
                profile_data[_ADD] = _added
            if warnings is not None:
                warnings.extend(f"Profile @{prof.get('tag')}: {p}" for p in _problems)

        # Restore portable profile config through the ONE declarative contract — the single
        # SINK for the three manifest sources (export / fork / v1.0 normalize). Covers
        # skillsConfig, componentConfig, recommenderConfig and the trust posture
        # (grounding / guardrail / honesty / authority). A key absent from the manifest is
        # deliberately NOT written, preserving the update-in-place fail-safe that absence
        # means "leave the existing profile's value alone".
        #
        # The type-specific branches above own the instance-local references — the genie
        # branch resolves child_tags → this instance's profile ids, the rag_focused branch
        # resolves collection_refs → this instance's collection ids. The contract declares
        # those paths (`id_ref_paths` / `local_ref_paths`) and therefore neither carries nor
        # overwrites them, so this call cannot undo that work whichever order it runs in.
        # A hand-maintained exclusion list here used to serve that purpose and covered only
        # knowledgeConfig — which is how genieConfig, added to the registry by the two-hash
        # trust envelope, silently reverted every imported coordinator's experts to the
        # publisher's profile ids.
        _pcc.apply_from_manifest(prof, profile_data)

        # Restore the session primer (e.g. the coordinator's design-session kickoff)
        session_primer = prof.get("session_primer")
        if session_primer:
            profile_data["session_primer"] = session_primer

        return profile_data

    def build_subscribed_profiles(
        self,
        pack_resources: list[dict],
        publisher_user_id: str,
        subscriber_user_id: str,
        llm_config_id: str,
        mcp_server_id: str | None,
        source_installation_id: int,
        pack_published_at: str | None = None,
        mcp_bindings: dict | None = None,
        existing_ids: dict | None = None,
    ) -> list[dict]:
        """Build subscriber-scoped profile dicts from a publisher's pack resources.

        ``mcp_bindings`` — optional explicit per-agent map ``{tag: subscriber server id}``
        (validated against the SUBSCRIBER's servers; a foreign id is refused).

        ``existing_ids`` — ``{tag: profile id}`` of the subscriber's EXISTING copies (a
        sync). A built profile keeps that id instead of a minted one, and because the
        non-genie profiles are built first, a coordinator's ``slaveProfiles`` resolve to
        the preserved ids — every session, assignment and ledger row keyed on them stays
        valid across a sync. Absent (a fresh subscribe) every id is minted.

        Replaces the old ``copy.deepcopy`` approach: every publisher-owned ID
        (LLM config, MCP server, collection IDs, genie child profile IDs, CWT) is
        remapped to values that are valid in the *subscriber's* account.

        Returns profiles in non-genie-first order so callers can add them
        in dependency order.
        """
        from datetime import datetime, timezone
        from trusted_data_agent.core.config_manager import get_config_manager

        config_manager = get_config_manager()

        # ── Validate subscriber LLM config ────────────────────────────────────
        subscriber_llms = config_manager.get_llm_configurations(subscriber_user_id)
        subscriber_llm_ids = {c.get("id") for c in subscriber_llms}
        if llm_config_id not in subscriber_llm_ids:
            raise ValueError(
                f"LLM configuration '{llm_config_id}' not found in subscriber account. "
                "Please select a valid LLM configuration."
            )

        # ── Load publisher profiles ordered non-genie first ───────────────────
        all_resources = [r for r in pack_resources if r["resource_type"] == "profile"]
        publisher_profiles = []
        for res in all_resources:
            p = config_manager.get_profile(res["resource_id"], publisher_user_id)
            if p:
                publisher_profiles.append(p)
            else:
                app_logger.warning(
                    f"build_subscribed_profiles: profile {res['resource_id']} not found "
                    f"in publisher {publisher_user_id} — skipping"
                )

        non_genie = [p for p in publisher_profiles if p.get("profile_type") != "genie"]
        genie_profiles = [p for p in publisher_profiles if p.get("profile_type") == "genie"]

        # ── Build collection ref map from pack collection resources ───────────
        # Subscribe does not copy collections — it creates CollectionSubscription
        # rows pointing at the publisher's source_collection_id.  The subscriber's
        # profiles need the same integer collection IDs so that RAG queries resolve.
        from trusted_data_agent.core.collection_db import CollectionDatabase
        _cdb = CollectionDatabase()
        ref_to_collection_id: dict[str, dict] = {}
        for res in pack_resources:
            if res["resource_type"] != "collection":
                continue
            coll_id = int(res["resource_id"])
            coll_meta = _cdb.get_collection_by_id(coll_id)
            if coll_meta:
                # Use "col-<id>" as a stable ref key
                ref_key = f"col-{coll_id}"
                ref_to_collection_id[ref_key] = {
                    "id": coll_id,
                    "name": coll_meta.get("name", ""),
                }

        # ── Convert live profile → manifest-entry format ──────────────────────
        def _to_manifest_entry(profile: dict, selected_profiles: list[dict]) -> dict:
            """Produce a manifest-style entry compatible with _build_profile()."""
            profile_type = profile.get("profile_type", "llm_only")
            entry: dict = {
                "tag": profile.get("tag"),
                "name": profile.get("name"),
                "description": profile.get("description", ""),
                "profile_type": profile_type,
                "classification_mode": profile.get("classification_mode", "light"),
                "contextWindowTypeId": profile.get("contextWindowTypeId"),
            }

            # collection_refs — map existing integer IDs back to our ref keys
            collection_refs: list[str] = []
            # knowledgeConfig collections
            for ci in profile.get("knowledgeConfig", {}).get("collections", []):
                ref_key = f"col-{ci.get('id')}"
                if ref_key in ref_to_collection_id:
                    collection_refs.append(ref_key)
            # ragCollections
            for rid in profile.get("ragCollections", []):
                ref_key = f"col-{rid}"
                if ref_key in ref_to_collection_id:
                    collection_refs.append(ref_key)
            if collection_refs:
                # deduplicate while preserving order
                seen: set = set()
                entry["collection_refs"] = [r for r in collection_refs if not (r in seen or seen.add(r))]

            if profile_type == "genie":
                child_ids = profile.get("genieConfig", {}).get("slaveProfiles", [])
                child_tags = []
                for cid in child_ids:
                    cp = next((p for p in selected_profiles if p["id"] == cid), None)
                    if cp:
                        child_tags.append(cp.get("tag"))
                entry["child_tags"] = child_tags

                gc = profile.get("genieConfig", {})
                gc_copy = {k: v for k, v in gc.items() if k not in ("slaveProfiles", "slaveProfileSettings")}
                slave_settings = gc.get("slaveProfileSettings", {})
                if slave_settings:
                    settings_by_tag = {}
                    for pid, settings in slave_settings.items():
                        cp = next((p for p in selected_profiles if p["id"] == pid), None)
                        if cp:
                            settings_by_tag[cp.get("tag")] = settings
                    if settings_by_tag:
                        gc_copy["slaveProfileSettings"] = settings_by_tag
                if gc_copy:
                    entry["genieConfig"] = gc_copy

            elif profile_type == "tool_enabled":
                entry["requires_mcp"] = True

            # Portable profile config via the ONE declarative contract (see
            # core/profile_config_contract.py). Instance-local ids — knowledgeConfig's
            # collections and genieConfig's slaveProfiles/slaveProfileSettings — are dropped
            # by the contract and the write merges, so the collection_refs / child_tags and
            # tag-keyed settings resolved above are preserved.
            _pcc.apply_to_manifest(profile, entry)

            if profile.get("useMcpTools"):
                entry["useMcpTools"] = True
            if profile.get("mcpServerName"):
                entry["mcpServerName"] = profile["mcpServerName"]
            elif profile.get("mcpServerId"):
                # Live profiles carry only the publisher-local mcpServerId — stamp the
                # server NAME so the subscriber-side import can auto-resolve it against
                # the subscriber's own servers by name.
                try:
                    _srv = next((s for s in config_manager.get_mcp_servers(publisher_user_id)
                                 if s.get("id") == profile["mcpServerId"]), None)
                    if _srv and _srv.get("name"):
                        entry["mcpServerName"] = _srv["name"]
                except Exception:
                    pass  # best-effort — never fail a subscribe on the name stamp
            _stamp_manifest_servers(entry, profile, publisher_user_id)

            allowed_tools = profile.get("tools") or profile.get("allowed_tools")
            if allowed_tools:
                entry["allowed_tools"] = allowed_tools

            synthesis_prompt = profile.get("knowledgeConfig", {}).get("synthesisPromptOverride")
            if synthesis_prompt:
                entry["synthesisPromptOverride"] = synthesis_prompt

            return entry

        # ── Two-pass profile building (non-genie first so IDs are available) ──
        tag_to_profile_id: dict[str, str] = {}
        subscribed_at = datetime.now(timezone.utc).isoformat()
        result_profiles: list[dict] = []
        mcp_name_to_id = self._mcp_name_map(subscriber_user_id)
        mcp_tag_bindings, _rejected = self._normalize_mcp_bindings(subscriber_user_id, mcp_bindings)
        if _rejected:
            raise ValueError(
                "mcp_bindings names MCP server(s) that are not configured for this user "
                f"(@{', @'.join(_rejected)})."
            )

        for publisher_profile in non_genie + genie_profiles:
            manifest_entry = _to_manifest_entry(publisher_profile, publisher_profiles)
            profile_data = self._build_profile(
                manifest_entry, ref_to_collection_id, llm_config_id, mcp_server_id,
                tag_to_profile_id=tag_to_profile_id,
                mcp_name_to_id=mcp_name_to_id,
                mcp_tag_bindings=mcp_tag_bindings,
            )

            # ── Validate / clear contextWindowTypeId ─────────────────────────
            cwt_id = profile_data.get("contextWindowTypeId")
            if cwt_id and cwt_id != "cwt-default-balanced":
                valid_cwt = config_manager.get_context_window_type(cwt_id, subscriber_user_id)
                if not valid_cwt:
                    app_logger.warning(
                        f"build_subscribed_profiles: CWT '{cwt_id}' not found for subscriber "
                        f"{subscriber_user_id} — reset to cwt-default-balanced"
                    )
                    profile_data["contextWindowTypeId"] = "cwt-default-balanced"

            # A sync keeps the copy's id (the children were built first, so the
            # coordinator above already resolved its slaveProfiles to preserved ids).
            _kept = (existing_ids or {}).get(publisher_profile.get("tag", ""))
            if _kept:
                profile_data["id"] = _kept

            # ── Subscribe metadata ────────────────────────────────────────────
            profile_data["is_subscribed"] = True
            profile_data["source_pack_installation_id"] = source_installation_id
            profile_data["subscribed_at"] = subscribed_at
            if pack_published_at:
                profile_data["pack_published_at"] = pack_published_at


            tag_to_profile_id[publisher_profile.get("tag", "")] = profile_data["id"]
            result_profiles.append(profile_data)
            app_logger.info(
                f"build_subscribed_profiles: built @{profile_data['tag']} "
                f"(id={profile_data['id']}, type={profile_data['profile_type']})"
            )

        return result_profiles

    async def materialize_subscription_members(
        self, *, subscriber_uuid: str, publisher_uuid: str, publisher_profile_ids: list,
        pack_name: str, pack_listing_id: str, tag_to_profile_id: dict,
        db_path: str | None = None, update: bool = False,
    ) -> dict:
        """Give a pack SUBSCRIBER the members a fork gets and subscribe never did:
        knowledge-graph COPIES (snapshot-with-sync, the KG marketplace's own model — a
        graph cannot be referenced across accounts, every read is user-scoped) and the
        pack's skills recorded in the ledger. P1 of PACK_SUBSCRIBE_COMPLETION.md.

        The source of truth is the publisher's LIVE installation, exported exactly as a
        fork exports it, so the manifest also carries ``member_trust`` — the claims
        ``_reinstate_member_trust`` re-applies once the ledger names the copies.

        Returns ``{manifest, kg_ids, kg_names, skill_ids, kgs_created, warnings, zip_path}``
        (+ ``kgs_rebuilt``, ``kgs_unchanged``, ``kgs_removed`` when ``update``).
        A graph whose profile is not among the subscriber's is skipped and SAID; a
        materialization failure is a warning, never a lost subscription — the profiles
        already exist and the caller decides what to report.

        ``update`` (a SYNC, P2): the subscriber's existing ``agent_pack`` copies of this
        listing are matched to the manifest by the publisher's ``kg_id`` — an unchanged
        export hash is kept as it is (attestation and activation untouched), a changed one
        is REBUILT in place (``rebuild_copy``: same local kg_id, activation preserved,
        the stamp re-applied), a manifest graph with no copy is materialized, and a copy
        whose graph the pack no longer ships is deleted. The KG marketplace's own fan-out
        rule, applied to the pack's graphs.
        """
        import hashlib as _hashlib
        import json as _json
        import sqlite3 as _sqlite3
        import zipfile as _zipfile
        from trusted_data_agent.core import kg_subscription as _kgsub

        out = {"manifest": {}, "kg_ids": [], "kg_names": [], "skill_ids": [],
               "kgs_created": 0, "warnings": [], "zip_path": None}
        if update:
            out.update({"kgs_rebuilt": 0, "kgs_unchanged": 0, "kgs_removed": 0})
        zip_path = await self.export_pack(
            profile_ids=list(publisher_profile_ids), user_uuid=publisher_uuid,
            pack_name=pack_name, pack_description=f"Subscribed from {pack_name}")
        out["zip_path"] = str(zip_path)
        db = str(db_path or self.db_path)
        existing_by_source: dict = {}
        if update:
            _c = sqlite_util.connect(db)
            _c.row_factory = _sqlite3.Row
            try:
                for _r in _c.execute(
                        "SELECT * FROM kg_subscriptions WHERE user_uuid = ? AND source_kind = ? "
                        "AND source_marketplace_id = ?",
                        (subscriber_uuid, _kgsub.SOURCE_AGENT_PACK, str(pack_listing_id))).fetchall():
                    if _r["source_kg_id"]:
                        existing_by_source[str(_r["source_kg_id"])] = _r
            finally:
                _c.close()
        seen_sources: set = set()
        with _zipfile.ZipFile(zip_path) as z:
            manifest = _json.loads(z.read("manifest.json").decode("utf-8"))
            out["manifest"] = manifest
            out["skill_ids"] = [s.get("skill_id") or s.get("id")
                                for s in (manifest.get("skills") or []) if isinstance(s, dict)
                                and (s.get("skill_id") or s.get("id"))]
            for entry in manifest.get("knowledge_graphs") or []:
                tag = entry.get("profile_tag") or entry.get("owner_profile_tag")
                target = tag_to_profile_id.get(tag) or tag_to_profile_id.get(entry.get("owner_profile_tag"))
                if not target:
                    out["warnings"].append(
                        f"knowledge graph '{entry.get('name')}' is for @{tag}, which this "
                        f"subscription does not carry — not materialized")
                    continue
                try:
                    raw = z.read(entry["file"])
                except KeyError:
                    out["warnings"].append(f"knowledge graph file missing in the export: {entry.get('file')}")
                    continue
                kg_data = _json.loads(raw.decode("utf-8"))
                export_data = {
                    "kg_name": entry.get("name") or kg_data.get("name") or "Knowledge graph",
                    "kg_description": entry.get("description") or kg_data.get("description") or "",
                    "kg_database_name": entry.get("database_name") or kg_data.get("database_name") or "",
                    "entities": kg_data.get("entities", []) or [],
                    "relationships": kg_data.get("relationships", []) or [],
                    "trust": None,   # reinstated by _reinstate_member_trust, by content
                    # A5: the extension claims — reinstated by the same step, after the base
                    "trust_extensions": kg_data.get("trust_extensions") or entry.get("trust_extensions"),
                }
                export_hash = _hashlib.sha256(raw).hexdigest()
                source_kg_id = str(entry.get("kg_id") or kg_data.get("kg_id") or "")
                existing = existing_by_source.get(source_kg_id) if (update and source_kg_id) else None
                if existing is not None:
                    seen_sources.add(source_kg_id)
                    if existing["last_synced_export_hash"] == export_hash:
                        out["kgs_unchanged"] += 1
                        out["kg_ids"].append(existing["local_kg_id"])
                        out["kg_names"].append(self._kg_name(db, existing["local_kg_id"]) or export_data["kg_name"])
                        continue
                    conn = sqlite_util.connect(db)
                    conn.row_factory = _sqlite3.Row
                    try:
                        _kgsub.rebuild_copy(conn, existing, export_data, export_hash)
                        conn.commit()
                    except Exception as e:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        _kgsub.record_sync_error(conn, existing["id"], str(e))
                        out["warnings"].append(f"knowledge graph '{entry.get('name')}' could not be rebuilt: {e}")
                        app_logger.warning(f"sync: KG '{entry.get('name')}' not rebuilt: {e}", exc_info=True)
                        # the previous copy still stands — the ledger keeps naming it
                        out["kg_ids"].append(existing["local_kg_id"])
                        out["kg_names"].append(self._kg_name(db, existing["local_kg_id"]) or export_data["kg_name"])
                        continue
                    finally:
                        conn.close()
                    try:
                        from components.builtin.knowledge_graph.graph_store import GraphStore as _GS
                        self._stamp_pack_kg_validation(
                            _GS(existing["target_profile_id"], subscriber_uuid,
                                kg_id=existing["local_kg_id"], allow_subscribed_write=True),
                            kg_data)
                    except Exception as e:
                        app_logger.debug(f"sync: validation stamp skipped for {existing['local_kg_id']}: {e}")
                    out["kgs_rebuilt"] += 1
                    out["kg_ids"].append(existing["local_kg_id"])
                    out["kg_names"].append(self._kg_name(db, existing["local_kg_id"]) or export_data["kg_name"])
                    app_logger.info(f"  sync: KG '{export_data['kg_name']}' rebuilt in place "
                                    f"(local {existing['local_kg_id']})")
                    continue
                conn = sqlite_util.connect(db)
                try:
                    made = _kgsub.materialize_copy(
                        conn, db_path=db, user_uuid=subscriber_uuid, target_profile_id=target,
                        source_kg_id=str(entry.get("kg_id") or kg_data.get("kg_id") or ""),
                        source_marketplace_id=str(pack_listing_id),
                        source_kind=_kgsub.SOURCE_AGENT_PACK,
                        export_data=export_data,
                        export_hash=export_hash,
                        fallback_name=entry.get("name") or "")
                    # every other pack profile the publisher assigned this graph to, on the
                    # subscriber's side — inactive unless that profile has no active graph
                    for ap in entry.get("assigned_profiles") or []:
                        atag = (ap or {}).get("tag") if isinstance(ap, dict) else None
                        apid = tag_to_profile_id.get(atag) if atag else None
                        if not apid or apid == target:
                            continue
                        has_active = conn.execute(
                            "SELECT 1 FROM kg_profile_assignments WHERE assigned_profile_id = ? "
                            "AND user_uuid = ? AND is_active = 1 LIMIT 1", (apid, subscriber_uuid)
                        ).fetchone() is not None
                        conn.execute(
                            "INSERT OR IGNORE INTO kg_profile_assignments "
                            "(kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (made["local_kg_id"], target, apid, subscriber_uuid, 0 if has_active else 1))
                    conn.commit()
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    out["warnings"].append(f"knowledge graph '{entry.get('name')}' could not be materialized: {e}")
                    app_logger.warning(f"subscribe: KG '{entry.get('name')}' not materialized: {e}", exc_info=True)
                    continue
                finally:
                    conn.close()
                # the publisher's source-validation stamp follows the content (the fork's rule)
                try:
                    from components.builtin.knowledge_graph.graph_store import GraphStore as _GS
                    self._stamp_pack_kg_validation(
                        _GS(target, subscriber_uuid, kg_id=made["local_kg_id"], allow_subscribed_write=True),
                        kg_data)
                except Exception as e:
                    app_logger.debug(f"subscribe: validation stamp skipped for {made['local_kg_id']}: {e}")
                out["kg_ids"].append(made["local_kg_id"])
                out["kg_names"].append(made["kg_name"])
                out["kgs_created"] += 1
                app_logger.info(f"  subscribe: KG '{made['kg_name']}' -> @{tag} "
                                f"(local {made['local_kg_id']}, active={made['active']})")
        # a copy of a graph the pack no longer ships goes — a sync is the one place the
        # subscriber's set is brought in line with the publisher's
        if update:
            for _src, _row in existing_by_source.items():
                if _src in seen_sources:
                    continue
                conn = sqlite_util.connect(db)
                conn.row_factory = _sqlite3.Row
                try:
                    _kgsub.delete_copy(conn, _row)
                    conn.commit()
                    out["kgs_removed"] += 1
                    app_logger.info(f"  sync: KG copy {_row['local_kg_id']} removed (the pack no longer ships it)")
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    out["warnings"].append(f"a knowledge-graph copy the pack no longer ships could not be removed: {e}")
                finally:
                    conn.close()
        return out

    @staticmethod
    def _kg_name(db_path: str, kg_id: str) -> str | None:
        """The stored name of a local graph (for the ledger row of a kept copy)."""
        try:
            c = sqlite_util.connect(str(db_path))
            try:
                r = c.execute("SELECT name FROM kg_metadata WHERE kg_id = ?", (kg_id,)).fetchone()
                return r[0] if r else None
            finally:
                c.close()
        except Exception:
            return None

    def _reinstate_member_trust(self, manifest: dict, installation_id) -> int:
        """Reinstate each pack member's captured attestation onto its materialised copy,
        matched by (kind, content hash) — the honesty gate accepts a copy only if its
        content is byte-identical to what was signed. Returns the number reinstated.

        ONE step for the fresh install AND update_in_place. It lived on the fresh path
        only, so every in-place update refreshed the ontology and the corpus and left the
        consumer's OLD attestations pointing at the previous content: four pack agents on
        a consumer read `changed` after each update although the publisher had re-signed
        the very content they now held. Never raises."""
        n = 0
        try:
            _member_claims = manifest.get("member_trust") or []
            if not _member_claims:
                return 0
            from trusted_data_agent.core.trust import portability as _trust_portability
            from trusted_data_agent.core.trust import pack as _trust_pack
            from trusted_data_agent.core.trust import content as _trust_content
            _by_kv = {(c.get("kind"), c.get("version_hash")): c for c in _member_claims if c}
            _refs, _own = _trust_pack.member_refs(installation_id)
            for _ref in (_refs or []):
                _c = _by_kv.get((_ref.kind, _ref.live_hash))
                if _c:
                    # an agent member is the installing account's agent (owner-scoped)
                    _trust_portability.reinstate(_ref.kind, _ref.artifact_id, _c, _ref.live_hash,
                                                 owner_uuid=_own)
                    n += 1
            # skills preserve their id (not in member_refs) — reinstate by content match
            for _s in (manifest.get("skills") or []):
                _sid = _s.get("skill_id") or _s.get("id")
                if _sid:
                    _lh = _trust_content.live_hash("skill", _sid)
                    _c = _by_kv.get(("skill", _lh))
                    if _c:
                        _trust_portability.reinstate("skill", _sid, _c, _lh)
                        n += 1
            n += self._reinstate_extension_claims(manifest, _refs or [])
        except Exception as _te:
            app_logger.debug(f"trust: member reinstate skipped: {_te}")
        return n

    @staticmethod
    def _reinstate_extension_claims(manifest: dict, refs) -> int:
        """PROP-008 A5: an extension's signature travels in the manifest's KG entry
        (`trust_extensions`) and reinstates onto the consumer's graph AFTER the base's
        own claim did — its content binds the base's attested hash. Which local graph
        an entry became is not recorded in the ledger by publisher kg_id, so every
        entry's claims are offered to every pack ontology; the honesty gate (content
        hash) lets exactly the right ones take and nothing else."""
        n = 0
        try:
            from trusted_data_agent.core.trust import extensions as _trust_ext
        except Exception:  # noqa: BLE001
            return 0
        claims = [e.get("trust_extensions") for e in (manifest.get("knowledge_graphs") or [])
                  if isinstance(e, dict) and e.get("trust_extensions")]
        if not claims:
            return 0
        for _ref in refs:
            if getattr(_ref, "kind", None) != "ontology":
                continue
            for c in claims:
                try:
                    n += sum(1 for ok in _trust_ext.reinstate_claims(_ref.artifact_id, c).values() if ok)
                except Exception:  # noqa: BLE001
                    pass
        return n

    def _find_own_registration(self, user_uuid: str, pack_name: str) -> int | None:
        """This owner's existing REGISTRATION of ``pack_name``, if any.

        Scoped to `_registration_origin == "created"` so a pack the user IMPORTED or
        SUBSCRIBED to under the same name is never hijacked by a registration — those
        are different acts with different ownership semantics. Newest wins; any older
        duplicates (from before registration became idempotent) are NAMED in the log
        rather than silently absorbed, because deleting a ledger row can invalidate a
        marketplace listing that points at it.
        """
        conn = sqlite_util.connect(self.db_path)
        try:
            rows = conn.execute("""
                SELECT id FROM agent_pack_installations
                 WHERE owner_user_id = ? AND name = ?
                   AND json_extract(manifest_json, '$._registration_origin') = 'created'
                 ORDER BY id DESC
            """, (user_uuid, pack_name)).fetchall()
        except Exception as e:  # noqa: BLE001 — a lookup failure must not block a build
            app_logger.warning(f"agent pack: could not look up an existing registration of "
                           f"'{pack_name}': {e} — recording a new installation")
            return None
        finally:
            conn.close()
        if not rows:
            return None
        keep = int(rows[0][0])
        if len(rows) > 1:
            app_logger.warning(
                f"agent pack '{pack_name}': {len(rows)} registrations exist for this owner "
                f"({', '.join(str(int(r[0])) for r in rows)}); updating {keep}. The others are "
                f"superseded duplicates — maintenance/dedupe_pack_registrations.py removes them."
            )
        return keep

    @staticmethod
    def _manifest_kg_members(manifest: dict) -> tuple[list[str], list[str]]:
        """The knowledge graphs a pack SHIPS, read from its own manifest.

        One derivation, so the publisher's ledger and every consumer's ledger name
        the same member set. Ids differ between the two sides; the composition hash
        is taken over each member's CONTENT, which is what makes them agree.
        """
        ids: list[str] = []
        names: list[str] = []
        for entry in (manifest or {}).get("knowledge_graphs") or []:
            if not isinstance(entry, dict):
                continue
            kid = entry.get("kg_id")
            if not kid or str(kid) in ids:
                continue
            ids.append(str(kid))
            names.append(entry.get("name") or str(kid))
        return ids, names

    def _record_installation(
        self,
        manifest: dict,
        coordinator_tag: str | None,
        coordinator_profile_id: str | None,
        pack_type: str,
        profile_ids: list[str],
        profile_tags: list[str],
        profile_roles: list[str],
        collection_ids: list[int],
        collection_refs: list[str],
        user_uuid: str,
        is_owned: bool = True,
        kg_ids: list[str] | None = None,
        kg_names: list[str] | None = None,
        skill_ids: list[str] | None = None,
        replace_installation_id: int | None = None,
    ) -> int:
        """Record the agent pack installation in the database.

        ``replace_installation_id`` rewrites an EXISTING ledger row in place — its
        id, and therefore every pointer at it (``marketplace_agent_packs
        .source_installation_id``, a subscriber's ``_source_installation_id``),
        survives. Used by the publisher-registration path, where re-registering
        the same pack is an update, never a second installation.
        """
        conn = sqlite_util.connect(self.db_path)
        try:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()

            if replace_installation_id is not None:
                cursor.execute("""
                    UPDATE agent_pack_installations
                       SET name = ?, description = ?, version = ?, author = ?,
                           coordinator_tag = ?, coordinator_profile_id = ?, pack_type = ?,
                           installed_at = ?, manifest_json = ?
                     WHERE id = ? AND owner_user_id = ?
                """, (
                    manifest["name"],
                    manifest.get("description", ""),
                    manifest.get("version", "1.0.0"),
                    manifest.get("author", ""),
                    coordinator_tag,
                    coordinator_profile_id,
                    pack_type,
                    now,
                    json.dumps(manifest),
                    int(replace_installation_id),
                    user_uuid,
                ))
                installation_id = int(replace_installation_id)
                # The resource rows are rebuilt below, so the ledger states what
                # the pack contains NOW — a member dropped from the pack must not
                # linger as a claim on a profile the pack no longer ships.
                cursor.execute("DELETE FROM agent_pack_resources WHERE pack_installation_id = ?",
                               (installation_id,))
            else:
                cursor.execute("""
                    INSERT INTO agent_pack_installations
                    (name, description, version, author, coordinator_tag,
                     coordinator_profile_id, pack_type, owner_user_id, installed_at, manifest_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    manifest["name"],
                    manifest.get("description", ""),
                    manifest.get("version", "1.0.0"),
                    manifest.get("author", ""),
                    coordinator_tag,
                    coordinator_profile_id,
                    pack_type,
                    user_uuid,
                    now,
                    json.dumps(manifest),
                ))
                installation_id = cursor.lastrowid

            # Record profiles
            owned_int = 1 if is_owned else 0
            for pid, tag, role in zip(profile_ids, profile_tags, profile_roles):
                cursor.execute("""
                    INSERT INTO agent_pack_resources
                    (pack_installation_id, resource_type, resource_id, resource_tag, resource_role, is_owned)
                    VALUES (?, 'profile', ?, ?, ?, ?)
                """, (installation_id, pid, tag, role, owned_int))

            # Record collections
            for coll_id, ref in zip(collection_ids, collection_refs):
                cursor.execute("""
                    INSERT INTO agent_pack_resources
                    (pack_installation_id, resource_type, resource_id, resource_tag, resource_role, is_owned)
                    VALUES (?, 'collection', ?, ?, 'collection', ?)
                """, (installation_id, str(coll_id), ref, owned_int))

            # Record knowledge graphs
            for kg_id, kg_name in zip(kg_ids or [], kg_names or []):
                cursor.execute("""
                    INSERT INTO agent_pack_resources
                    (pack_installation_id, resource_type, resource_id, resource_tag, resource_role, is_owned)
                    VALUES (?, 'knowledge_graph', ?, ?, 'knowledge_graph', ?)
                """, (installation_id, kg_id, kg_name, owned_int))

            # Record skills — a pack-delivered skill was previously untracked
            # entirely: no provenance to show, and uninstall could not see it.
            # Skills are KEPT on uninstall (they may be edited or referenced by
            # non-pack profiles) and marked ORPHANED instead.
            for _sid in (skill_ids or []):
                cursor.execute("""
                    INSERT INTO agent_pack_resources
                    (pack_installation_id, resource_type, resource_id, resource_tag, resource_role, is_owned)
                    VALUES (?, 'skill', ?, ?, 'skill', ?)
                """, (installation_id, _sid, _sid, owned_int))

            conn.commit()
            return installation_id
        finally:
            conn.close()

    # ── Update-in-place (preserves IDs for marketplace subscribers) ────────────

    def _upsert_pack_profile(
        self, config_manager, prof: dict, profile_data: dict,
        ledger_pid: str | None, user_uuid: str, tag_to_profile_id: dict,
    ) -> None:
        """Write one pack profile, reconciling reality TO the pack's ledger.

        `ledger_pid` is what `agent_pack_resources` says this tag's profile ID is.
        Three states, and only the first two used to be handled:

          - live profile exists      -> update in place, ID preserved
          - no ledger entry          -> genuinely new, mint an ID
          - ledger entry, no profile -> RE-CREATE at the ledger's ID

        The third state is a pack whose profiles were lost out-of-band while the
        installation and its resources survived. `update_profile` returns False for a
        profile it cannot find and the old call sites discarded that return, so the
        write silently did nothing: the tag stayed missing, and every row that still
        referenced the ID (agent_pack_resources, kg_metadata.profile_id,
        kg_profile_assignments) stayed pointed at a profile that no longer existed.
        Re-creating at the SAME ID is what keeps those references valid — minting a
        fresh one would orphan the pack's knowledge graph and collection.
        """
        tag = prof["tag"]
        from trusted_data_agent.core import profile_config_contract as _pcc
        if ledger_pid:
            profile_data["id"] = ledger_pid
            # Read the live type BEFORE the merge: a config manager may hand back the
            # very dict the merge then updates, and the comparison would see the new type.
            try:
                _live = dict(config_manager.get_profile(ledger_pid, user_uuid) or {})
            except Exception:
                _live = {}
            live_type = _live.get("profile_type")
            _live_keys = set(_live.keys())
            # The classification snapshot is DEPLOYMENT-LOCAL state: it is the reachable
            # tool/prompt catalog the deployment half of the trust envelope hashes, and
            # `_build_profile` emits it EMPTY (a new profile has no snapshot yet). Merged
            # over a consumer's live profile, that empty block wiped the catalog on every
            # in-place update — three signed agents on the reference consumer read
            # `changed` ("tools removed … +55"), and one attested over the emptied
            # snapshot read `trusted` for a catalog it did not have (found live,
            # 2026-09-07). A pack ships what an agent IS, never what this instance can
            # reach; the snapshot stays the instance's.
            _writable = set(_pcc.import_writable_keys())
            _dropped = [k for k in profile_data if k not in _writable]
            profile_data = {k: v for k, v in profile_data.items() if k in _writable}
            if any(k not in _pcc.DEPLOYMENT_LOCAL_KEYS for k in _dropped):
                # A key `_build_profile` writes that the contract does not know is a bug
                # at the source, not something to carry quietly.
                app_logger.warning(
                    f"  @{tag}: import wrote keys outside the contract's import "
                    f"boundary, dropped from the in-place merge: "
                    f"{[k for k in _dropped if k not in _pcc.DEPLOYMENT_LOCAL_KEYS]}")
            # ...and when the pack moves the binding the snapshot IS stale — say so with
            # the same rule the editor applies, so the next activation re-takes it.
            from trusted_data_agent.core.config_manager import reclassification_needed
            if reclassification_needed(_live, profile_data):
                profile_data["needs_reclassification"] = True
                app_logger.info(f"  @{tag}: binding changed by the pack — reclassification flagged")
            if config_manager.update_profile(ledger_pid, profile_data, user_uuid):
                tag_to_profile_id[tag] = ledger_pid
                shed = [
                    k for k in self._TYPE_EXCLUSIVE_KEYS.get(live_type, ())
                    if k not in profile_data
                ] if live_type and live_type != profile_data.get("profile_type") else []
                # A pack copy must be FAITHFUL on the capability-signed keys: the manifest
                # entry is the publisher's projection of every portable key it has, so a
                # signed capability key the entry does not carry is absent on the
                # publisher — and a copy that still carries it can never reach the
                # publisher's badge (a coordinator that held an analyst's entry for a
                # morning kept `tools`/`useMcpTools` and read `changed` forever). Posture
                # keys keep their own rule: absence preserves (never a silent downgrade).
                # Stated by POLICY, not by a hand list: every portable key WITHOUT a
                # downgrade guard (no mode / flag / floor / ceiling path) is the pack's
                # to set and to unset — capability keys and the unsigned skills /
                # recommender / judge blocks alike (a leftover skillsConfig adds skill
                # INGREDIENTS the publisher's agent never had, so the composition badge
                # can never match). A guarded key (the posture blocks, knowledgeConfig
                # with its floor and flag) keeps "absence preserves".
                shed += [
                    k for k in _pcc.portable_keys()
                    if (k in _live_keys and k not in profile_data and k not in shed
                        and self._pack_owned_absence(_pcc.spec_for(k)))
                ]
                if shed and hasattr(config_manager, "remove_profile_keys"):
                    config_manager.remove_profile_keys(ledger_pid, shed, user_uuid)
                    app_logger.info(
                        f"  @{tag} changed type {live_type} -> "
                        f"{profile_data.get('profile_type')}; dropped {shed}"
                    )
                app_logger.info(f"  Updated @{tag} in place (id={ledger_pid})")
                return
            # Ledger owns the tag but no live profile carries the ID.
            config_manager.add_profile(profile_data, user_uuid)
            tag_to_profile_id[tag] = ledger_pid
            app_logger.warning(
                f"  Re-created @{tag} at its ledger ID {ledger_pid} — the pack owned "
                f"this tag but no live profile carried it. References to that ID "
                f"(knowledge graph, collections, pack resources) remain valid."
            )
            return
        config_manager.add_profile(profile_data, user_uuid)
        tag_to_profile_id[tag] = profile_data["id"]
        app_logger.info(f"  Added new @{tag} (id={profile_data['id']})")


    #: injectable for tests — the real GraphStore binds to the application database
    _graph_store_cls = None

    async def _refresh_pack_kgs_in_place(self, manifest: dict, temp_path, tag_to_profile_id: dict,
                                         old_resources: list, user_uuid: str, warnings: list) -> dict:
        """Bring the consumer's pack-owned knowledge graphs in line with the manifest's,
        keeping every id that can be kept.

        Returns ``{"refreshed": n, "created": [kg_id, ...], "moved": n}``.

        Each manifest graph is matched to the pack-owned graph the profile it is shipped
        for is currently on (``_pack_kg_for_profile``). Matched → snapshot, clear_graph,
        import_bulk, id kept (the subscription-sync idiom). A graph the pack does not own
        is never touched.

        The graph TOPOLOGY may change between releases, and the match above is silent
        about it — which is how a split would have destroyed a consumer's graph:

        * SPLIT (one shipped graph became several — the policing pack moving from one
          combined ontology to one per expert): two manifest entries resolve to the SAME
          consumer graph. Refreshing it twice would leave it holding only the second
          entry's content while both analysts stayed active on it — the first product's
          ontology gone, ``kgs_refreshed: 2`` reported. Now the graph stays with the
          entry the manifest names as its OWNER (else the first in manifest order), and
          every other entry that resolved to it gets a graph of its own: created under
          the manifest owner, imported, recorded as a pack resource, and the profile's
          active assignment MOVED onto it.
        * MERGE (several became one): the surviving entry's ``assigned_profiles`` pull the
          other profiles onto the kept graph; the pack graph they left is kept, inactive,
          and reported.

        Assignments follow the manifest for the profiles the pack ships, with ONE rule
        for activation: a profile's active graph is moved only when its current active
        graph is pack-owned — a consumer's own choice of graph is never displaced (the
        fresh-install rule). A pack profile's assignment rows to pack-owned graphs the
        manifest no longer assigns it to are removed.
        """
        pack_kgs = manifest.get("knowledge_graphs", []) or []
        result = {"refreshed": 0, "created": [], "moved": 0, "names": {}}
        if not pack_kgs:
            return result
        import json as _json
        GS = self._graph_store_cls
        if GS is None:
            from components.builtin.knowledge_graph.graph_store import GraphStore as GS  # noqa: N806
        try:
            from trusted_data_agent.core.kg_version_db import snapshot_kg as _snap
        except Exception:  # noqa: BLE001 — versioning is optional; refresh still proceeds
            _snap = None
        owned = {str(r["resource_id"]) for r in old_resources if r.get("resource_type") == "knowledge_graph"}

        # ── pre-pass: resolve every entry BEFORE anything is mutated, so the topology
        # decision (who keeps a shared graph) is made on the consumer's state as found
        plans = []
        for entry in pack_kgs:
            ref = entry.get("ref") or entry.get("file") or entry.get("name")
            fpath = temp_path / entry.get("file", "")
            if not entry.get("file") or not fpath.exists():
                warnings.append(f"KG '{entry.get('name')}': file missing from the pack; not refreshed")
                continue
            # Resolve the consumer's graph by the entry's OWNER first. A graph is owned by
            # exactly one agent, while `profile_tag` is whichever ASSIGNED profile the
            # export happened to walk first — and the moment a graph legitimately grounds
            # more than one agent (the policing pack's Optimize twin, Sep 2026) that single
            # tag stopped identifying it. When it named a profile the consumer had no
            # assignment for, the entry resolved to nothing, was skipped, and
            # `_prune_pack_kg_assignments` then deleted the consumer's REAL binding —
            # observed live: an in-place update left @NIBRS with no ontology at all.
            # Every listed assignee is a last resort so an older manifest still resolves.
            pid = kg_id = None
            for _tag in ([entry.get("owner_profile_tag"), entry.get("profile_tag")]
                         + [ap.get("tag") for ap in (entry.get("assigned_profiles") or [])]):
                _pid = tag_to_profile_id.get(_tag)
                if not _pid:
                    continue
                _kg = self._pack_kg_for_profile(_pid, user_uuid, owned)
                if _kg:
                    pid, kg_id = _pid, _kg
                    break
            if not pid or not kg_id:
                continue  # a graph the consumer replaced with their own is theirs; never overwritten
            owner_pid = tag_to_profile_id.get(entry.get("owner_profile_tag")) or pid
            plans.append({"ref": ref, "entry": entry, "pid": pid, "owner_pid": owner_pid,
                          "path": fpath, "kg_id": kg_id})
        keeper = self._split_keepers(plans, {p["kg_id"]: self._kg_owner(p["kg_id"], user_uuid) for p in plans})

        pack_graphs = set(owned)
        desired_rows: set = set()   # (assigned_profile_id, kg_id) the manifest wants
        for p in plans:
            entry, pid, kg_id = p["entry"], p["pid"], p["kg_id"]
            data = _json.loads(p["path"].read_text())
            ents, rels = data.get("entities", []) or [], data.get("relationships", []) or []
            if keeper.get(kg_id) != p["ref"]:
                # SPLIT: this entry's graph was kept by another entry — it gets its own
                new_id = self._create_pack_kg(GS, entry, data, p["owner_pid"], user_uuid, warnings)
                if not new_id:
                    continue
                result["created"].append(new_id)
                result["names"][new_id] = data.get("name") or entry.get("name")
                pack_graphs.add(new_id)
                app_logger.info(f"  KG '{entry.get('name')}': the pack split its graph — created "
                                f"{new_id} under {p['owner_pid']} for @{entry.get('profile_tag')} "
                                f"(was on {kg_id})")
                kg_id = new_id
            else:
                try:
                    store = GS(profile_id=pid, user_uuid=user_uuid, kg_id=kg_id)
                    if _snap is not None:
                        try:
                            cur_e = await store.list_entities(limit=100000)
                            cur_r = await store.list_relationships(limit=200000)
                            _snap(kg_id, user_uuid, cur_e or [], cur_r or [], label="Pre-pack-update snapshot")
                        except Exception as _se:  # noqa: BLE001
                            warnings.append(f"KG '{entry.get('name')}': pre-update snapshot failed ({_se}); refresh continued")
                    # one operation: the pack's layer replaced, the consumer's authored layer kept
                    res = store.replace_model_layer(entities=ents, relationships=rels)
                    self._stamp_pack_kg_validation(store, data)
                    result["refreshed"] += 1
                    result["names"][kg_id] = data.get("name") or entry.get("name")
                    # the graph's identity follows the manifest too: after a split the
                    # kept graph holds ONE product but was still named "(CASE + NIBRS)"
                    # on the rehearsal consumer — a name that no longer describes it
                    self._describe_pack_kg(kg_id, entry, data, user_uuid)
                    app_logger.info(f"  Refreshed KG '{entry.get('name')}' in place (id={kg_id}, "
                                    f"entities={res.get('entities_added')}, rels={res.get('relationships_added')})")
                except Exception as exc:  # noqa: BLE001 — one graph's failure must not abort the pack update
                    warnings.append(f"KG '{entry.get('name')}': in-place refresh failed ({exc})")
                    app_logger.error(f"  KG in-place refresh failed for {kg_id}: {exc}", exc_info=True)
                    continue
            moved = self._reconcile_pack_kg_assignments(
                kg_id, entry, tag_to_profile_id, pack_graphs, user_uuid, warnings, desired_rows)
            result["moved"] += moved

        if plans:
            self._prune_pack_kg_assignments(tag_to_profile_id, pack_graphs, desired_rows, user_uuid, warnings)
        return result

    @staticmethod
    def _split_keepers(plans: list, owner_of: dict) -> dict:
        """Pure: which manifest entry KEEPS each consumer graph several entries resolved to.

        The entry whose manifest owner is the graph's current owner keeps it (the graph
        stays with the profile the manifest says owns it); otherwise the first entry in
        manifest order. Every other entry on that graph is a split and gets its own."""
        keepers: dict = {}
        by_kg: dict = {}
        for p in plans:
            by_kg.setdefault(p["kg_id"], []).append(p)
        for kg_id, group in by_kg.items():
            match = next((p for p in group if p["owner_pid"] == owner_of.get(kg_id)), None)
            keepers[kg_id] = (match or group[0])["ref"]
        return keepers

    def _stamp_pack_kg_validation(self, store, data: dict) -> None:
        """The source-validation stamp of a pack graph follows the pack's CONTENT — the rule
        an apply-update applies to its candidate. Three sources, in order: the publisher's
        stamp carried in the pack (kept verbatim, its ``validated_at`` included — the graph
        was validated THERE, at that time); else the stamped facts on the nodes themselves
        (a pack exported before the stamp travelled); else cleared — a refresh from an
        unvalidated source must never leave a stale badge on the consumer's card."""
        if not hasattr(store, "set_source_validation"):
            return
        try:
            from trusted_data_agent.agent.kg_constructors import source_validation as _sv
            carried = data.get("source_validation") if isinstance(data, dict) else None
            ents = (data.get("entities") or []) if isinstance(data, dict) else []
            if isinstance(carried, dict) and carried:
                store.set_source_validation({k: v for k, v in carried.items() if k != "validated_at"},
                                            validated_at=carried.get("validated_at"))
            elif _sv.has_source_validation(ents):
                store.set_source_validation(_sv.summarize(ents))
            else:
                store.set_source_validation(None)
        except Exception as exc:  # noqa: BLE001 — never fail a pack import on the stamp
            app_logger.warning(f"[Pack import] could not stamp source validation: {exc}")

    def _describe_pack_kg(self, kg_id: str, entry: dict, data: dict, user_uuid: str) -> None:
        """Name, database and description of a refreshed pack graph follow the manifest
        (metadata-only columns; the owner and activation are never touched here)."""
        name = data.get("name") or entry.get("name")
        if not name:
            return
        conn = sqlite_util.connect(self.db_path)
        try:
            conn.execute("UPDATE kg_metadata SET name = ?, database_name = COALESCE(?, database_name), "
                         "description = COALESCE(?, description) WHERE kg_id = ? AND user_uuid = ?",
                         (name, data.get("database_name") or entry.get("database_name"),
                          data.get("description") or entry.get("description"), kg_id, user_uuid))
            conn.commit()
        finally:
            conn.close()

    def _kg_owner(self, kg_id: str, user_uuid: str):
        conn = sqlite_util.connect(self.db_path)
        try:
            row = conn.execute("SELECT profile_id FROM kg_metadata WHERE kg_id = ? AND user_uuid = ?",
                               (kg_id, user_uuid)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _create_pack_kg(self, GS, entry: dict, data: dict, owner_pid: str, user_uuid: str,
                        warnings: list):
        """A new pack-owned graph for a manifest entry, under its manifest owner. The
        owner's own active graph is not displaced (the fresh-install rule); a split
        whose owner already grounds on another graph is reported."""
        import uuid as _uuid
        new_id = str(_uuid.uuid4())
        conn = sqlite_util.connect(self.db_path)
        try:
            has_active = conn.execute(
                "SELECT 1 FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? AND is_active = 1 LIMIT 1",
                (owner_pid, user_uuid)).fetchone() is not None
        finally:
            conn.close()
        try:
            store = GS(profile_id=owner_pid, user_uuid=user_uuid, kg_id=new_id)
            store.set_kg_metadata(
                name=data.get("name") or entry.get("name", "Imported KG"),
                database_name=data.get("database_name") or entry.get("database_name"),
                description=data.get("description") or entry.get("description"),
                is_active=not has_active,
            )
            store.import_bulk(entities=data.get("entities", []) or [],
                              relationships=data.get("relationships", []) or [])
            self._stamp_pack_kg_validation(store, data)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"KG '{entry.get('name')}': creating the split graph failed ({exc})")
            app_logger.error(f"  KG split create failed for '{entry.get('name')}': {exc}", exc_info=True)
            return None
        if has_active:
            warnings.append(f"KG '{entry.get('name')}': created inactive — its owner already grounds "
                            f"on another graph; activate it under Knowledge Graphs if intended")
        return new_id

    def _reconcile_pack_kg_assignments(self, kg_id: str, entry: dict, tag_to_profile_id: dict,
                                       pack_graphs: set, user_uuid: str, warnings: list,
                                       desired_rows: set) -> int:
        """Assignment rows for one shipped graph follow its manifest entry. Returns how
        many profiles were MOVED onto it (their active graph changed)."""
        listed = entry.get("assigned_profiles") or [{"tag": entry.get("profile_tag"), "is_active": True}]
        owner_pid = self._kg_owner(kg_id, user_uuid)
        if not owner_pid:
            return 0
        moved = 0
        conn = sqlite_util.connect(self.db_path)
        try:
            for ap in listed:
                apid = tag_to_profile_id.get(ap.get("tag"))
                if not apid:
                    continue
                desired_rows.add((apid, kg_id))
                # the table's uniqueness is (owner, assigned, user) — a row for this pair may
                # already exist pointing at another of the owner's graphs; re-point it only
                # if that graph is the pack's, else the row is the consumer's and stays
                existing = conn.execute(
                    "SELECT kg_id, is_active FROM kg_profile_assignments "
                    "WHERE kg_owner_profile_id = ? AND assigned_profile_id = ? AND user_uuid = ?",
                    (owner_pid, apid, user_uuid)).fetchone()
                if existing and str(existing[0]) != kg_id:
                    if str(existing[0]) in pack_graphs:
                        conn.execute("UPDATE kg_profile_assignments SET kg_id = ? WHERE kg_owner_profile_id = ? "
                                     "AND assigned_profile_id = ? AND user_uuid = ?",
                                     (kg_id, owner_pid, apid, user_uuid))
                    else:
                        warnings.append(f"KG '{entry.get('name')}': @{ap.get('tag')} keeps its own assignment "
                                        f"to another graph of the same owner; not reassigned")
                        continue
                elif not existing:
                    conn.execute("INSERT INTO kg_profile_assignments (kg_id, kg_owner_profile_id, "
                                 "assigned_profile_id, user_uuid, is_active) VALUES (?, ?, ?, ?, 0)",
                                 (kg_id, owner_pid, apid, user_uuid))
                if not bool(ap.get("is_active", False)):
                    continue
                active = conn.execute(
                    "SELECT kg_id FROM kg_profile_assignments WHERE assigned_profile_id = ? "
                    "AND user_uuid = ? AND is_active = 1", (apid, user_uuid)).fetchone()
                cur = str(active[0]) if active and active[0] is not None else None
                if cur == kg_id:
                    continue
                if cur is not None and cur not in pack_graphs:
                    continue  # the consumer's own graph — never displaced
                conn.execute("UPDATE kg_profile_assignments SET is_active = 0 WHERE assigned_profile_id = ? "
                             "AND user_uuid = ?", (apid, user_uuid))
                conn.execute("UPDATE kg_profile_assignments SET is_active = 1 WHERE kg_id = ? "
                             "AND assigned_profile_id = ? AND user_uuid = ?", (kg_id, apid, user_uuid))
                if cur is not None:
                    moved += 1
            conn.commit()
        finally:
            conn.close()
        return moved

    def _prune_pack_kg_assignments(self, tag_to_profile_id: dict, pack_graphs: set,
                                   desired_rows: set, user_uuid: str, warnings: list) -> None:
        """A pack profile's rows to pack-owned graphs the manifest no longer assigns it to
        are removed; a pack-owned graph left with no active pack profile is reported."""
        pids = {p for p in tag_to_profile_id.values() if p}
        if not pids or not pack_graphs:
            return
        conn = sqlite_util.connect(self.db_path)
        try:
            for apid in pids:
                for (kid,) in conn.execute(
                        "SELECT kg_id FROM kg_profile_assignments WHERE assigned_profile_id = ? AND user_uuid = ?",
                        (apid, user_uuid)).fetchall():
                    if kid is not None and str(kid) in pack_graphs and (apid, str(kid)) not in desired_rows:
                        conn.execute("DELETE FROM kg_profile_assignments WHERE kg_id = ? AND assigned_profile_id = ? "
                                     "AND user_uuid = ?", (kid, apid, user_uuid))
            conn.commit()
            for kid in sorted(pack_graphs):
                live = conn.execute("SELECT 1 FROM kg_profile_assignments WHERE kg_id = ? AND user_uuid = ? "
                                    "AND is_active = 1 LIMIT 1", (kid, user_uuid)).fetchone()
                if not live:
                    warnings.append(f"KG {kid}: no pack profile is active on it after this update; "
                                    f"kept, not deleted")
        finally:
            conn.close()

    def _pack_kg_for_profile(self, profile_id: str, user_uuid: str, owned: set) -> "str | None":
        """The pack-owned graph a profile is on — the active one first, else any it holds."""
        conn = sqlite_util.connect(self.db_path)
        try:
            rows = [tuple(r) for r in conn.execute(
                "SELECT kg_id, is_active FROM kg_profile_assignments "
                "WHERE assigned_profile_id = ? AND user_uuid = ?",
                (profile_id, user_uuid)).fetchall()]
            if not rows:
                # legacy single-KG rows carry the owner on kg_metadata only. The
                # `released_of` column is self-applying (Model B); a DB that predates it
                # gains the column here rather than failing the lookup.
                _q = ("SELECT kg_id, is_active FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? "
                      "AND released_of IS NULL")   # a released view is never a pack graph
                try:
                    rows = [tuple(r) for r in conn.execute(_q, (profile_id, user_uuid)).fetchall()]
                except Exception:  # noqa: BLE001 — a DB predating the column holds no shadows
                    rows = [tuple(r) for r in conn.execute(
                        "SELECT kg_id, is_active FROM kg_metadata WHERE profile_id = ? AND user_uuid = ?",
                        (profile_id, user_uuid)).fetchall()]
        finally:
            conn.close()
        return self._pick_pack_kg(rows, owned)

    @staticmethod
    def _pick_pack_kg(rows, owned: set):
        """Pure: among (kg_id, is_active) rows, the active pack-owned graph, else the first
        pack-owned one, else None — a graph the pack does not own is never touched."""
        cands = [r[0] for r in rows if str(r[0]) in owned]
        active = [r[0] for r in rows if str(r[0]) in owned and r[1]]
        return (active or cands or [None])[0]

    @staticmethod
    def _pair_collections_by_identity(manifest_collections: list, ref_to_id: dict,
                                      coll_db) -> dict:
        """Pure over its inputs: ``{manifest ref: existing collection id}`` paired by
        identity — ``(repository_type, name)`` — with the positional ref as the fallback.

        Rules: an existing collection is claimed by at most one manifest entry; an
        identity that matches SEVERAL existing collections is ambiguous and falls back
        to the positional ref (only if that ref's collection is still unclaimed); a
        manifest entry with no name pairs positionally; an entry matching nothing is
        left unpaired (the caller creates it). ``coll_db.get_collection_by_id`` is the
        only read."""
        by_identity: dict = {}
        for ref, cid in ref_to_id.items():
            try:
                row = coll_db.get_collection_by_id(cid) or {}
            except Exception:  # noqa: BLE001
                row = {}
            key = (str(row.get("repository_type") or "knowledge"), str(row.get("name") or "").strip().lower())
            if key[1]:
                by_identity.setdefault(key, []).append(cid)
        paired: dict = {}
        claimed: set = set()
        # identity first, so a positional fallback never steals a collection an
        # identity match owns
        for entry in manifest_collections:
            ref = entry.get("ref")
            name = str(entry.get("name") or "").strip().lower()
            if not ref or not name:
                continue
            key = (str(entry.get("repository_type") or "knowledge"), name)
            cands = [c for c in by_identity.get(key, []) if c not in claimed]
            if len(cands) == 1:
                paired[ref] = cands[0]
                claimed.add(cands[0])
        for entry in manifest_collections:
            ref = entry.get("ref")
            if not ref or ref in paired:
                continue
            cid = ref_to_id.get(ref)
            if cid is None or cid in claimed:
                continue
            name = str(entry.get("name") or "").strip().lower()
            if name:
                # a NAMED entry takes the positional store only when that store IS the
                # same identity (the ambiguous-duplicate case) or has no identity of its
                # own — never a store known to be something else
                try:
                    row = coll_db.get_collection_by_id(cid) or {}
                except Exception:  # noqa: BLE001
                    row = {}
                store_key = (str(row.get("repository_type") or "knowledge"), str(row.get("name") or "").strip().lower())
                if store_key[1] and store_key != (str(entry.get("repository_type") or "knowledge"), name):
                    continue
            paired[ref] = cid
            claimed.add(cid)
        return paired

    async def _handle_update_in_place(
        self,
        manifest: dict,
        temp_path: Path,
        existing_profiles: list[dict],
        conflicting_tags: list[str],
        config_manager,
        user_uuid: str,
        mcp_server_id: str | None,
        llm_configuration_id: str | None,
        vector_store_config_id: str | None,
        force_drop_tags: bool,
        dry_run: bool,
        allow_posture_downgrade: bool = False,
        mcp_tag_bindings: dict | None = None,
    ) -> dict:
        """Re-import an agent pack while preserving every subscriber-visible ID.

        Preserves:
          - agent_pack_installations.id
          - marketplace_agent_packs.id (if a listing exists)
          - profile.id per matching tag
          - collection.id per matching ref
          - per-document CDC config (source_uri, sync_enabled)

        See plan: /Users/rainer.geissendoerfer/.claude/plans/replicated-wobbling-squirrel.md
        """
        from trusted_data_agent.core.agent_pack_db import AgentPackDB
        from trusted_data_agent.core.collection_db import get_collection_db
        from trusted_data_agent.agent.rag_retriever import get_rag_retriever

        pack_db = AgentPackDB(self.db_path)
        coll_db = get_collection_db()

        manifest_profiles = manifest["profiles"]
        manifest_collections = manifest.get("collections", [])
        new_tags = {p["tag"] for p in manifest_profiles}

        # ── Step 1: Detect target installation ────────────────────────────────
        # All conflicting profiles must come from a single pack installation.
        existing_by_tag = {p["tag"]: p for p in existing_profiles if p.get("tag")}
        candidate_installation_ids: set[int] = set()
        for tag in conflicting_tags:
            prof = existing_by_tag.get(tag)
            if not prof:
                continue
            for pack_info in pack_db.get_packs_for_resource("profile", str(prof["id"])):
                candidate_installation_ids.add(int(pack_info["id"]))

        # Same fact from the ledger, for tags whose live profile is missing. Union, not
        # fallback: a pack half-lost still has to resolve to exactly ONE installation,
        # so a ledger-only tag must be able to make the target ambiguous rather than
        # quietly disappear from the check below.
        ledger_owned = pack_db.get_installations_by_profile_tags(
            list(conflicting_tags), user_uuid
        )
        for _tag, _info in ledger_owned.items():
            candidate_installation_ids.add(_info["installation_id"])

        if not candidate_installation_ids:
            raise ValueError(
                "update_in_place: profiles match by tag but none are managed by "
                "an existing pack installation. They were likely created manually. "
                "Use 'replace' instead."
            )
        if len(candidate_installation_ids) > 1:
            raise ValueError(
                f"ambiguous_target_installation: existing profiles span "
                f"{len(candidate_installation_ids)} different pack installations: "
                f"{sorted(candidate_installation_ids)}. Cannot update_in_place."
            )

        target_installation_id = candidate_installation_ids.pop()
        app_logger.info(
            f"update_in_place: target installation_id={target_installation_id}"
        )

        # Load old pack's resources to compute dropped tags & collection mapping.
        old_resources = pack_db.get_resources_for_pack(target_installation_id)
        old_resources, ledger_corrections = self._heal_ledger_tags(
            old_resources, existing_profiles, new_tags
        )
        for _n in ledger_corrections:
            app_logger.warning(f"update_in_place: {_n}")
        old_profile_tags = {
            r["resource_tag"] for r in old_resources
            if r["resource_type"] == "profile" and r.get("resource_tag")
        }
        old_coll_ref_to_id = {
            r["resource_tag"]: int(r["resource_id"])
            for r in old_resources
            if r["resource_type"] == "collection" and r.get("resource_tag")
        }
        # A manifest ref (`collection_N`) is a COORDINATE of one export's walk, not an
        # identity: the walk follows the profiles, and a re-registration or a new member
        # can reorder it. Paired positionally, the next update patched the core-model
        # planner INTO the consumer's NIBRS planner copy and created the NIBRS planner
        # anew (production, 2026-09-15). Pair by what a collection IS instead — the
        # repository type and the name the copy kept — and keep the positional pairing
        # only as the fallback for a manifest entry that carries no name.
        old_coll_ref_to_id = self._pair_collections_by_identity(
            manifest_collections, old_coll_ref_to_id, coll_db)

        # ── Step 2: Validation ────────────────────────────────────────────────
        validation_errors: list[str] = []

        dropped_tags = sorted(old_profile_tags - new_tags)
        if dropped_tags and not force_drop_tags:
            validation_errors.append(
                f"dropped_tags: the manifest no longer contains tags "
                f"{', '.join('@' + t for t in dropped_tags)}. Re-run with "
                f"force_drop_tags=true to remove them, or restore them in the manifest."
            )

        # Check embedding-model lock per existing collection that the manifest still references.
        for coll_entry in manifest_collections:
            ref = coll_entry["ref"]
            existing_coll_id = old_coll_ref_to_id.get(ref)
            if existing_coll_id is None:
                continue  # Brand new collection, no lock to enforce
            existing_coll = coll_db.get_collection_by_id(existing_coll_id)
            if not existing_coll:
                continue
            try:
                inner_meta = self._read_collection_zip_metadata(temp_path / coll_entry["file"])
            except Exception as e:
                validation_errors.append(
                    f"collection '{ref}': failed to read inner ZIP metadata: {e}"
                )
                continue
            new_model = inner_meta.get("embedding_model", "")
            existing_model = existing_coll.get("embedding_model", "")
            if (
                int(existing_coll.get("embedding_model_locked") or 0) == 1
                and new_model
                and existing_model
                and new_model != existing_model
            ):
                validation_errors.append(
                    f"embedding_model_lock_violation: collection '{ref}' is locked "
                    f"to embedding_model='{existing_model}', but manifest declares "
                    f"'{new_model}'. Unlock the collection first or align models."
                )

        # Trust-posture downgrade guard (charter #8, fail-safe). The profile merge is a
        # shallow dict.update, so a manifest block REPLACES the stored one wholesale — a
        # pack shipping `observe` would silently switch off a hardened agent's enforcement.
        # Absence still preserves (never a downgrade); only an explicit weakening trips
        # this. Same seam and shape as dropped_tags above, so dry_run reports it too.
        posture_downgrades = []
        if not allow_posture_downgrade:
            _existing_by_tag = {
                r["resource_tag"]: r["resource_id"] for r in old_resources
                if r["resource_type"] == "profile" and r.get("resource_tag")
            }
            for _p in manifest_profiles:
                _pid = _existing_by_tag.get(_p.get("tag"))
                if not _pid:
                    continue  # brand-new profile — nothing to weaken
                try:
                    _live = config_manager.get_profile(_pid, user_uuid)
                except Exception:
                    _live = None
                if not _live:
                    continue
                for _d in _pcc.find_downgrades(_live, _p):
                    posture_downgrades.append(f"@{_p['tag']} {_d.describe()}")
        # ...and the planner lock: a LOCKED repository whose manifest entry says
        # unlocked is a downgrade too (the seal would shrink from every case to the
        # pack-owned ones). Absence of the key preserves; only an explicit False trips.
        if not allow_posture_downgrade:
            for coll_entry in manifest_collections:
                if "locked" not in coll_entry or coll_entry.get("locked"):
                    continue
                _cid = old_coll_ref_to_id.get(coll_entry["ref"])
                if _cid is None:
                    continue
                try:
                    _row = coll_db.get_collection_by_id(_cid) or {}
                except Exception:
                    _row = {}
                if _row.get("repository_type") == "planner" and _row.get("trust_locked"):
                    posture_downgrades.append(
                        f"planner repository '{_row.get('name') or coll_entry['ref']}' locked → unlocked")
        if posture_downgrades:
            validation_errors.append(
                "posture_downgrade: the manifest would REDUCE protection on an existing "
                f"profile — {'; '.join(posture_downgrades)}. Re-run with "
                "allow_posture_downgrade=true to accept, or align the manifest."
            )

        # ── Step 2 (cont.): If validation failed, fail loudly ──────────────────
        if validation_errors:
            if dry_run:
                return {
                    "status": "dry_run_failed",
                    "validation_errors": validation_errors,
                    "would_preserve": None,
                    "would_change": None,
                }
            raise ValueError("; ".join(validation_errors))

        # ── Step 3: Compute the diff plan (used by both dry-run and commit) ──
        #
        # Diff signal: per-document SOURCE FILE content hash.  The agent pack
        # build records each chunk's source_content_hash (sha256 of the raw
        # file bytes).  This matches what CDC sync writes into
        # knowledge_documents.content_hash, so:
        #     incoming.content_hash == existing.content_hash  <=>  file unchanged.
        # We can't compare chunk-id sets directly because the post-install CDC
        # sync re-chunks every doc via the constructor (different chunk sizes),
        # so existing chunk_ids never match build-time chunk_ids.
        collection_plans: list[dict] = []
        for coll_entry in manifest_collections:
            ref = coll_entry["ref"]
            existing_coll_id = old_coll_ref_to_id.get(ref)
            if existing_coll_id is None:
                # Brand-new collection in this update
                collection_plans.append({
                    "ref": ref,
                    "existing_collection_id": None,
                    "action": "create",
                })
                continue

            inner_zip = temp_path / coll_entry["file"]

            # A PLANNER repository is not a document store: its unit is a champion
            # case, it has no knowledge backend, and `_diff_and_patch_collection`
            # raises on one. It also holds cases the agent LEARNED from its own
            # successful turns, which a pack must never delete. So it gets its own
            # plan and its own patch, keyed on the case's question.
            if (coll_entry.get("repository_type")
                    or (coll_db.get_collection_by_id(existing_coll_id) or {}).get(
                        "repository_type")) == "planner":
                collection_plans.append({
                    "ref": ref,
                    "existing_collection_id": existing_coll_id,
                    "action": "diff_patch_planner",
                    "inner_zip": inner_zip,
                })
                continue

            # The ZIP carries the EXPORTING instance's document ids. A document id is
            # local to an instance (the fresh-install path already mints its own); the
            # in-place path had kept the source ids, so on an instance that hosts both
            # the publisher and a subscriber the subscriber's rows hit
            # `ON CONFLICT(document_id)` and UPDATED THE PUBLISHER'S rows instead —
            # observed live: 20 publisher rows re-stamped, zero rows for the copy.
            _scope = (coll_db.get_collection_by_id(existing_coll_id) or {}).get("collection_name") \
                or f"collection_{existing_coll_id}"
            incoming = self._localize_incoming(
                self._read_collection_zip_documents(inner_zip), _scope)
            existing_docs = coll_db.get_all_documents_in_collection(existing_coll_id)
            existing_by_id = {d["document_id"]: d for d in existing_docs}

            to_add: list[str] = []
            to_remove: list[str] = []
            to_update: list[str] = []
            unchanged: list[str] = []
            for doc_id, info in incoming.items():
                if doc_id not in existing_by_id:
                    to_add.append(doc_id)
                    continue
                existing_hash = existing_by_id[doc_id].get("content_hash")
                incoming_hash = info["content_hash"]
                # Guard: only treat the doc as unchanged when the incoming
                # pack actually carries source_content_hash (newer build.py).
                # Older builds fall back to a chunk-text hash that won't
                # match — those will be flagged updated, which is the safe
                # conservative behaviour.
                if (
                    info.get("has_source_content_hash")
                    and existing_hash
                    and existing_hash == incoming_hash
                ):
                    unchanged.append(doc_id)
                else:
                    to_update.append(doc_id)
            for doc_id in existing_by_id.keys():
                if doc_id not in incoming:
                    to_remove.append(doc_id)

            collection_plans.append({
                "ref": ref,
                "existing_collection_id": existing_coll_id,
                "action": "diff_patch",
                "incoming": incoming,
                "existing_by_id": existing_by_id,
                "to_add": to_add,
                "to_update": to_update,
                "to_remove": to_remove,
                "unchanged": unchanged,
                "inner_zip": inner_zip,
            })

        # Profile diffs.
        existing_pack_profile_resources = [
            r for r in old_resources if r["resource_type"] == "profile"
        ]
        existing_pack_profile_id_by_tag = {
            r["resource_tag"]: r["resource_id"]
            for r in existing_pack_profile_resources
            if r.get("resource_tag")
        }
        profile_plans = {
            "updated": [
                p["tag"] for p in manifest_profiles
                if p["tag"] in old_profile_tags
            ],
            "added": [
                p["tag"] for p in manifest_profiles
                if p["tag"] not in old_profile_tags
            ],
            "dropped": dropped_tags,
        }

        # Marketplace listing diff.
        marketplace_plan: dict
        listing_id, listing_old_version = self._get_marketplace_listing_for_installation(
            target_installation_id
        )
        if listing_id is None:
            marketplace_plan = {"action": "none"}
        else:
            marketplace_plan = {
                "action": "update",
                "listing_id": listing_id,
                "version_change": f"{listing_old_version} -> {manifest.get('version', '1.0.0')}",
            }

        # ── Step 4: Dry-run early return ──────────────────────────────────────
        if dry_run:
            return {
                "status": "dry_run",
                "would_preserve": {
                    "installation_id": target_installation_id,
                    "marketplace_listing_id": listing_id,
                    "profile_ids_by_tag": existing_pack_profile_id_by_tag,
                    "collection_ids_by_ref": old_coll_ref_to_id,
                },
                "would_change": {
                    "profiles": profile_plans,
                    "collections": [
                        {
                            "ref": cp["ref"],
                            "id": cp["existing_collection_id"],
                            "action": cp["action"],
                            "docs_unchanged": len(cp.get("unchanged", [])),
                            "docs_updated": len(cp.get("to_update", [])),
                            "docs_added": len(cp.get("to_add", [])),
                            "docs_removed": len(cp.get("to_remove", [])),
                        }
                        for cp in collection_plans
                    ],
                    "marketplace_listing": marketplace_plan,
                },
                "ledger_corrections": ledger_corrections,
                "validation_errors": [],
            }

        # ── Step 5: COMMIT path ───────────────────────────────────────────────
        uip_warnings: list[str] = []  # non-blocking (e.g. a refused skill overwrite)
        # Pause CDC poller for affected collections (set next_sync_at = NULL).
        affected_collection_ids = [
            cp["existing_collection_id"]
            for cp in collection_plans
            if cp["existing_collection_id"] is not None
        ]
        prior_next_sync_at: dict[int, str | None] = {}
        for cid in affected_collection_ids:
            existing_coll = coll_db.get_collection_by_id(cid)
            if existing_coll:
                prior_next_sync_at[cid] = existing_coll.get("next_sync_at")
        for cid in affected_collection_ids:
            coll_db.update_collection(cid, {"next_sync_at": None})
        app_logger.info(
            f"update_in_place: paused CDC poller for {len(affected_collection_ids)} "
            f"collection(s)."
        )

        retriever = get_rag_retriever()
        ref_to_collection_id: dict = {}
        total_added = 0
        total_updated = 0
        total_removed = 0
        total_unchanged = 0

        try:
            # ── Step 5a: Apply each collection plan ──────────────────────────
            for cp in collection_plans:
                ref = cp["ref"]
                if cp["action"] == "create":
                    # Brand-new collection — fall through to normal import_collection_from_zip
                    from trusted_data_agent.core.collection_utils import import_collection_from_zip
                    coll_entry = next(c for c in manifest_collections if c["ref"] == ref)
                    inner_zip = temp_path / coll_entry["file"]
                    is_planner = coll_entry.get("repository_type") == "planner"
                    coll_mcp = self._planner_mcp_for_ref(
                        ref, manifest, self._mcp_name_map(user_uuid),
                        mcp_tag_bindings, mcp_server_id,
                    ) if is_planner else None
                    result = await import_collection_from_zip(
                        zip_path=inner_zip,
                        user_uuid=user_uuid,
                        display_name=coll_entry.get("name"),
                        mcp_server_id=coll_mcp,
                        skip_reload=True,
                        populate_knowledge_docs=True,
                        vector_store_config_id=vector_store_config_id,
                    )
                    new_coll_id = result["collection_id"]
                    ref_to_collection_id[ref] = {
                        "id": new_coll_id,
                        "name": result["collection_name"],
                    }
                    if is_planner:
                        _n = await self._stamp_planner_ownership(
                            new_coll_id, {"collection_name": result["collection_name"]},
                            self._pack_ref(manifest))
                        app_logger.info(
                            f"  Planner '{ref}' -> id={new_coll_id}: stamped {_n} case(s) "
                            f"as pack-owned; keyed to {coll_mcp or 'NO SERVER'}")
                        self._apply_planner_lock(new_coll_id, coll_entry)
                    app_logger.info(
                        f"  Created new collection '{ref}' -> id={new_coll_id} "
                        f"({result['document_count']} docs)"
                    )
                    continue

                # Diff-and-patch existing collection
                if cp["action"] == "diff_patch_planner":
                    existing_coll_id = cp["existing_collection_id"]
                    existing_coll = coll_db.get_collection_by_id(existing_coll_id)
                    if not existing_coll:
                        raise RuntimeError(
                            f"update_in_place: planner collection id={existing_coll_id} "
                            f"disappeared mid-flight"
                        )
                    ref_to_collection_id[cp["ref"]] = {
                        "id": existing_coll_id,
                        "name": existing_coll.get("name", cp["ref"]),
                    }
                    stats = await self._diff_and_patch_planner_collection(
                        collection_id=existing_coll_id,
                        collection_meta=existing_coll,
                        inner_zip=cp["inner_zip"],
                        pack_ref=self._pack_ref(manifest),
                        retriever=retriever,
                    )
                    total_added += stats["added"]
                    total_updated += stats["updated"]
                    total_removed += stats["removed"]
                    total_unchanged += stats["unchanged"]
                    self._apply_planner_lock(
                        existing_coll_id,
                        next(c for c in manifest_collections if c["ref"] == cp["ref"]))
                    app_logger.info(
                        f"  Patched planner '{cp['ref']}' (id={existing_coll_id}): "
                        f"added={stats['added']} updated={stats['updated']} "
                        f"removed={stats['removed']} unchanged={stats['unchanged']} "
                        f"learned_kept={stats['learned_kept']}"
                    )
                    continue

                existing_coll_id = cp["existing_collection_id"]
                existing_coll = coll_db.get_collection_by_id(existing_coll_id)
                if not existing_coll:
                    raise RuntimeError(
                        f"update_in_place: collection id={existing_coll_id} disappeared mid-flight"
                    )
                ref_to_collection_id[ref] = {
                    "id": existing_coll_id,
                    "name": existing_coll.get("name", ref),
                }

                stats = await self._diff_and_patch_collection(
                    collection_id=existing_coll_id,
                    collection_meta=existing_coll,
                    inner_zip=cp["inner_zip"],
                    incoming=cp["incoming"],
                    existing_by_id=cp["existing_by_id"],
                    to_add=cp["to_add"],
                    to_update=cp["to_update"],
                    to_remove=cp["to_remove"],
                    retriever=retriever,
                )
                total_added += stats["added"]
                total_updated += stats["updated"]
                total_removed += stats["removed"]
                total_unchanged += stats["unchanged"]
                app_logger.info(
                    f"  Patched collection '{ref}' (id={existing_coll_id}): "
                    f"added={stats['added']} updated={stats['updated']} "
                    f"removed={stats['removed']} unchanged={stats['unchanged']}"
                )

            # ── Step 5a2: Install / refresh bundled skills (v1.3+ packs) ─────
            # Same loop as the fresh-install path's step 5b, and BEFORE the
            # profiles step for the same reason (profiles reference the skills).
            # save_skill overwrites in place, so a republished skill's content
            # update flows through — the update_in_place contract.
            _installed_skill_ids = self._install_pack_skills(
                manifest, temp_path, owner_user_uuid=user_uuid,
                warnings_sink=uip_warnings)

            # ── Step 5b: Resolve LLM config ──────────────────────────────────
            llm_config_id = llm_configuration_id or config_manager.get_active_llm_configuration_id(user_uuid)
            if not llm_config_id:
                configs = config_manager.get_llm_configurations(user_uuid)
                if configs:
                    llm_config_id = configs[0].get("id")
            if not llm_config_id:
                raise ValueError("No LLM configuration available. Please add one in Uderia first.")

            # ── Step 5c: Update / add profiles ───────────────────────────────
            tag_to_profile_id: dict = {}
            # Pre-fill with existing profile IDs (these get preserved verbatim).
            for tag, pid in existing_pack_profile_id_by_tag.items():
                tag_to_profile_id[tag] = pid

            non_genie = [p for p in manifest_profiles if p["profile_type"] != "genie"]
            genie_profiles = [p for p in manifest_profiles if p["profile_type"] == "genie"]
            mcp_name_to_id = self._mcp_name_map(user_uuid)

            for prof in non_genie:
                profile_data = self._build_profile(
                    prof, ref_to_collection_id, llm_config_id, mcp_server_id,
                    tag_to_profile_id=tag_to_profile_id,
                    mcp_name_to_id=mcp_name_to_id,
                    mcp_tag_bindings=mcp_tag_bindings,
                )
                self._upsert_pack_profile(
                    config_manager, prof, profile_data,
                    existing_pack_profile_id_by_tag.get(prof["tag"]),
                    user_uuid, tag_to_profile_id,
                )

            for prof in genie_profiles:
                profile_data = self._build_profile(
                    prof, ref_to_collection_id, llm_config_id, mcp_server_id,
                    tag_to_profile_id=tag_to_profile_id,
                    mcp_name_to_id=mcp_name_to_id,
                    mcp_tag_bindings=mcp_tag_bindings,
                )
                self._upsert_pack_profile(
                    config_manager, prof, profile_data,
                    existing_pack_profile_id_by_tag.get(prof["tag"]),
                    user_uuid, tag_to_profile_id,
                )

            # ── Step 5d: Drop tags that the manifest no longer mentions ──────
            archived_session_count = 0
            if dropped_tags and force_drop_tags:
                from trusted_data_agent.core.session_manager import (
                    archive_sessions_by_profile,
                )
                for dt in dropped_tags:
                    pid_to_drop = existing_pack_profile_id_by_tag.get(dt)
                    if not pid_to_drop:
                        continue
                    try:
                        archived = await archive_sessions_by_profile(pid_to_drop, user_uuid)
                        archived_session_count += int(archived or 0)
                    except Exception as e:
                        app_logger.warning(
                            f"  Could not archive sessions for dropped @{dt}: {e}"
                        )
                    config_manager.remove_profile(pid_to_drop, user_uuid)
                    app_logger.info(f"  Dropped @{dt} (id={pid_to_drop}) per force_drop_tags")

            # ── Step 5d2: Refresh pack-owned knowledge graphs IN PLACE ───────
            # A consumer's graph is a frozen copy with no source of its own, so it can
            # never follow the publisher's rebuild by resync; the pack IS its source.
            # Mirrors the marketplace subscription sync (clear, then bulk import; the
            # kg_id, its profile assignments and its trust attestation identity kept);
            # a pre-refresh version snapshot makes the step reversible. A graph the
            # pack no longer ships is left alone; one whose content is unchanged is a
            # no-op (the snapshot de-duplicates on content hash).
            kg_result = await self._refresh_pack_kgs_in_place(
                manifest, temp_path, tag_to_profile_id, old_resources, user_uuid, uip_warnings
            )
            kgs_refreshed = kg_result["refreshed"]
            kgs_created = list(kg_result["created"])

            # ── Step 5e: Reconcile agent_pack_resources ──────────────────────
            desired_resources: list[dict] = []
            for prof in manifest_profiles:
                desired_resources.append({
                    "resource_type": "profile",
                    "resource_id": tag_to_profile_id[prof["tag"]],
                    "resource_tag": prof["tag"],
                    "resource_role": prof.get("role", "standalone"),
                    "is_owned": True,
                })
            for ref, info in ref_to_collection_id.items():
                desired_resources.append({
                    "resource_type": "collection",
                    "resource_id": str(info["id"]),
                    "resource_tag": ref,
                    "resource_role": "collection",
                    "is_owned": True,
                })
            for _sid in _installed_skill_ids:
                desired_resources.append({
                    "resource_type": "skill",
                    "resource_id": _sid,
                    "resource_tag": _sid,
                    "resource_role": "skill",
                    "is_owned": True,
                })
            # every graph this run refreshed or split off is the pack's, tagged with the
            # name it now carries (a refreshed graph keeps its old ownership flag)
            _old_kg_owned = {str(r["resource_id"]): bool(r.get("is_owned", True))
                             for r in old_resources if r.get("resource_type") == "knowledge_graph"}
            for _kid, _kname in kg_result["names"].items():
                desired_resources.append({
                    "resource_type": "knowledge_graph",
                    "resource_id": str(_kid),
                    "resource_tag": _kname,
                    "resource_role": "knowledge_graph",
                    "is_owned": _old_kg_owned.get(str(_kid), True),
                })
            desired_resources = self._carry_forward_resources(
                desired_resources, old_resources
            )
            recon = pack_db.reconcile_pack_resources(
                target_installation_id, desired_resources
            )

            # ── Step 5f: Update agent_pack_installations row ────────────────
            self._update_installation_metadata(
                target_installation_id,
                manifest=manifest,
                manifest_profiles=manifest_profiles,
                tag_to_profile_id=tag_to_profile_id,
            )

            # ── Step 5g: Update marketplace listing in place ────────────────
            if listing_id is not None:
                self._update_marketplace_listing(
                    listing_id,
                    manifest=manifest,
                    profile_count=len(manifest_profiles),
                    collection_count=len(manifest_collections),
                    profile_tags=[p["tag"] for p in manifest_profiles],
                )

            # ── Step 5h: Reload retriever once ───────────────────────────────
            if retriever:
                try:
                    retriever.reload_collections_for_mcp_server()
                except Exception as e:
                    app_logger.warning(f"Failed to reload collections: {e}")

            # ── Step 5i: Re-sync collection counts ───────────────────────────
            for cid in affected_collection_ids:
                try:
                    coll_db.sync_collection_counts(cid)
                except Exception as e:
                    app_logger.warning(f"Failed to sync counts for collection {cid}: {e}")

            # ── Step 5j: Reinstate the publisher's attestations on the updated copies ──
            trust_reinstated = self._reinstate_member_trust(manifest, target_installation_id)

            commit_succeeded = True
        except Exception:
            commit_succeeded = False
            raise
        finally:
            # ── Resume CDC poller (always) ──────────────────────────────────
            from datetime import timedelta
            for cid in affected_collection_ids:
                existing_coll = coll_db.get_collection_by_id(cid)
                if not existing_coll:
                    continue
                interval = (existing_coll.get("sync_interval") or "daily").lower()
                interval_seconds = {
                    "hourly": 3600,
                    "6h": 21600,
                    "daily": 86400,
                    "weekly": 604800,
                }.get(interval, 86400)
                next_run = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=interval_seconds)
                ).isoformat()
                # On failure, restore the prior schedule rather than advance it.
                if not commit_succeeded and prior_next_sync_at.get(cid):
                    next_run = prior_next_sync_at[cid]
                coll_db.update_collection(cid, {"next_sync_at": next_run})

        # ── Step 6: Build response ──────────────────────────────────────────
        coordinator_tag = next(
            (p["tag"] for p in genie_profiles if p.get("role") == "coordinator"),
            None,
        )
        coordinator_profile_id = tag_to_profile_id.get(coordinator_tag) if coordinator_tag else None

        return {
            "status": "updated_in_place",
            "installation_id": target_installation_id,
            "marketplace_listing_id": listing_id,
            "name": manifest["name"],
            "coordinator_tag": coordinator_tag,
            "coordinator_profile_id": coordinator_profile_id,
            "profiles_updated": len(profile_plans["updated"]),
            "profiles_added": len(profile_plans["added"]),
            "profiles_dropped": len(dropped_tags) if force_drop_tags else 0,
            "collections_changed": sum(
                1 for cp in collection_plans
                if cp.get("to_update") or cp.get("to_add") or cp.get("to_remove")
            ),
            "docs_added": total_added,
            "docs_updated": total_updated,
            "docs_removed": total_removed,
            "docs_unchanged": total_unchanged,
            "archived_sessions": archived_session_count if dropped_tags and force_drop_tags else 0,
            "skills_installed": len(_installed_skill_ids),
            "kgs_refreshed": kgs_refreshed,
            "kgs_created": len(kgs_created),
            "kgs_moved": kg_result["moved"],
            "ledger_corrections": ledger_corrections,
            "trust_reinstated": trust_reinstated,
            "resource_reconciliation": recon,
            "warnings": uip_warnings or None,
            "tag_remap": None,
        }

    @staticmethod
    def _read_collection_zip_metadata(inner_zip: Path) -> dict:
        """Open a per-collection ZIP and return its collection_metadata.json content."""
        with zipfile.ZipFile(inner_zip, 'r') as zf:
            with zf.open("collection_metadata.json") as f:
                return json.load(f)

    @staticmethod
    def _localize_incoming(incoming: dict, scope: str) -> dict:
        """Pure: re-key an incoming document map to THIS collection's document ids.

        Uses the ONE derivation the fresh-install path uses
        (`collection_utils.local_document_id`, keyed on the target's vector collection
        NAME), so an update finds exactly the rows the install created. Each entry keeps `source_document_id`; every chunk's metadata
        `document_id` and the chunk id's leading document segment are rewritten, so the
        vectors and the registry agree under the local id. Input untouched."""
        from trusted_data_agent.core.collection_utils import local_document_id
        out: dict = {}
        for src, info in (incoming or {}).items():
            local = local_document_id(scope, src)
            chunks = []
            for c in info.get("chunks") or []:
                meta = dict(c.get("metadata") or {})
                meta["document_id"] = local
                cid = str(c.get("chunk_id") or "")
                if cid.startswith(str(src)):
                    cid = local + cid[len(str(src)):]
                chunks.append({**c, "chunk_id": cid, "metadata": meta})
            out[local] = {**info, "chunks": chunks, "source_document_id": str(src)}
        return out

    @staticmethod
    def _read_collection_zip_documents(inner_zip: Path) -> dict:
        """Parse a per-collection ZIP into {document_id: {chunks, content_hash, ...}}.

        Reads documents.jsonl (or legacy documents.json) and groups all chunks by
        document_id. content_hash is reconstructed the same way
        collection_utils._populate_knowledge_documents does it: SHA256 of all chunk
        text concatenated by chunk_index. This guarantees the same hash a fresh
        import would produce, so the diff path can compare apples to apples.
        """
        import hashlib as _hashlib

        chunks_by_doc: dict[str, list] = {}
        registry: dict = {}
        with zipfile.ZipFile(inner_zip, 'r') as zf:
            namelist = set(zf.namelist())
            # The export's document registry carries each document's ROW hash (sha256 of
            # the source bytes). Chunks uploaded through the API carry no
            # source_content_hash, so without the registry every such document fell to
            # the chunk-text fallback and was re-ingested on every update.
            if "document_registry.json" in namelist:
                try:
                    with zf.open("document_registry.json") as rf:
                        _reg = json.loads(rf.read().decode("utf-8", errors="replace"))
                    registry = _reg if isinstance(_reg, dict) else {}
                except Exception:
                    registry = {}
            if "documents.jsonl" in namelist:
                with zf.open("documents.jsonl") as f:
                    for raw in f:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        batch = json.loads(line)
                        ids = batch.get("ids", [])
                        docs = batch.get("documents", [])
                        metas = batch.get("metadatas", []) or [{} for _ in ids]
                        embs = batch.get("embeddings", []) or [None for _ in ids]
                        for i, chunk_id in enumerate(ids):
                            meta = metas[i] if i < len(metas) else {}
                            doc_id = (meta or {}).get("document_id", "")
                            if not doc_id:
                                continue
                            chunks_by_doc.setdefault(doc_id, []).append({
                                "chunk_id": chunk_id,
                                "content": docs[i] if i < len(docs) else "",
                                "metadata": meta or {},
                                "embedding": embs[i] if i < len(embs) else None,
                            })
            elif "documents.json" in namelist:
                with zf.open("documents.json") as f:
                    batch = json.loads(f.read().decode("utf-8", errors="replace"))
                ids = batch.get("ids", [])
                docs = batch.get("documents", [])
                metas = batch.get("metadatas", []) or [{} for _ in ids]
                embs = batch.get("embeddings", []) or [None for _ in ids]
                for i, chunk_id in enumerate(ids):
                    meta = metas[i] if i < len(metas) else {}
                    doc_id = (meta or {}).get("document_id", "")
                    if not doc_id:
                        continue
                    chunks_by_doc.setdefault(doc_id, []).append({
                        "chunk_id": chunk_id,
                        "content": docs[i] if i < len(docs) else "",
                        "metadata": meta or {},
                        "embedding": embs[i] if i < len(embs) else None,
                    })
            else:
                raise ValueError(
                    f"Collection ZIP {inner_zip.name} has neither documents.jsonl "
                    f"nor documents.json"
                )

        result = {}
        for doc_id, chunks in chunks_by_doc.items():
            sorted_chunks = sorted(chunks, key=lambda c: c["metadata"].get("chunk_index", 0))
            first_meta = sorted_chunks[0]["metadata"]
            # Prefer the build-time-stamped source_content_hash (sha256 of raw
            # file bytes — matches what CDC sync stores in
            # knowledge_documents.content_hash).  Fall back to a chunk-text
            # hash if the pack was built without that field, but the diff
            # will then conservatively mark all docs as updated.
            _reg_hash = (registry.get(doc_id) or {}).get("content_hash") if isinstance(registry.get(doc_id), dict) else None
            source_content_hash = first_meta.get("source_content_hash") or _reg_hash
            _authoritative = bool(first_meta.get("source_content_hash") or _reg_hash)
            if not source_content_hash:
                combined_text = " ".join(c["content"] or "" for c in sorted_chunks)
                source_content_hash = _hashlib.sha256(
                    combined_text.encode("utf-8", errors="replace")
                ).hexdigest()
            result[doc_id] = {
                "chunks": sorted_chunks,
                "content_hash": source_content_hash,
                "filename": first_meta.get("filename", ""),
                "title": first_meta.get("title", first_meta.get("filename", "")),
                "category": first_meta.get("category", ""),
                "tags": first_meta.get("tags", ""),
                "document_type": first_meta.get("document_type", "markdown"),
                "author": first_meta.get("author", ""),
                "has_source_content_hash": _authoritative,
            }
        return result

    @classmethod
    def _planner_mcp_for_ref(cls, ref: str, manifest: dict, name_map: dict,
                             tag_bindings: dict | None,
                             pack_mcp_server_id: str | None) -> str | None:
        """Which MCP server a PLANNER collection must be keyed to.

        A planner repository is loaded by the retriever only for sessions on the server
        it is keyed to, so an unkeyed one is never read: the agent installs, looks
        healthy, and silently never benefits from a single champion case. Taking the
        key from the pack-level ``mcp_server_id`` alone left exactly that hole, because
        an importer who does not know to pass it gets None.

        The right key is not a pack-level default at all — it is the endpoint of the
        AGENT THAT READS THIS REPOSITORY, resolved the same way that agent's own
        binding is (explicit tag binding first, then the manifest's server name). The
        pack-level id remains the fallback for a manifest that names no reader.
        """
        readers = [
            p for p in (manifest.get("profiles") or [])
            if ref in (p.get("collection_refs") or [])
            and p.get("profile_type") == "tool_enabled"
        ]
        for prof in readers:
            explicit = cls._tag_binding(tag_bindings, prof.get("tag"))
            if explicit:
                return explicit
            wanted = (prof.get("mcpServerName") or "").strip()
            by_name = (name_map or {}).get(wanted.lower()) if wanted else None
            if by_name:
                return by_name
        return pack_mcp_server_id

    @staticmethod
    def _pack_ref(manifest: dict) -> str:
        """The token that marks a champion case as THIS PACK's.

        The pack NAME, not the installation id: an id changes when the pack is
        uninstalled and installed again (89 -> 90 on the reference deployment), and
        ownership stamped with it would be unrecognisable afterwards — every case the
        pack had seeded would read as one the agent learned, and could never be
        cleaned again.
        """
        return str((manifest or {}).get("name") or "agent_pack")

    # ── the disk mirror ────────────────────────────────────────────────────────
    #
    # A planner repository is stored TWICE, and the two stores answer different
    # questions: ChromaDB serves retrieval, the on-disk ``case_*.json`` files are the
    # source of truth (``_run_maintenance`` reconciles ChromaDB *from* disk, and
    # ``trust.content.planner_cases`` hashes disk — it is what the signature covers).
    # The platform's own seeding path writes BOTH; pack import wrote only ChromaDB, so
    # a pack-installed planner was invisible to the trust hash: the consumer's
    # composition omitted the repository the publisher had signed, the hashes could
    # never agree, and the honesty gate refused to reinstate the publisher's signature
    # forever. Observed on the reference deployment as an agent stuck ``unsigned`` that
    # its non-admin owner had no way to sign.
    #
    # Every write below therefore mirrors: add / update / remove / stamp on both.

    @staticmethod
    def _planner_case_dir(collection_id: int):
        from trusted_data_agent.core.config import APP_CONFIG
        from trusted_data_agent.core.utils import get_project_root
        return (get_project_root() / APP_CONFIG.RAG_CASES_DIR
                / f"collection_{int(collection_id)}")

    @staticmethod
    def _planner_case_filename(chroma_id: str) -> str:
        """Disk filename for a ChromaDB case id — the seeder's convention, kept so the
        two writers can never disagree about where a case lives."""
        cid = str(chroma_id or "")
        return f"{cid}.json" if cid.startswith("case_") else f"case_{cid}.json"

    @classmethod
    def _mirror_planner_case_to_disk(cls, collection_id: int, chroma_id: str,
                                     metadata: dict, document: str = "") -> bool:
        """Write one case to disk, in the shape the seeder writes and the hash reads.

        The case study travels inside the ChromaDB metadata as ``full_case_data`` (the
        export carries it verbatim), so the mirror is a derivation, never a second
        authoring path: the flat metadata is overlaid on top, so the two stores agree by
        construction rather than by both being written correctly. Exactly ONE field is
        rewritten to local truth — ``collection_id`` — because it is the one the import
        does not re-key, and a file inside ``collection_521/`` naming the publisher's 382
        is a disagreement the maintenance re-sync writes back into ChromaDB. The
        signature is unaffected: ``trust.content`` strips the instance-local fields before
        hashing, which is what lets a faithful copy still reinstate.

        Fails soft and says so: a mirror that cannot be written leaves the repository as
        it is today, which is the status quo rather than a corruption.
        """
        import json as _json
        try:
            md = dict(metadata or {})
            raw = md.pop("full_case_data", None)
            case = None
            if raw:
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        case = parsed
                except Exception:
                    case = None
            if case is None:
                # No carried case study (a hand-built or legacy export): reconstruct the
                # minimum the retriever and the hash both read, rather than skip — an
                # unmirrored case is one the signature cannot cover.
                case = {"metadata": {},
                        "intent": {"user_query": md.get("user_query") or document or ""}}

            cid = str(chroma_id or "")
            case["case_id"] = cid[len("case_"):] if cid.startswith("case_") else cid
            cmeta = case.setdefault("metadata", {})
            if not isinstance(cmeta, dict):
                cmeta = {}
                case["metadata"] = cmeta
            # The flat metadata is authoritative: it is what the diff compared on
            # (`same_sql` reads `metadata["sql_statement"]`), and it is derived from the
            # case study by `_prepare_chroma_metadata`, so overlaying it makes the two
            # stores agree by construction rather than by both being written correctly.
            # Without it an export carrying no `full_case_data` mirrors a case whose SQL
            # is absent from disk — present in ChromaDB, invisible to the hash.
            cmeta.update({k: v for k, v in md.items() if v is not None})
            # ONE field is rewritten, and only this one. The import re-keys
            # ``mcp_server_id`` itself and the overlay above carries that through; it
            # does NOT re-key ``collection_id``, so without this line the file inside
            # ``collection_521/`` would name the publisher's 382 — a disagreement the
            # maintenance re-sync would then write back into ChromaDB.
            cmeta["collection_id"] = int(collection_id)
            # ``user_uuid`` is deliberately NOT rewritten. A pack's cases are seeded
            # through the template generator and carry the TEMPLATE uuid, which is
            # exactly what the owned-collection filter ORs in
            # (rag_access_context.build_query_filter) to make them retrievable for any
            # consumer. Rewriting it to the installing user would be written back to
            # ChromaDB by the next maintenance pass and silently narrow that.
            # The ownership stamp likewise rides the overlay — it is on the ChromaDB
            # metadata by the time anything is mirrored — and must reach DISK, because
            # the hash reads disk and the baseline formula is selected by its presence.

            d = cls._planner_case_dir(collection_id)
            d.mkdir(parents=True, exist_ok=True)
            (d / cls._planner_case_filename(cid)).write_text(
                _json.dumps(case, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            app_logger.warning(
                f"planner disk mirror failed for case {chroma_id} in collection "
                f"{collection_id}: {e} — the repository stays as it is; its trust hash "
                f"will not reflect this case.")
            return False

    @classmethod
    def _remove_planner_case_from_disk(cls, collection_id: int, chroma_id: str) -> bool:
        """Delete a case's file. Symmetry is the point: leaving the file behind would let
        ``_run_maintenance`` re-add to ChromaDB the very case the update just removed."""
        try:
            f = cls._planner_case_dir(collection_id) / cls._planner_case_filename(chroma_id)
            if f.exists():
                f.unlink()
            return True
        except Exception as e:
            app_logger.warning(
                f"planner disk mirror delete failed for case {chroma_id} in collection "
                f"{collection_id}: {e}")
            return False

    @staticmethod
    def _apply_planner_lock(collection_id: int, coll_entry: dict) -> None:
        """Write the manifest's lock onto the repository — ONLY when the entry says
        so. Absence preserves whatever the consumer set; the downgrade guard in
        `_handle_update_in_place` has already refused an explicit unlock unless the
        consumer accepted it, so an explicit value here is always one to apply."""
        if not isinstance(coll_entry, dict) or "locked" not in coll_entry:
            return
        try:
            from trusted_data_agent.core.collection_db import get_collection_db
            get_collection_db().update_collection(int(collection_id),
                                                  {"trust_locked": 1 if coll_entry.get("locked") else 0})
        except Exception as e:
            app_logger.warning(f"planner lock not applied to collection {collection_id}: {e}")

    @staticmethod
    def _planner_store_name(collection_id: int, collection_meta: dict | None) -> str:
        """The name a planner's ChromaDB store is reached by — read from the
        collection's OWN row, never from a caller's hint.

        The hint is what let the stamp never fire on a fresh install:
        ``import_collection_from_zip`` returns ``collection_name`` = the DISPLAY name
        ("Policing Data Products — Planner"), while the store is named
        ``col_<uuid>_<ts>_<hex>``. Both create sites handed that display name in, the
        lookup raised ``Collection [...] does not exist``, the soft-fail swallowed it,
        and the log read "stamped 0 case(s)" on every install ever made — the gate's
        fake accepted any name, so the mirror was never load-bearing under test. One
        resolver here means no caller can get it wrong again; the hint survives only as
        the fallback for a row the DB does not have (a test double, a race).
        """
        try:
            from trusted_data_agent.core.collection_db import get_collection_db
            row = get_collection_db().get_collection_by_id(int(collection_id)) or {}
            name = row.get("collection_name") if isinstance(row, dict) else None
            if name:
                return str(name)
        except Exception:
            pass
        return (collection_meta or {}).get("collection_name") or f"collection_{collection_id}"

    async def _stamp_planner_ownership(self, collection_id: int, collection_meta: dict,
                                       pack_ref: str, retriever=None) -> int:
        """Mark every case in a freshly INSTALLED planner collection as the pack's.

        Without this the create path leaves its cases unowned, and the update path --
        which must never delete what it does not own -- can then never remove a case
        the pack shipped once and later dropped. Observed: a consumer left holding five
        cases for a product its endpoint cannot even reach, uncleanable by any later
        update, so the pack had to be re-initialised instead of refreshed.

        Returns the number of cases stamped. Failure is reported, never fatal: an
        unstamped repository is the status quo, not a corruption.
        """
        if retriever is None:
            from trusted_data_agent.agent.rag_retriever import get_rag_retriever
            retriever = get_rag_retriever()
        if retriever is None:
            return 0
        coll_name = self._planner_store_name(collection_id, collection_meta)
        try:
            store = retriever.client.get_collection(name=coll_name)
            existing = store.get(include=["metadatas", "documents"])
            ids = existing.get("ids") or []
            metas = existing.get("metadatas") or []
            docs = existing.get("documents") or []
            if not ids:
                return 0
            stamped = []
            for i, cid in enumerate(ids):
                md = dict((metas[i] if i < len(metas) else {}) or {})
                md["uderia_pack_ref"] = pack_ref
                stamped.append(md)
            store.update(ids=ids, metadatas=stamped)
            # Mirror to disk. On a FRESH install the case files do not exist at all —
            # the import writes only ChromaDB — so this is where a pack-installed
            # planner first becomes visible to the trust hash.
            mirrored = 0
            for i, cid in enumerate(ids):
                if self._mirror_planner_case_to_disk(
                        collection_id, cid, stamped[i],
                        docs[i] if i < len(docs) else ""):
                    mirrored += 1
            if mirrored != len(ids):
                app_logger.warning(
                    f"planner collection {collection_id}: mirrored {mirrored} of "
                    f"{len(ids)} case(s) to disk; the trust hash covers only what is "
                    f"on disk.")
            return len(ids)
        except Exception as e:
            app_logger.warning(
                f"planner ownership stamp failed for collection {collection_id}: {e}")
            return 0

    @staticmethod
    def _planner_case_content_id(meta: dict):
        """What a case SAYS, or None when it cannot be told.

        Reads the case study the export carries in ``full_case_data`` and hashes it
        through the SAME function the trust baseline uses, so the update and the
        signature can never disagree about whether a case changed.
        """
        raw = (meta or {}).get("full_case_data")
        if not raw:
            return None
        try:
            import json as _json
            study = _json.loads(raw)
            if not isinstance(study, dict):
                return None
            from trusted_data_agent.core.trust.content import case_content_id
            return case_content_id(study)
        except Exception:
            return None

    @staticmethod
    def _planner_case_key(meta: dict) -> str:
        """The identity of a champion case ACROSS instances.

        Not its id: case ids are minted per instance (the export carries the
        publisher's), so they cannot match. The question is what the retriever
        matches a user's turn against, so it is what makes two cases the same case —
        the same key `build.py`'s top-up already dedupes on.
        """
        return " ".join(str((meta or {}).get("user_query") or "").split()).lower()

    async def _diff_and_patch_planner_collection(
        self,
        collection_id: int,
        collection_meta: dict,
        inner_zip,
        pack_ref: str,
        retriever=None,
    ) -> dict:
        """Apply case-level diff to one PLANNER collection.

        Three things make this different from the knowledge path, and each is a rule
        rather than a convenience:

        * **The store is reached by NAME, through the raw client.** A planner
          repository is loaded into the retriever only for sessions on the MCP server
          it is keyed to, so `retriever.collections[id]` is empty here more often than
          not — and an empty read is indistinguishable from an empty repository. Going
          through `client.get_collection(name=...)` (what the export path does) makes
          the read mean what it says regardless of context.
        * **Learned cases are never removed.** A planner repository accumulates
          champion cases the agent seeded from its own successful turns. Removal is
          scoped to cases THIS pack put there (`uderia_pack_ref`); anything else is the
          consumer's and is counted as kept, not as residue.
        * **Identity is the question, not the id** — see `_planner_case_key`.
        """
        import json as _json
        import zipfile as _zip

        if retriever is None:
            from trusted_data_agent.agent.rag_retriever import get_rag_retriever
            retriever = get_rag_retriever()
        if retriever is None:
            raise RuntimeError("update_in_place: RAG retriever not initialised")

        coll_name = self._planner_store_name(collection_id, collection_meta)
        try:
            store = retriever.client.get_collection(name=coll_name)
        except Exception as e:
            raise RuntimeError(
                f"update_in_place: planner store '{coll_name}' not reachable: {e}"
            ) from e

        # ── incoming, from the pack ──
        # Every case in a pack export carries the PUBLISHER's server key; the consumer's
        # retrieval filters on the key of the collection it reads, so the case is
        # re-keyed to THIS collection's server exactly as the fresh install re-keys it
        # (collection_utils.stamp_planner_server_key — one rule, both paths).
        from trusted_data_agent.core.collection_utils import stamp_planner_server_key
        target_server = (collection_meta or {}).get("mcp_server_id")
        incoming: dict = {}
        with _zip.ZipFile(inner_zip) as z:
            for line in z.read("documents.jsonl").decode("utf-8").splitlines():
                if not line.strip():
                    continue
                blob = _json.loads(line)
                ids = blob.get("ids") or []
                metas = blob.get("metadatas") or []
                docs = blob.get("documents") or []
                for i, cid in enumerate(ids):
                    meta = dict(metas[i] or {}) if i < len(metas) else {}
                    key = self._planner_case_key(meta)
                    if not key:
                        continue
                    meta["uderia_pack_ref"] = pack_ref
                    stamp_planner_server_key([meta], target_server)
                    incoming[key] = {
                        "id": cid,
                        "metadata": meta,
                        "document": docs[i] if i < len(docs) else "",
                    }

        # ── existing, from the store ──
        existing = store.get(include=["metadatas", "documents"])
        ex_ids = existing.get("ids") or []
        ex_metas = existing.get("metadatas") or []
        ex_docs = existing.get("documents") or []
        by_key: dict = {}
        for i, cid in enumerate(ex_ids):
            meta = ex_metas[i] if i < len(ex_metas) else {}
            by_key.setdefault(self._planner_case_key(meta), []).append(
                {"id": cid, "metadata": meta or {},
                 "document": ex_docs[i] if i < len(ex_docs) else ""})

        added = updated = removed = unchanged = learned_kept = rekeyed = 0

        # add / update
        for key, inc in incoming.items():
            hits = by_key.get(key) or []
            if not hits:
                store.add(ids=[inc["id"]], documents=[inc["document"]],
                          metadatas=[inc["metadata"]])
                self._mirror_planner_case_to_disk(
                    collection_id, inc["id"], inc["metadata"], inc["document"])
                added += 1
                continue
            keep = hits[0]
            # What changed is decided on the case's CONTENT, through the same function
            # the trust baseline hashes. The flat metadata's `sql_statement` — which
            # this compared before — is EMPTY in a real export (all 14 cases of the
            # reference pack): the SQL lives inside the carried case study. So every
            # case compared equal, every case counted "unchanged", and a champion case
            # the publisher had rewritten never reached a consumer at all — leaving the
            # publisher signing a baseline the consumer could never hold, which is the
            # unreinstatable state this whole path exists to prevent. The old field
            # comparison remains the fallback for an export carrying no case study.
            inc_cid = self._planner_case_content_id(inc["metadata"])
            keep_cid = self._planner_case_content_id(keep["metadata"])
            if inc_cid is not None and keep_cid is not None:
                same_sql = (inc_cid == keep_cid)
            else:
                same_sql = (str((keep["metadata"] or {}).get("sql_statement") or "")
                            == str(inc["metadata"].get("sql_statement") or ""))
            if same_sql:
                # Stamp ownership even when the content matches, so a case this pack
                # seeded before the stamp existed becomes removable on a later update.
                md = dict(keep["metadata"] or {})
                _touched = md.get("uderia_pack_ref") != pack_ref
                if _touched:
                    md["uderia_pack_ref"] = pack_ref
                # HEAL a case an earlier update wrote under the publisher's key: it has
                # been in this store and invisible to retrieval since; the next update
                # is the one place every consumer passes through.
                if stamp_planner_server_key([md], target_server):
                    _touched = True
                    rekeyed += 1
                if _touched:
                    store.update(ids=[keep["id"]], metadatas=[md])
                # Mirror even when nothing changed. This is what HEALS an install made
                # before the mirror existed: its cases are in ChromaDB and stamped, but
                # absent from disk — so the repository is invisible to the trust hash and
                # its agent cannot be signed. Without this, an unchanged case would be
                # skipped and the update would leave exactly that state in place.
                self._mirror_planner_case_to_disk(
                    collection_id, keep["id"], md, keep["document"])
                unchanged += 1
            else:
                store.update(ids=[keep["id"]], documents=[inc["document"]],
                             metadatas=[inc["metadata"]])
                self._mirror_planner_case_to_disk(
                    collection_id, keep["id"], inc["metadata"], inc["document"])
                updated += 1
            # A duplicate of a case the pack owns is residue from an earlier
            # unguarded top-up; collapse it. A duplicate the pack does not own is
            # the consumer's and is left alone.
            for dup in hits[1:]:
                if (dup["metadata"] or {}).get("uderia_pack_ref") == pack_ref:
                    store.delete(ids=[dup["id"]])
                    self._remove_planner_case_from_disk(collection_id, dup["id"])
                    removed += 1
                else:
                    learned_kept += 1

        # remove only what THIS pack put there and no longer ships
        for key, hits in by_key.items():
            if key in incoming:
                continue
            for row in hits:
                if (row["metadata"] or {}).get("uderia_pack_ref") == pack_ref:
                    store.delete(ids=[row["id"]])
                    self._remove_planner_case_from_disk(collection_id, row["id"])
                    removed += 1
                else:
                    learned_kept += 1

        try:
            from trusted_data_agent.agent.rag_retriever import get_rag_retriever
            retriever = get_rag_retriever()
            if retriever is not None:
                retriever.refresh_persisted_count(collection_id)
            else:
                from trusted_data_agent.core.collection_db import get_collection_db
                get_collection_db().update_counts(collection_id, chunk_count=store.count())
        except Exception as e:  # a counter is reporting, never the write itself
            app_logger.warning(f"update_in_place: planner count refresh failed: {e}")

        if rekeyed:
            app_logger.info(f"update_in_place: planner {collection_id}: re-keyed {rekeyed} "
                            f"case(s) to this collection's server {target_server}")
        return {"added": added, "updated": updated, "removed": removed,
                "unchanged": unchanged, "learned_kept": learned_kept, "rekeyed": rekeyed}

    async def _diff_and_patch_collection(
        self,
        collection_id: int,
        collection_meta: dict,
        inner_zip: Path,
        incoming: dict,
        existing_by_id: dict,
        to_add: list[str],
        to_update: list[str],
        to_remove: list[str],
        retriever,
    ) -> dict:
        """Apply chunk-level diff to one knowledge collection.

        Preserves collection.id and per-document CDC config (source_uri,
        sync_enabled). Returns counts dict.
        """
        from trusted_data_agent.core.collection_db import get_collection_db
        from trusted_data_agent.vectorstore import (
            FieldFilter, FilterOp, VectorDocument,
        )

        coll_db = get_collection_db()
        backend = await retriever._get_knowledge_backend(collection_id) if retriever else None
        if backend is None:
            raise RuntimeError(
                f"update_in_place: no knowledge backend resolved for collection {collection_id}"
            )

        coll_meta = retriever.get_collection_metadata(collection_id) if retriever else None
        coll_name = (
            coll_meta.get("collection_name")
            if coll_meta and coll_meta.get("collection_name")
            else collection_meta.get("collection_name") or f"collection_{collection_id}"
        )

        ingest_epoch = int(datetime.now(timezone.utc).timestamp())

        # ── Sweep ORPHAN chunks: a chunk whose document id owns no row ────────
        # Before the in-place path localized ids, an update wrote the copy's chunks
        # under the exporting instance's document ids (and its rows onto the
        # publisher's). Those chunks carry no row of their own, so the remove loop
        # below cannot see them, and they sat beside the localized chunks as
        # duplicates — measured live: 281 chunks in a store whose 12 rows owned 151,
        # the same chunk served twice into the model's eight slots. The rule is the
        # ownership itself, not a list of ids: a chunk no row owns is a leftover.
        # (The sweep of the CURRENT source ids was too narrow — the leftovers sat
        # under the publisher's PREVIOUS ids.) Bounded, fail-open.
        try:
            _owned = {d["document_id"] for d in existing_by_id.values()} | set(incoming.keys())
            _all = await backend.get(coll_name, include_documents=False, include_metadata=True, limit=100_000)
            _orphans = [d.id for d in (_all.documents if _all else [])
                        if (getattr(d, "metadata", None) or {}).get("document_id") not in _owned]
            if _orphans:
                await backend.delete(coll_name, _orphans)
                app_logger.info(f"  diff_patch[{collection_id}]: swept {len(_orphans)} orphan chunk(s) owned by no document row")
        except Exception as e:
            app_logger.warning(f"  diff_patch[{collection_id}]: orphan sweep failed: {e}")

        # ── Remove gone documents ────────────────────────────────────────────
        for doc_id in to_remove:
            try:
                get_result = await backend.get(
                    coll_name,
                    where=FieldFilter("document_id", FilterOp.EQ, doc_id),
                    include_documents=False,
                    include_metadata=False,
                )
                chunk_ids = [d.id for d in get_result.documents]
                if chunk_ids:
                    await backend.delete(coll_name, chunk_ids)
            except Exception as e:
                app_logger.warning(
                    f"  diff_patch[{collection_id}]: failed to delete chunks for {doc_id}: {e}"
                )
            # Drop the metadata row regardless of backend outcome
            conn = coll_db._get_connection()
            try:
                conn.execute(
                    "DELETE FROM knowledge_documents WHERE collection_id = ? AND document_id = ?",
                    (collection_id, doc_id),
                )
                conn.commit()
            finally:
                conn.close()

        # ── Update changed documents ────────────────────────────────────────
        for doc_id in to_update:
            info = incoming[doc_id]
            try:
                get_result = await backend.get(
                    coll_name,
                    where=FieldFilter("document_id", FilterOp.EQ, doc_id),
                    include_documents=False,
                    include_metadata=False,
                )
                stale_ids = [d.id for d in get_result.documents]
                if stale_ids:
                    await backend.delete(coll_name, stale_ids)
            except Exception as e:
                app_logger.warning(
                    f"  diff_patch[{collection_id}]: failed to clear stale chunks for {doc_id}: {e}"
                )

            new_docs = [
                VectorDocument(
                    id=c["chunk_id"],
                    content=c["content"] or "",
                    metadata={k: v for k, v in (c["metadata"] or {}).items() if v is not None},
                    embedding=c.get("embedding") or None,
                )
                for c in info["chunks"]
            ]
            await backend.add(coll_name, new_docs)
            coll_db.upsert_document_metadata_preserving_cdc(
                document_id=doc_id,
                collection_id=collection_id,
                filename=info["filename"],
                content_hash=info["content_hash"],
                ingest_epoch=ingest_epoch,
                chunk_count=len(new_docs),
                document_type=info["document_type"],
                title=info["title"],
                author=info["author"],
                category=info["category"],
                tags=info["tags"],
                source="update_in_place",
            )
            # RAG Trust Hardening Wave 2: re-scan updated content (observe-only).
            try:
                from trusted_data_agent.agent.guardrails import corpus_screen
                corpus_screen.scan_and_store(
                    doc_id, info["filename"],
                    "\n".join((c.get("content") or "") for c in info["chunks"]),
                    collection_id=collection_id)
            except Exception:
                pass

        # ── Add new documents ────────────────────────────────────────────────
        for doc_id in to_add:
            info = incoming[doc_id]
            new_docs = [
                VectorDocument(
                    id=c["chunk_id"],
                    content=c["content"] or "",
                    metadata={k: v for k, v in (c["metadata"] or {}).items() if v is not None},
                    embedding=c.get("embedding") or None,
                )
                for c in info["chunks"]
            ]
            await backend.add(coll_name, new_docs)
            # New docs have no existing CDC config to preserve.
            coll_db.upsert_document_metadata(
                document_id=doc_id,
                collection_id=collection_id,
                filename=info["filename"],
                content_hash=info["content_hash"],
                ingest_epoch=ingest_epoch,
                chunk_count=len(new_docs),
                source_uri=None,
                sync_enabled=0,
                document_type=info["document_type"],
                title=info["title"],
                author=info["author"],
                category=info["category"],
                tags=info["tags"],
                source="update_in_place",
            )
            # RAG Trust Hardening Wave 2: ingest firewall on pack-imported documents
            # (observe-only; scans the chunk contents that were just materialized).
            try:
                from trusted_data_agent.agent.guardrails import corpus_screen
                corpus_screen.scan_and_store(
                    doc_id, info["filename"],
                    "\n".join((c.get("content") or "") for c in info["chunks"]),
                    collection_id=collection_id)
            except Exception:
                pass

        return {
            "added": len(to_add),
            "updated": len(to_update),
            "removed": len(to_remove),
            "unchanged": len(incoming) - len(to_add) - len(to_update),
        }

    def _get_marketplace_listing_for_installation(
        self, installation_id: int
    ) -> tuple[str | None, str | None]:
        """Return (listing_id, listing_version) for a published pack, or (None, None)."""
        conn = sqlite_util.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, version FROM marketplace_agent_packs WHERE source_installation_id = ?",
                (installation_id,),
            ).fetchone()
            if row:
                return row["id"], row["version"]
            return None, None
        finally:
            conn.close()

    def _update_installation_metadata(
        self,
        installation_id: int,
        manifest: dict,
        manifest_profiles: list,
        tag_to_profile_id: dict,
    ) -> None:
        """UPDATE the agent_pack_installations row in place; preserve id + installed_at."""
        coordinator_tag = next(
            (p["tag"] for p in manifest_profiles
             if p["profile_type"] == "genie" and p.get("role") == "coordinator"),
            None,
        )
        coordinator_profile_id = (
            tag_to_profile_id.get(coordinator_tag) if coordinator_tag else None
        )
        conn = sqlite_util.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE agent_pack_installations
                SET name = ?, description = ?, version = ?, author = ?,
                    coordinator_tag = ?, coordinator_profile_id = ?,
                    manifest_json = ?
                WHERE id = ?
                """,
                (
                    manifest.get("name"),
                    manifest.get("description", ""),
                    manifest.get("version", "1.0.0"),
                    manifest.get("author", ""),
                    coordinator_tag,
                    coordinator_profile_id,
                    json.dumps(manifest),
                    installation_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_marketplace_listing(
        self,
        listing_id: str,
        manifest: dict,
        profile_count: int,
        collection_count: int,
        profile_tags: list[str],
    ) -> None:
        """UPDATE the marketplace_agent_packs row in place; preserve id + published_at.

        Stamps ``content_updated_at`` beside ``updated_at``: this is the ONE place a
        listing's content moves after publish, and the stamp subscribers compare their
        ``synced_at`` against (``updated_at`` moves on every subscribe and fork too)."""
        coordinator_tag = next(
            (p["tag"] for p in manifest["profiles"]
             if p["profile_type"] == "genie" and p.get("role") == "coordinator"),
            None,
        )
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite_util.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE marketplace_agent_packs
                SET name = ?, description = ?, version = ?, author = ?,
                    profile_count = ?, collection_count = ?,
                    coordinator_tag = ?, profile_tags = ?,
                    updated_at = ?, content_updated_at = ?
                WHERE id = ?
                """,
                (
                    manifest.get("name"),
                    manifest.get("description", ""),
                    manifest.get("version", "1.0.0"),
                    manifest.get("author", ""),
                    profile_count,
                    collection_count,
                    coordinator_tag,
                    json.dumps(profile_tags),
                    now,
                    now,   # the CONTENT moved — what a subscriber's "update available" reads
                    listing_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
