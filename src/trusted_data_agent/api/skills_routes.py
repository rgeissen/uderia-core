"""
REST API routes for skills management.

Skills are pre-processing prompt injections — reusable markdown knowledge/instruction
documents that get injected into the LLM's context when activated.

Endpoints:
- GET  /v1/skills                               — List available skills
- POST /v1/skills/reload                         — Hot-reload skills from disk
- GET  /v1/skills/activated                      — Get user's activated skills
- POST /v1/skills/<skill_id>/activate            — Activate a skill
- POST /v1/skills/activations/<name>/deactivate  — Deactivate a skill
- PUT  /v1/skills/activations/<name>/config      — Update default param
- PUT  /v1/skills/activations/<name>/rename      — Rename activation
- DELETE /v1/skills/activations/<name>           — Delete activation
- GET  /v1/skills/<skill_id>/content             — Get skill content (for editor)
- PUT  /v1/skills/<skill_id>                     — Update/create skill (save)
- DELETE /v1/skills/<skill_id>                   — Delete user-created skill
- POST /v1/skills/<skill_id>/export              — Export skill as .skill (Claude Code compatible)
- POST /v1/skills/import                         — Import skill from .skill/.zip

Marketplace:
- POST /v1/skills/<skill_id>/publish             — Publish skill to marketplace
- GET  /v1/marketplace/skills                     — Browse marketplace
- GET  /v1/marketplace/skills/<id>                — Skill detail + ratings
- POST /v1/marketplace/skills/<id>/install        — Install from marketplace
- POST /v1/marketplace/skills/<id>/rate           — Rate/review
- DELETE /v1/marketplace/skills/<id>              — Unpublish
"""

from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
from trusted_data_agent.api.operations import operation
import json
import logging
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from quart import Blueprint, jsonify, request, send_file

from trusted_data_agent.auth.admin import get_current_user_from_request

skills_api_bp = Blueprint("skills_api", __name__)
app_logger = logging.getLogger("quart.app")


def _parse_frontmatter_static(content: str) -> "tuple[dict, str]":
    """Parse YAML frontmatter from markdown content (no PyYAML dependency).

    Returns (fields_dict, body_without_frontmatter).
    """
    if not content.startswith("---"):
        return {}, content
    try:
        end = content.index("\n---", 3)
    except ValueError:
        return {}, content
    yaml_block = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")
    fields: dict = {}
    for line in yaml_block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    return fields, body


def _get_user_uuid_from_request():
    """Extract user ID from request (from auth token or header)."""
    user = get_current_user_from_request()
    if user:
        return user.id
    return None


# ---------------------------------------------------------------------------
# List & Discovery
# ---------------------------------------------------------------------------

@skills_api_bp.route("/v1/skills", methods=["GET"])
@operation("skills.list", effect="read", scope="instance",
           summary=("The skills available on this deployment — expertise added to every turn: what each "
                    "skill teaches and whether it is built in or the user's own"),
           summary_fields=("skill_id", "description", "component_id"))
async def list_skills():
    """
    List all available skills (built-in + user).

    Enforces admin governance: disabled skills are filtered out.
    If user skills are disabled, hides user-created skills.

    Returns:
        { "skills": [...], "_settings": { "user_skills_enabled": bool } }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills.settings import get_skill_settings, is_skill_available

        manager = get_skill_manager()
        skills = manager.list_skills()

        # Enforce admin governance
        skills = [s for s in skills if is_skill_available(s["skill_id"])]

        # If user skills disabled, hide user-created skills
        settings = get_skill_settings()
        if not settings.get("user_skills_enabled", True):
            skills = [s for s in skills if s.get("is_builtin")]

        # Annotate with marketplace publish status for current user
        import sqlite3
        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        try:
            conn = sqlite_util.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT skill_id, id, visibility FROM marketplace_skills WHERE publisher_user_id = ?",
                (user_uuid,),
            )
            published = {row["skill_id"]: {"marketplace_id": row["id"], "visibility": row["visibility"]}
                         for row in cursor.fetchall()}
            conn.close()
        except Exception:
            published = {}

        for s in skills:
            pub = published.get(s["skill_id"])
            s["is_marketplace_listed"] = pub is not None
            s["marketplace_id"] = pub["marketplace_id"] if pub else None

        return jsonify({
            "skills": skills,
            "_settings": {
                "user_skills_enabled": settings.get("user_skills_enabled", True),
                "user_skills_marketplace_enabled": settings.get("user_skills_marketplace_enabled", True),
            }
        }), 200

    except Exception as e:
        app_logger.error(f"Failed to list skills: {e}", exc_info=True)
        return jsonify({"error": "Failed to list skills."}), 500


@skills_api_bp.route("/v1/skills/reload", methods=["POST"])
async def reload_skills():
    """Hot-reload all skills from disk without restarting the application."""
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.manager import get_skill_manager

        manager = get_skill_manager()
        manager.reload()

        return jsonify({
            "status": "success",
            "loaded": len(manager.manifests),
            "skills": list(manager.manifests.keys())
        }), 200

    except Exception as e:
        app_logger.error(f"Failed to reload skills: {e}", exc_info=True)
        return jsonify({"error": "Failed to reload skills."}), 500


# ---------------------------------------------------------------------------
# User Activations
# ---------------------------------------------------------------------------

@skills_api_bp.route("/v1/skills/activated", methods=["GET"])
async def get_activated_skills():
    """
    Get the current user's activated skills, merged with manifest metadata.

    This is what the frontend uses for # autocomplete.
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.db import get_user_activated_skills
        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills.settings import is_skill_available

        activations = get_user_activated_skills(user_uuid)
        manager = get_skill_manager()

        # Merge activation data with manifest metadata
        result = []
        for activation in activations:
            skill_id = activation["skill_id"]
            if not is_skill_available(skill_id):
                continue
            skill_info = next(
                (s for s in manager.list_skills() if s["skill_id"] == skill_id),
                None,
            )
            if skill_info:
                merged = {**skill_info, **activation}
                result.append(merged)

        return jsonify({"skills": result}), 200

    except Exception as e:
        app_logger.error(f"Failed to get activated skills: {e}", exc_info=True)
        return jsonify({"error": "Failed to get activated skills."}), 500


@skills_api_bp.route("/v1/skills/<skill_id>/activate", methods=["POST"])
async def activate_skill_endpoint(skill_id: str):
    """
    Activate a skill for the current user.

    Request body (optional):
    {
        "default_param": "strict",
        "config": {"key": "value"},
        "activation_name": "myalias"  (optional — auto-generated if omitted)
    }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.db import activate_skill
        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills.settings import is_skill_available

        # Enforce admin governance
        if not is_skill_available(skill_id):
            return jsonify({"error": f"Skill '{skill_id}' has been disabled by the administrator."}), 403

        # Validate skill exists
        manager = get_skill_manager()
        if skill_id not in manager.manifests:
            return jsonify({"error": f"Skill '{skill_id}' not found."}), 404

        data = await request.get_json() or {}
        default_param = data.get("default_param")
        config = data.get("config")
        activation_name = data.get("activation_name")

        success, generated_name = activate_skill(
            user_uuid, skill_id, default_param, config, activation_name
        )
        if success:
            return jsonify({
                "status": "activated",
                "skill_id": skill_id,
                "activation_name": generated_name,
            }), 200
        else:
            return jsonify({"error": "Failed to activate skill. Name may already be in use."}), 409

    except Exception as e:
        app_logger.error(f"Failed to activate skill '{skill_id}': {e}", exc_info=True)
        return jsonify({"error": "Failed to activate skill."}), 500


@skills_api_bp.route("/v1/skills/activations/<activation_name>/deactivate", methods=["POST"])
async def deactivate_skill_endpoint(activation_name: str):
    """Deactivate a skill activation by its activation_name."""
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.db import deactivate_skill

        success = deactivate_skill(user_uuid, activation_name)
        if success:
            return jsonify({"status": "deactivated", "activation_name": activation_name}), 200
        else:
            return jsonify({"error": f"Activation '{activation_name}' not found."}), 404

    except Exception as e:
        app_logger.error(f"Failed to deactivate skill '{activation_name}': {e}", exc_info=True)
        return jsonify({"error": "Failed to deactivate skill."}), 500


@skills_api_bp.route("/v1/skills/activations/<activation_name>/config", methods=["PUT"])
async def update_skill_config_endpoint(activation_name: str):
    """
    Update configuration for a skill activation.

    Request body:
    {
        "default_param": "lenient",
        "config": {"key": "value"}
    }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.db import update_skill_config

        data = await request.get_json() or {}
        default_param = data.get("default_param")
        config = data.get("config")

        success = update_skill_config(user_uuid, activation_name, default_param, config)
        if success:
            return jsonify({"status": "updated", "activation_name": activation_name}), 200
        else:
            return jsonify({"error": f"Activation '{activation_name}' is not active."}), 404

    except Exception as e:
        app_logger.error(f"Failed to update skill config: {e}", exc_info=True)
        return jsonify({"error": "Failed to update skill config."}), 500


@skills_api_bp.route("/v1/skills/activations/<activation_name>/rename", methods=["PUT"])
async def rename_skill_activation_endpoint(activation_name: str):
    """
    Rename a skill activation.

    Request body:
    { "new_name": "my_custom_name" }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.db import rename_skill_activation

        data = await request.get_json() or {}
        new_name = data.get("new_name")
        if not new_name:
            return jsonify({"error": "new_name is required."}), 400

        success = rename_skill_activation(user_uuid, activation_name, new_name)
        if success:
            return jsonify({
                "status": "renamed",
                "old_name": activation_name,
                "new_name": new_name,
            }), 200
        else:
            return jsonify({"error": f"Rename failed. Name '{new_name}' may already be in use."}), 409

    except Exception as e:
        app_logger.error(f"Failed to rename skill '{activation_name}': {e}", exc_info=True)
        return jsonify({"error": "Failed to rename skill."}), 500


@skills_api_bp.route("/v1/skills/activations/<activation_name>", methods=["DELETE"])
async def delete_skill_activation_endpoint(activation_name: str):
    """Hard-delete a skill activation (removes the row entirely)."""
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.db import delete_skill_activation

        success = delete_skill_activation(user_uuid, activation_name)
        if success:
            return jsonify({"status": "deleted", "activation_name": activation_name}), 200
        else:
            return jsonify({"error": f"Activation '{activation_name}' not found."}), 404

    except Exception as e:
        app_logger.error(f"Failed to delete skill '{activation_name}': {e}", exc_info=True)
        return jsonify({"error": "Failed to delete skill activation."}), 500


# ---------------------------------------------------------------------------
# Skill Content & CRUD
# ---------------------------------------------------------------------------

@skills_api_bp.route("/v1/skills/<skill_id>/content", methods=["GET"])
async def get_skill_content(skill_id: str):
    """
    Get the full content and manifest of a skill for the editor.

    Returns:
    {
        "skill_id": "sql-expert",
        "content": "# SQL Expert\n...",
        "manifest": { ... },
        "is_builtin": true,
        "file_path": "/path/to/skill.md"
    }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.manager import get_skill_manager

        manager = get_skill_manager()

        if skill_id not in manager.manifests:
            return jsonify({"error": f"Skill '{skill_id}' not found."}), 404

        content = manager.get_skill_full_content(skill_id)
        manifest = manager.get_skill_manifest(skill_id)

        # Determine file path and builtin status from raw manifest
        raw_manifest = manager.manifests[skill_id]
        file_path = str(raw_manifest.get("_dir", "")) if raw_manifest else ""
        is_builtin = not raw_manifest.get("_is_user", False) if raw_manifest else True

        return jsonify({
            "skill_id": skill_id,
            "content": content or "",
            "manifest": manifest or {},
            "is_builtin": is_builtin,
            "file_path": file_path,
        }), 200

    except Exception as e:
        app_logger.error(f"Failed to get skill content for '{skill_id}': {e}", exc_info=True)
        return jsonify({"error": "Failed to get skill content."}), 500


@skills_api_bp.route("/v1/skills/<skill_id>", methods=["PUT"])
async def save_skill_endpoint(skill_id: str):
    """
    Create or update a user skill.

    Saves to ~/.tda/skills/<skill_id>/ directory.
    Built-in skills cannot be overwritten directly — saving creates a user override.

    Request body:
    {
        "content": "# My Skill\n\nInstructions...",
        "manifest": {
            "name": "my-skill",
            "description": "...",
            "tags": ["tag1"],
            ...
        }
    }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.settings import are_user_skills_enabled
        if not are_user_skills_enabled():
            return jsonify({
                "error": "Custom skill creation has been disabled by the administrator."
            }), 403

        from trusted_data_agent.skills.manager import get_skill_manager

        data = await request.get_json()
        if not data:
            return jsonify({"error": "No data provided."}), 400

        content = data.get("content", "")
        manifest_data = data.get("manifest", {})

        if not content.strip():
            return jsonify({"error": "Skill content cannot be empty."}), 400

        manager = get_skill_manager()
        manager.save_skill(skill_id, content, manifest_data)

        return jsonify({
            "status": "saved",
            "skill_id": skill_id,
            "message": f"Skill '{skill_id}' saved successfully.",
        }), 200

    except Exception as e:
        app_logger.error(f"Failed to save skill '{skill_id}': {e}", exc_info=True)
        return jsonify({"error": f"Failed to save skill: {str(e)}"}), 500


@skills_api_bp.route("/v1/skills/<skill_id>", methods=["DELETE"])
async def delete_skill_endpoint(skill_id: str):
    """
    Delete a user-created skill from disk.

    Rejects if:
      - Skill is built-in (403)
      - Skill not found (404)
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.manager import get_skill_manager

        manager = get_skill_manager()

        if skill_id not in manager.manifests:
            return jsonify({"error": f"Skill '{skill_id}' not found."}), 404

        manager.delete_skill(skill_id)

        return jsonify({
            "status": "deleted",
            "skill_id": skill_id,
            "message": f"Skill '{skill_id}' deleted.",
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        app_logger.error(f"Failed to delete skill '{skill_id}': {e}", exc_info=True)
        return jsonify({"error": f"Failed to delete skill: {str(e)}"}), 500


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------

@skills_api_bp.route("/v1/skills/<skill_id>/versions", methods=["GET"])
async def get_skill_versions(skill_id: str):
    """A skill's content address + archived versions (best-practice step 4).

    ``current`` is the live copy's content hash; ``versions`` are the archived
    snapshots (newest first) that a profile's skillsConfig entry can PIN via
    ``content_hash``. Read-only; the archive is append-only by construction.
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills import versions as skill_versions

        manager = get_skill_manager()
        if skill_id not in manager.manifests:
            return jsonify({"error": f"Skill '{skill_id}' not found."}), 404

        skill_dir = manager.skill_dirs.get(skill_id)
        current = skill_versions.live_skill_hash(skill_dir) if skill_dir else None
        return jsonify({
            "skill_id": skill_id,
            "current_content_hash": current,
            "versions": skill_versions.list_versions(manager.state_dir, skill_id),
        }), 200
    except Exception as e:
        app_logger.error(f"Failed to list versions for skill '{skill_id}': {e}", exc_info=True)
        return jsonify({"error": f"Failed to list skill versions: {e}"}), 500


@skills_api_bp.route("/v1/skills/<skill_id>/library-index", methods=["GET"])
async def get_skill_library_index(skill_id: str):
    """The anchor index of a skill that uses runtime progressive disclosure.

    Feeds the profile editor's Library-access control (the anchor grant —
    `skillsConfig.skills[].anchors`). Lazily fetched when the operator opens the
    control, so a 77-section index never rides the skills list. Metadata only:
    anchor, title, summary — never section content.
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills.package import progressive_index, is_progressive

        manager = get_skill_manager()
        manifest = (getattr(manager, "manifests", {}) or {}).get(skill_id)
        if manifest is None:
            return jsonify({"error": f"Skill '{skill_id}' not found."}), 404
        if not is_progressive(manifest):
            return jsonify({"skill_id": skill_id, "progressive": False,
                            "anchors": [], "count": 0}), 200

        anchors = [
            {"anchor": e.get("anchor"), "title": e.get("title") or e.get("anchor"),
             "summary": e.get("summary") or ""}
            for e in progressive_index(manifest) if e.get("anchor")
        ]
        return jsonify({"skill_id": skill_id, "progressive": True,
                        "anchors": anchors, "count": len(anchors)}), 200
    except Exception as e:
        app_logger.error(f"Failed to read library index for '{skill_id}': {e}", exc_info=True)
        return jsonify({"error": f"Could not read library index: {e}"}), 500


@skills_api_bp.route("/v1/skills/<skill_id>/export", methods=["POST"])
async def export_skill_endpoint(skill_id: str):
    """
    Export a skill as a downloadable .zip file.

    The archive contains:
        skill.json     — Uderia manifest metadata
        <name>.md      — markdown content
        SKILL.md       — Claude Code-compatible (YAML frontmatter + content)

    Works for both built-in and user-created skills.
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.manager import get_skill_manager

        manager = get_skill_manager()

        if skill_id not in manager.manifests:
            return jsonify({"error": f"Skill '{skill_id}' not found."}), 404

        # Get content and manifest
        content = manager.get_skill_full_content(skill_id)
        manifest = dict(manager.get_skill_manifest(skill_id) or {})

        if not content:
            return jsonify({"error": f"Could not retrieve content for '{skill_id}'."}), 404

        # Clean internal metadata keys
        for internal_key in ("_is_user", "_source_path", "_dir", "_auto_generated"):
            manifest.pop(internal_key, None)

        manifest["export_format_version"] = "1.0"
        manifest["exported_at"] = datetime.now(timezone.utc).isoformat()

        # Ensure name is present
        if "name" not in manifest:
            manifest["name"] = skill_id

        # Determine the markdown filename
        main_file = manifest.get("main_file", f"{skill_id}.md")

        # Create ZIP in temp directory
        tmp_dir = tempfile.mkdtemp()
        zip_filename = f"{skill_id}.skill"
        zip_path = Path(tmp_dir) / zip_filename

        # Build Claude Code-compatible SKILL.md (frontmatter + content)
        description_safe = manifest.get("description", "").replace("\n", " ")
        skill_name = manifest.get("name", skill_id)
        skill_md_content = (
            f"---\nname: {skill_name}\ndescription: {description_safe}\n"
            f"user-invocable: true\n---\n\n"
            + content
        )

        # Serialize via the ONE shared serializer (PROP-005); a progressive
        # skill's library payload travels with the export, index-driven.
        from trusted_data_agent.skills.package import build_skill_zip as _pkg_build
        _library = manager.get_skill_library_files(skill_id) or None
        zip_path.write_bytes(_pkg_build(manifest, main_file, content,
                                        skill_md=skill_md_content,
                                        library_files=_library))

        return await send_file(
            str(zip_path),
            mimetype="application/zip",
            as_attachment=True,
            attachment_filename=zip_filename,
        )

    except Exception as e:
        app_logger.error(f"Failed to export skill '{skill_id}': {e}", exc_info=True)
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


@skills_api_bp.route("/v1/skills/import", methods=["POST"])
async def import_skill_endpoint():
    """
    Import a skill from an uploaded .zip file.

    Accepts multipart/form-data with a 'file' field.
    Supports two formats:
      - Uderia format: ZIP with skill.json + <name>.md
      - Claude Code format: ZIP with SKILL.md (YAML frontmatter parsed as manifest)

    Saves to ~/.tda/skills/<skill_id>/ and hot-reloads the skill manager.
    """
    tmp_path = None
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.settings import are_user_skills_enabled
        if not are_user_skills_enabled():
            return jsonify({
                "error": "Custom skill creation has been disabled by the administrator."
            }), 403

        # Get uploaded file
        files = await request.files
        if "file" not in files:
            return jsonify({"error": "No file uploaded. Use 'file' field."}), 400

        uploaded = files["file"]
        if not uploaded.filename:
            return jsonify({"error": "Empty filename."}), 400

        # Save to temp file
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / uploaded.filename
        await uploaded.save(str(tmp_path))

        # Validate it's a valid zip
        if not zipfile.is_zipfile(str(tmp_path)):
            return jsonify({"error": "Uploaded file is not a valid ZIP archive."}), 400

        # Extract via the ONE shared serializer (PROP-005). Mechanism lives there
        # (root-strip, selection order, library extraction with zip-slip guard —
        # a library/*.md can no longer be promoted to the skill body); this
        # endpoint keeps its POLICY: the same error messages and status codes.
        from trusted_data_agent.skills.package import (
            parse_skill_zip, is_progressive, validate_progressive_payload,
        )

        parsed = parse_skill_zip(tmp_path.read_bytes())

        manifest_data = None
        md_content = None
        md_filename = None

        if parsed.manifest is not None:
            # Existing Uderia format — skill.json is the metadata
            manifest_data = parsed.manifest
            md_content = parsed.content if not parsed.content_from_skill_md else None
            md_filename = parsed.md_name
        elif parsed.frontmatter is not None:
            # Claude Code format — SKILL.md frontmatter is the manifest
            fm = parsed.frontmatter
            if not fm.get("name"):
                return jsonify({
                    "error": "SKILL.md frontmatter must include a 'name' field."
                }), 400
            manifest_data = {
                "name": fm["name"],
                "description": fm.get("description", ""),
                "version": fm.get("version", "1.0.0"),
                "author": fm.get("author", "User"),
                "tags": [],
                "keywords": [],
                "use_cases": [],
            }
            if parsed.content_from_skill_md:
                md_content = parsed.content
                md_filename = f"{fm['name']}.md"
            else:
                md_content = parsed.content
                md_filename = parsed.md_name
        else:
            return jsonify({"error": "No skill.json or SKILL.md found in archive."}), 400

        if md_content is None:
            return jsonify({"error": "No .md file found in archive."}), 400

        # Determine skill ID from manifest
        skill_id = manifest_data.get("name")
        if not skill_id:
            # Fall back to filename without extension
            skill_id = Path(md_filename).stem if md_filename else None
            if not skill_id:
                return jsonify({"error": "Could not determine skill id."}), 400

        # Clean export metadata
        manifest_data.pop("export_format_version", None)
        manifest_data.pop("exported_at", None)

        # Ensure main_file points to the correct filename
        manifest_data["main_file"] = Path(md_filename).name if md_filename else f"{skill_id}.md"

        # Progressive payload: verify the index integrity BEFORE materialising —
        # a tampered/incomplete payload is refused, never partially installed.
        library = None
        if is_progressive(manifest_data):
            errors = validate_progressive_payload(manifest_data, parsed.library_files)
            if errors:
                return jsonify({"error": "Progressive skill payload failed integrity checks.",
                                "details": errors}), 400
            library = parsed.library_files

        # Save to user skills directory
        from trusted_data_agent.skills.manager import get_skill_manager

        manager = get_skill_manager()
        manager.save_skill(skill_id, md_content, manifest_data, library_files=library)

        return jsonify({
            "status": "imported",
            "skill_id": skill_id,
            "name": manifest_data.get("name", skill_id),
            "description": manifest_data.get("description", ""),
            "message": f"Skill '{skill_id}' imported successfully.",
        }), 201

    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON in skill.json manifest."}), 400
    except Exception as e:
        app_logger.error(f"Failed to import skill: {e}", exc_info=True)
        return jsonify({"error": f"Import failed: {str(e)}"}), 500
    finally:
        # Cleanup temp file
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ============================================================================
# SKILL MARKETPLACE ENDPOINTS
# ============================================================================

@skills_api_bp.route("/v1/skills/<skill_id>/publish", methods=["POST"])
async def publish_skill(skill_id: str):
    """
    Publish a user-created skill to the marketplace.

    Body (JSON, optional):
    {
        "visibility": "public" | "targeted",
        "user_ids": ["uuid1", "uuid2"]  (required if targeted)
    }
    """
    import hashlib
    import sqlite3
    import uuid as _uuid

    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.settings import is_skill_marketplace_enabled
        if not is_skill_marketplace_enabled():
            return jsonify({"error": "Skill marketplace has been disabled by the administrator."}), 403

        from trusted_data_agent.skills.manager import get_skill_manager
        manager = get_skill_manager()
        manifest = manager.get_skill_manifest(skill_id)
        if not manifest:
            return jsonify({"error": f"Skill '{skill_id}' not found."}), 404

        # Only user-created skills can be published
        if not manifest.get("_is_user"):
            return jsonify({"error": "Only user-created skills can be published."}), 400

        data = (await request.get_json()) or {}
        visibility = data.get("visibility", "public")
        user_ids = data.get("user_ids", [])

        if visibility not in ("public", "targeted"):
            return jsonify({"error": "Visibility must be 'public' or 'targeted'"}), 400
        if visibility == "targeted" and not user_ids:
            return jsonify({"error": "Targeted publish requires at least one user"}), 400

        # Get content for hashing
        content = manager.get_skill_full_content(skill_id) or ""
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Prevent republishing marketplace-installed skills
        cursor.execute(
            "SELECT 1 FROM marketplace_installs mi "
            "JOIN marketplace_skills ms ON ms.id = mi.marketplace_id "
            "WHERE ms.skill_id = ? AND mi.user_id = ? AND mi.artifact_type = 'skill'",
            (skill_id, user_uuid),
        )
        if cursor.fetchone():
            conn.close()
            return jsonify({"error": "Marketplace-installed skills cannot be republished."}), 403

        # Check if already published by this user
        cursor.execute(
            "SELECT id FROM marketplace_skills WHERE skill_id = ? AND publisher_user_id = ?",
            (skill_id, user_uuid),
        )
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return jsonify({"error": "This skill is already published", "marketplace_id": existing["id"]}), 409

        marketplace_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Store manifest JSON for preview (strip internal keys)
        clean_manifest = {k: v for k, v in manifest.items() if not k.startswith("_")}

        # Trust Membrane portability: carry the skill's attestation (if signed) so it
        # travels with the listing and can be reinstated cross-instance on install.
        try:
            from trusted_data_agent.core.trust import portability as _trust_portability
            _skill_claim = _trust_portability.capture("skill", skill_id)
            if _skill_claim:
                clean_manifest["trust"] = _skill_claim
        except Exception:
            pass

        # Extract skill-specific metadata
        uderia_section = manifest.get("uderia", {})
        injection_target = uderia_section.get("injection_target", "system_prompt")
        allowed_params = uderia_section.get("allowed_params", [])
        tags = manifest.get("tags", [])

        cursor.execute(
            """INSERT INTO marketplace_skills
               (id, skill_id, name, description, version, author,
                injection_target, has_params, tags_json,
                publisher_user_id, visibility, manifest_json, content_hash,
                download_count, install_count, published_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            (
                marketplace_id, skill_id,
                manifest.get("name", skill_id),
                manifest.get("description", ""),
                manifest.get("version", "1.0.0"),
                manifest.get("author", "Unknown"),
                injection_target,
                1 if allowed_params else 0,
                json.dumps(tags),
                user_uuid, visibility,
                json.dumps(clean_manifest), content_hash,
                now, now,
            ),
        )

        # Store files in marketplace_data directory (Claude Code compatible format)
        marketplace_data = Path(__file__).resolve().parents[3] / "marketplace_data" / "skills" / marketplace_id
        marketplace_data.mkdir(parents=True, exist_ok=True)
        main_file = manifest.get("main_file", f"{skill_id}.md")
        (marketplace_data / "skill.json").write_text(json.dumps(clean_manifest, indent=2), encoding="utf-8")
        (marketplace_data / main_file).write_text(content, encoding="utf-8")

        # Progressive skills (PROP-005): the library payload travels in the
        # sidecar, INDEX-driven (already sanitized relpaths; strays never ship).
        # content_hash keeps its existing md-only meaning — the per-file
        # integrity lives in the signed index (sha256 per entry).
        from trusted_data_agent.skills.manager import get_skill_manager as _gsm_pub
        _pub_library = _gsm_pub().get_skill_library_files(skill_id)
        for _rel, _data in sorted(_pub_library.items()):
            _dest = marketplace_data / "library" / _rel
            _dest.parent.mkdir(parents=True, exist_ok=True)
            _dest.write_bytes(_data)

        # Targeted sharing grants — only for valid, active users
        if visibility == "targeted" and user_ids:
            for uid in user_ids:
                cursor.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1", (uid,))
                if not cursor.fetchone():
                    continue
                cursor.execute(
                    "INSERT OR IGNORE INTO marketplace_sharing_grants "
                    "(id, resource_type, resource_id, grantor_user_id, grantee_user_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(_uuid.uuid4()), "skill", marketplace_id, user_uuid, uid, now),
                )

        conn.commit()
        conn.close()

        app_logger.info(f"Skill '{skill_id}' published to marketplace (id={marketplace_id}, visibility={visibility})")
        return jsonify({
            "status": "success",
            "marketplace_id": marketplace_id,
            "message": "Skill published to marketplace",
        }), 200

    except Exception as e:
        app_logger.error(f"Skill publish failed: {e}", exc_info=True)
        return jsonify({"error": f"Publish failed: {e}"}), 500


@skills_api_bp.route("/v1/marketplace/skills", methods=["GET"])
async def browse_marketplace_skills():
    """
    Browse published skills with pagination, search, and sorting.

    Query params: page, per_page, search, sort_by, injection_target
    """
    import sqlite3

    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        page = int(request.args.get("page", 1))
        per_page = min(int(request.args.get("per_page", 12)), 50)
        search = request.args.get("search", "").strip()
        sort_by = request.args.get("sort_by", "recent")
        injection_target = request.args.get("injection_target", "all")
        # 'public' (default) → only public listings; 'targeted' → only listings
        # the user has been granted access to; 'all' → both (legacy union).
        # Own listings are returned in all cases — frontend filters via is_publisher.
        visibility_filter = request.args.get("visibility", "public").strip().lower()
        if visibility_filter not in ("public", "targeted", "all"):
            visibility_filter = "public"

        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        if visibility_filter == "public":
            where_clauses = ["s.visibility = 'public'"]
            params = []
        elif visibility_filter == "targeted":
            # Show targeted listings the user has a grant for OR is the publisher of
            where_clauses = [
                "s.visibility = 'targeted' AND (s.id IN "
                "(SELECT resource_id FROM marketplace_sharing_grants WHERE resource_type = 'skill' AND grantee_user_id = ?) "
                "OR s.publisher_user_id = ?)"
            ]
            params = [user_uuid, user_uuid]
        else:  # 'all'
            # 'all' is the union of what 'public' and 'targeted' return — including
            # the caller's OWN targeted listings, or the name would not be true.
            where_clauses = [
                "(s.visibility = 'public' OR (s.visibility = 'targeted' AND (s.id IN "
                "(SELECT resource_id FROM marketplace_sharing_grants WHERE resource_type = 'skill' AND grantee_user_id = ?) "
                "OR s.publisher_user_id = ?)))"
            ]
            params = [user_uuid, user_uuid]

        if injection_target != "all":
            where_clauses.append("s.injection_target = ?")
            params.append(injection_target)

        if search:
            where_clauses.append("(s.name LIKE ? OR s.description LIKE ? OR s.skill_id LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])

        where_sql = " AND ".join(where_clauses)

        sort_map = {
            "downloads": "s.download_count DESC",
            "installs": "s.install_count DESC",
            "rating": "avg_rating DESC",
            "recent": "s.published_at DESC",
            "name": "s.name ASC",
        }
        order_sql = sort_map.get(sort_by, "s.published_at DESC")

        cursor = conn.cursor()

        # Total count
        cursor.execute(f"SELECT COUNT(*) FROM marketplace_skills s WHERE {where_sql}", params)
        total = cursor.fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)

        # Fetch page with ratings JOIN
        offset = (page - 1) * per_page
        query = f"""
            SELECT s.*,
                   u.username AS publisher_username,
                   u.display_name AS publisher_display_name,
                   COALESCE(r.avg_rating, 0) AS avg_rating,
                   COALESCE(r.rating_count, 0) AS rating_count
            FROM marketplace_skills s
            LEFT JOIN users u ON s.publisher_user_id = u.id
            LEFT JOIN (
                SELECT skill_marketplace_id, AVG(rating) AS avg_rating, COUNT(*) AS rating_count
                FROM skill_ratings
                GROUP BY skill_marketplace_id
            ) r ON r.skill_marketplace_id = s.id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
        """
        cursor.execute(query, params + [per_page, offset])
        rows = cursor.fetchall()

        # Batch-check which skills the current user has installed (for rating gate)
        if rows:
            skill_ids = [row["id"] for row in rows]
            placeholders = ",".join("?" * len(skill_ids))
            cursor.execute(
                f"SELECT marketplace_id FROM marketplace_installs "
                f"WHERE artifact_type = 'skill' AND user_id = ? AND marketplace_id IN ({placeholders})",
                [user_uuid] + skill_ids,
            )
            installed_ids = {r[0] for r in cursor.fetchall()}
        else:
            installed_ids = set()

        skills = []
        for row in rows:
            skill = {
                "id": row["id"],
                "skill_id": row["skill_id"],
                "name": row["name"],
                "description": row["description"],
                "version": row["version"],
                "author": row["author"],
                "injection_target": row["injection_target"],
                "has_params": bool(row["has_params"]),
                "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
                "publisher_username": row["publisher_display_name"] or row["publisher_username"],
                "download_count": row["download_count"],
                "install_count": row["install_count"],
                "average_rating": round(row["avg_rating"], 1),
                "rating_count": row["rating_count"],
                "visibility": row["visibility"],
                "published_at": row["published_at"],
                "is_publisher": row["publisher_user_id"] == user_uuid,
                "is_installed": row["id"] in installed_ids,
            }
            skills.append(skill)

        conn.close()

        return jsonify({
            "status": "success",
            "skills": skills,
            "total": total,
            "total_pages": total_pages,
            "page": page,
            "per_page": per_page,
        }), 200

    except Exception as e:
        app_logger.error(f"Browse marketplace skills failed: {e}", exc_info=True)
        return jsonify({"error": f"Browse failed: {e}"}), 500


@skills_api_bp.route("/v1/marketplace/skills/<marketplace_id>", methods=["GET"])
async def get_marketplace_skill_detail(marketplace_id: str):
    """Get detail for a single marketplace skill."""
    import sqlite3

    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """SELECT s.*,
                      u.username AS publisher_username,
                      u.display_name AS publisher_display_name,
                      COALESCE(r.avg_rating, 0) AS avg_rating,
                      COALESCE(r.rating_count, 0) AS rating_count
               FROM marketplace_skills s
               LEFT JOIN users u ON s.publisher_user_id = u.id
               LEFT JOIN (
                   SELECT skill_marketplace_id, AVG(rating) AS avg_rating, COUNT(*) AS rating_count
                   FROM skill_ratings GROUP BY skill_marketplace_id
               ) r ON r.skill_marketplace_id = s.id
               WHERE s.id = ?""",
            (marketplace_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Skill not found"}), 404

        if row["visibility"] == "targeted" and row["publisher_user_id"] != user_uuid:
            cursor.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'skill' AND resource_id = ? AND grantee_user_id = ?",
                (marketplace_id, user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"error": "Skill not found"}), 404

        # Get user's own rating
        cursor.execute(
            "SELECT rating, comment FROM skill_ratings WHERE skill_marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_uuid),
        )
        user_rating_row = cursor.fetchone()

        conn.close()

        skill = {
            "id": row["id"],
            "skill_id": row["skill_id"],
            "name": row["name"],
            "description": row["description"],
            "version": row["version"],
            "author": row["author"],
            "injection_target": row["injection_target"],
            "has_params": bool(row["has_params"]),
            "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
            "manifest_json": json.loads(row["manifest_json"]) if row["manifest_json"] else {},
            "publisher_username": row["publisher_display_name"] or row["publisher_username"],
            "download_count": row["download_count"],
            "install_count": row["install_count"],
            "average_rating": round(row["avg_rating"], 1),
            "rating_count": row["rating_count"],
            "visibility": row["visibility"],
            "published_at": row["published_at"],
            "is_publisher": row["publisher_user_id"] == user_uuid,
            "user_rating": dict(user_rating_row) if user_rating_row else None,
        }

        return jsonify({"status": "success", "skill": skill}), 200

    except Exception as e:
        app_logger.error(f"Get marketplace skill detail failed: {e}", exc_info=True)
        return jsonify({"error": f"Failed to get skill detail: {e}"}), 500


@skills_api_bp.route("/v1/marketplace/skills/<marketplace_id>/install", methods=["POST"])
async def install_marketplace_skill(marketplace_id: str):
    """
    Install a marketplace skill into the user's ~/.tda/skills/ directory.
    Copies skill.json + .md content, hot-reloads the skill manager, increments install_count.
    """
    import sqlite3

    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.skills.settings import is_skill_marketplace_enabled
        if not is_skill_marketplace_enabled():
            return jsonify({"error": "Skill marketplace has been disabled by the administrator."}), 403

        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM marketplace_skills WHERE id = ?", (marketplace_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Skill not found in marketplace"}), 404

        if row["visibility"] == "targeted" and row["publisher_user_id"] != user_uuid:
            cursor.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'skill' AND resource_id = ? AND grantee_user_id = ?",
                (marketplace_id, user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"error": "Skill not found in marketplace"}), 404

        skill_id = row["skill_id"]

        # Read files from marketplace_data (Claude Code compatible format)
        marketplace_data = Path(__file__).resolve().parents[3] / "marketplace_data" / "skills" / marketplace_id
        manifest_path = marketplace_data / "skill.json"

        if not manifest_path.exists():
            conn.close()
            return jsonify({"error": "Skill manifest not found"}), 404

        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)

        # Find the .md content file
        main_file = manifest.get("main_file", f"{skill_id}.md")
        content_path = marketplace_data / main_file
        if not content_path.exists():
            # Try any .md file in the directory
            md_files = list(marketplace_data.glob("*.md"))
            if md_files:
                content_path = md_files[0]
                main_file = content_path.name
            else:
                conn.close()
                return jsonify({"error": "Skill content not found"}), 404

        content = content_path.read_text(encoding="utf-8")

        # Verify content integrity against the hash stored at publish time
        import hashlib as _hl
        actual_hash = _hl.sha256(content.encode()).hexdigest()
        if row["content_hash"] and actual_hash != row["content_hash"]:
            conn.close()
            app_logger.error(
                f"Skill marketplace data integrity check failed for {marketplace_id}: "
                f"stored={row['content_hash'][:12]}… actual={actual_hash[:12]}…"
            )
            return jsonify({"error": "Skill integrity check failed — marketplace data may be corrupted."}), 500

        # Progressive skills (PROP-005): read the sidecar library per the
        # manifest's index and verify EVERY file against its signed sha256
        # BEFORE materialising — a tampered/incomplete payload aborts the
        # install loudly, never a silent partial skill.
        from trusted_data_agent.skills.package import (
            is_progressive as _is_prog,
            progressive_index as _prog_idx,
            safe_library_relpath as _safe_rel,
            validate_progressive_payload as _validate_payload,
        )
        install_library: dict | None = None
        if _is_prog(manifest):
            _lib_files: dict = {}
            for _e in _prog_idx(manifest):
                _rel = _safe_rel(_e.get("file", ""))
                if _rel is None:
                    continue
                _p = marketplace_data / "library" / _rel
                if _p.is_file():
                    _lib_files[_rel] = _p.read_bytes()
            _errors = _validate_payload(manifest, _lib_files)
            if _errors:
                conn.close()
                app_logger.error(
                    f"Marketplace skill {marketplace_id} progressive payload failed integrity: {_errors}")
                return jsonify({"error": "Progressive skill payload failed integrity checks — "
                                         "marketplace data may be corrupted.",
                                "details": _errors}), 500
            install_library = _lib_files

        # Fix 2D: Check for existing skill conflict and snapshot for rollback
        from trusted_data_agent.skills.manager import get_skill_manager
        manager = get_skill_manager()

        existing_manifest = manager.get_skill_manifest(skill_id)
        rollback_snapshot: dict | None = None
        if existing_manifest:
            # Snapshot existing state so we can restore on failure — the
            # library too: rolling back an update over a progressive skill
            # must restore ITS payload ({} for a legacy skill = clear).
            existing_content = manager.get_skill_full_content(skill_id) or ""
            rollback_snapshot = {
                "content": existing_content,
                "manifest": existing_manifest,
                "library": manager.get_skill_library_files(skill_id),
            }

        # Save skill files
        manager.save_skill(skill_id, content, manifest, library_files=install_library)

        # Hot-reload with rollback on failure
        try:
            manager.reload()
        except Exception as reload_err:
            app_logger.error(
                f"Skill reload failed after installing '{skill_id}', rolling back: {reload_err}",
                exc_info=True,
            )
            if rollback_snapshot is not None:
                # Restore the previous version
                try:
                    manager.save_skill(
                        skill_id,
                        rollback_snapshot["content"],
                        rollback_snapshot["manifest"],
                        library_files=rollback_snapshot.get("library", {}),
                    )
                    manager.reload()
                except Exception as rb_err:
                    app_logger.error(
                        f"Rollback of skill '{skill_id}' also failed: {rb_err}", exc_info=True
                    )
            else:
                # New install that broke reload — remove it
                try:
                    manager.delete_skill(skill_id)
                    manager.reload()
                except Exception as rb_err:
                    app_logger.error(
                        f"Cleanup of broken new skill '{skill_id}' failed: {rb_err}", exc_info=True
                    )
            conn.close()
            return jsonify({"error": f"Skill installed but failed to activate: {reload_err}"}), 500

        was_update = rollback_snapshot is not None

        # Trust Membrane portability: reinstate the publisher's attestation on the installed
        # skill iff the installed content matches what was signed (honesty gate). The skill_id
        # is preserved, so this is a no-op within-instance (already trusted) and restores
        # trust cross-instance.
        try:
            from trusted_data_agent.core.trust import portability as _trust_portability
            _trust_portability.reinstate_kind("skill", skill_id, (manifest or {}).get("trust"))
        except Exception:
            pass

        # Increment install_count + record install for rating gate (Fix 4A)
        import uuid as _inst_uuid
        now_ts = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "UPDATE marketplace_skills SET install_count = install_count + 1 WHERE id = ?",
            (marketplace_id,),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO marketplace_installs "
            "(id, artifact_type, marketplace_id, user_id, installed_at) "
            "VALUES (?, 'skill', ?, ?, ?)",
            (str(_inst_uuid.uuid4()), marketplace_id, user_uuid, now_ts),
        )
        conn.commit()
        conn.close()

        app_logger.info(
            f"User {user_uuid} {'updated' if was_update else 'installed'} "
            f"marketplace skill '{skill_id}' (marketplace_id={marketplace_id})"
        )
        return jsonify({
            "status": "success",
            "skill_id": skill_id,
            "was_update": was_update,
            "message": (
                f"Skill '{row['name']}' updated successfully"
                if was_update
                else f"Skill '{row['name']}' installed successfully"
            ),
        }), 200

    except Exception as e:
        app_logger.error(f"Install marketplace skill failed: {e}", exc_info=True)
        return jsonify({"error": f"Install failed: {e}"}), 500


@skills_api_bp.route("/v1/marketplace/skills/<marketplace_id>/rate", methods=["POST"])
async def rate_marketplace_skill(marketplace_id: str):
    """
    Rate a marketplace skill.

    Body (JSON): {"rating": 5, "comment": "Great skill!"}
    """
    import sqlite3
    import uuid as _uuid

    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        data = await request.get_json()
        if not data or "rating" not in data:
            return jsonify({"error": "rating is required"}), 400

        rating = int(data["rating"])
        if rating < 1 or rating > 5:
            return jsonify({"error": "rating must be between 1 and 5"}), 400

        comment = data.get("comment", "").strip() or None

        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Verify skill exists
        cursor.execute("SELECT id, publisher_user_id FROM marketplace_skills WHERE id = ?", (marketplace_id,))
        skill_row = cursor.fetchone()
        if not skill_row:
            conn.close()
            return jsonify({"error": "Skill not found"}), 404

        # Cannot rate own skill
        if skill_row["publisher_user_id"] == user_uuid:
            conn.close()
            return jsonify({"error": "Cannot rate your own skill"}), 400

        # Fix 4A: Must have installed before rating
        cursor.execute(
            "SELECT 1 FROM marketplace_installs "
            "WHERE artifact_type = 'skill' AND marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_uuid),
        )
        if not cursor.fetchone():
            # Legacy fallback: check if skill is currently installed (pre-Fix 4A installs)
            from trusted_data_agent.skills.manager import get_skill_manager as _gsm
            _sm = _gsm()
            skill_id_for_check = skill_row["skill_id"] if "skill_id" in skill_row.keys() else None
            if not skill_id_for_check or not _sm.get_skill_manifest(skill_id_for_check):
                conn.close()
                return jsonify({
                    "error": "You must install this skill before rating it.",
                }), 403

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "SELECT id FROM skill_ratings WHERE skill_marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_uuid),
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "UPDATE skill_ratings SET rating = ?, comment = ?, updated_at = ? WHERE id = ?",
                (rating, comment, now, existing["id"]),
            )
            rating_id = existing["id"]
            message = "Rating updated successfully"
            status_code = 200
        else:
            rating_id = str(_uuid.uuid4())
            cursor.execute(
                "INSERT INTO skill_ratings (id, skill_marketplace_id, user_id, rating, comment, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rating_id, marketplace_id, user_uuid, rating, comment, now, now),
            )
            message = "Rating submitted successfully"
            status_code = 201

        conn.commit()
        conn.close()

        return jsonify({"status": "success", "rating_id": rating_id, "message": message}), status_code

    except Exception as e:
        app_logger.error(f"Skill rating failed: {e}", exc_info=True)
        return jsonify({"error": f"Rating failed: {e}"}), 500


@skills_api_bp.route("/v1/marketplace/skills/<marketplace_id>", methods=["DELETE"])
async def unpublish_skill(marketplace_id: str):
    """Unpublish a skill from the marketplace (publisher only)."""
    import sqlite3

    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, skill_id FROM marketplace_skills WHERE id = ? AND publisher_user_id = ?",
            (marketplace_id, user_uuid),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Skill not found or you are not the publisher"}), 404

        # Delete sharing grants
        cursor.execute(
            "DELETE FROM marketplace_sharing_grants WHERE resource_type = 'skill' AND resource_id = ?",
            (marketplace_id,),
        )
        # Delete ratings
        cursor.execute(
            "DELETE FROM skill_ratings WHERE skill_marketplace_id = ?",
            (marketplace_id,),
        )
        # Delete install records so subscribers no longer see the skill in My Assets
        cursor.execute(
            "DELETE FROM marketplace_installs WHERE artifact_type = 'skill' AND marketplace_id = ?",
            (marketplace_id,),
        )
        # Delete marketplace record
        cursor.execute("DELETE FROM marketplace_skills WHERE id = ?", (marketplace_id,))

        conn.commit()
        conn.close()

        # Clean up marketplace_data files
        marketplace_data = Path(__file__).resolve().parents[3] / "marketplace_data" / "skills" / marketplace_id
        if marketplace_data.exists():
            import shutil
            shutil.rmtree(marketplace_data, ignore_errors=True)

        app_logger.info(f"Skill '{row['skill_id']}' unpublished from marketplace (id={marketplace_id})")
        return jsonify({"status": "success", "message": "Skill unpublished"}), 200

    except Exception as e:
        app_logger.error(f"Unpublish skill failed: {e}", exc_info=True)
        return jsonify({"error": f"Unpublish failed: {e}"}), 500


# ---------------------------------------------------------------------------
# Marketplace skill targeted-user management
# ---------------------------------------------------------------------------

@skills_api_bp.route("/v1/marketplace/skills/<marketplace_id>/targeted-users", methods=["GET"])
async def get_skill_targeted_users(marketplace_id: str):
    """Return the list of targeted users for a published skill (publisher only)."""
    import sqlite3
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required"}), 401
    try:
        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT publisher_user_id FROM marketplace_skills WHERE id = ?", (marketplace_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Skill not found"}), 404
        if row["publisher_user_id"] != user_uuid:
            conn.close()
            return jsonify({"error": "Only the publisher can view targeted users"}), 403
        cursor.execute(
            """SELECT g.grantee_user_id AS user_id, u.username, u.display_name
               FROM marketplace_sharing_grants g
               LEFT JOIN users u ON g.grantee_user_id = u.id
               WHERE g.resource_type = 'skill' AND g.resource_id = ?""",
            (marketplace_id,),
        )
        users = [
            {"user_id": r["user_id"], "username": r["username"], "display_name": r["display_name"] or r["username"]}
            for r in cursor.fetchall()
        ]
        conn.close()
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        app_logger.error(f"Failed to get skill targeted users: {e}", exc_info=True)
        return jsonify({"error": f"Failed: {e}"}), 500


@skills_api_bp.route("/v1/marketplace/skills/<marketplace_id>/targeted-users", methods=["PUT"])
async def update_skill_targeted_users(marketplace_id: str):
    """Update the targeted users list for a published skill (publisher only)."""
    import sqlite3, uuid as _uuid
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required"}), 401
    try:
        data = (await request.get_json()) or {}
        new_user_ids = set(data.get("user_ids", []))
        db_path = Path(__file__).resolve().parents[3] / "tda_auth.db"
        conn = sqlite_util.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT publisher_user_id FROM marketplace_skills WHERE id = ?", (marketplace_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Skill not found"}), 404
        if row["publisher_user_id"] != user_uuid:
            conn.close()
            return jsonify({"error": "Only the publisher can update targeted users"}), 403
        cursor.execute(
            "SELECT grantee_user_id FROM marketplace_sharing_grants WHERE resource_type = 'skill' AND resource_id = ?",
            (marketplace_id,),
        )
        existing_ids = {r["grantee_user_id"] for r in cursor.fetchall()}
        to_remove = existing_ids - new_user_ids
        if to_remove:
            placeholders = ",".join("?" * len(to_remove))
            cursor.execute(
                f"DELETE FROM marketplace_sharing_grants WHERE resource_type = 'skill' AND resource_id = ? AND grantee_user_id IN ({placeholders})",
                [marketplace_id] + list(to_remove),
            )
        now = datetime.now(timezone.utc).isoformat()
        for uid in new_user_ids - existing_ids:
            cursor.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1", (uid,))
            if not cursor.fetchone():
                continue
            cursor.execute(
                "INSERT OR IGNORE INTO marketplace_sharing_grants (id, resource_type, resource_id, grantor_user_id, grantee_user_id, created_at) VALUES (?, 'skill', ?, ?, ?, ?)",
                (str(_uuid.uuid4()), marketplace_id, user_uuid, uid, now),
            )
        conn.commit()
        cursor.execute(
            """SELECT g.grantee_user_id AS user_id, u.username, u.display_name
               FROM marketplace_sharing_grants g
               LEFT JOIN users u ON g.grantee_user_id = u.id
               WHERE g.resource_type = 'skill' AND g.resource_id = ?""",
            (marketplace_id,),
        )
        users = [
            {"user_id": r["user_id"], "username": r["username"], "display_name": r["display_name"] or r["username"]}
            for r in cursor.fetchall()
        ]
        conn.close()
        app_logger.info(f"Updated targeted users for skill {marketplace_id}: {len(users)} users")
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        app_logger.error(f"Failed to update skill targeted users: {e}", exc_info=True)
        return jsonify({"error": f"Failed: {e}"}), 500


# ---------------------------------------------------------------------------
# Profile skill assignments
# ---------------------------------------------------------------------------

@skills_api_bp.route("/v1/profiles/<profile_id>/skills", methods=["GET"])
@operation("profiles.skills", effect="read", scope="own",
           summary="The skills assigned to an agent, with their manifest details",
           path={"profile_id": "the agent's (profile's) id"})
async def get_profile_skills(profile_id):
    """
    Get skills assigned to a profile, enriched with manifest metadata.

    Returns:
        { "skills": [{ "id", "enabled", "active", "param", "name", "description",
                        "icon", "injection_target", "tags", "is_builtin" }] }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills.settings import is_skill_available

        cm = get_config_manager()
        profile = cm.get_profile(profile_id, user_uuid)
        if not profile:
            return jsonify({"error": "Profile not found"}), 404

        manager = get_skill_manager()
        all_skills = {s["skill_id"]: s for s in manager.list_skills()}

        # Build the profile's explicit skill entries. A missing skillsConfig means "no
        # explicit skills" — NOT "all skills enabled". (The old fallback auto-enabled every
        # installed skill, which diverged from what actually runs and misrepresented imported
        # profiles that carry no skillsConfig.)
        profile_type = profile.get("profile_type", "")
        skills_config = profile.get("skillsConfig")
        profile_skills = {}
        if isinstance(skills_config, dict):
            for e in skills_config.get("skills", []):
                sid = e.get("id", "")
                if sid:
                    profile_skills[sid] = e

        # Companion auto-assign — mirror the runtime resolver (execution_service) EXACTLY so
        # the panel shows what really applies: a builtin component's companion skill (e.g.
        # flow -> flow-designer) auto-applies when that component is enabled for the profile.
        from trusted_data_agent.skills.manager import resolve_auto_skill_specs
        companion_map = manager.companion_skill_map()
        enabled_component_ids = set()
        if companion_map:
            try:
                from trusted_data_agent.components.manager import get_component_manager
                from trusted_data_agent.agent.recommender import _component_enabled_for_profile
                _cm = get_component_manager()
                for _comp_id in companion_map:
                    if _component_enabled_for_profile(_cm, _comp_id, profile, profile_type, user_uuid):
                        enabled_component_ids.add(_comp_id)
            except Exception as e:
                app_logger.debug(f"[skills] companion auto-assign check failed in get_profile_skills: {e}")
        auto_ids = {
            spec.name for spec in resolve_auto_skill_specs(
                profile_skills, enabled_component_ids, companion_map, is_skill_available
            )
        }

        # Assigned set = explicit skillsConfig entries ∪ auto-companion skills (explicit first).
        shown_ids = list(profile_skills.keys()) + [sid for sid in auto_ids if sid not in profile_skills]

        result = []
        for skill_id in shown_ids:
            entry = profile_skills.get(skill_id, {})
            is_auto = skill_id in auto_ids
            manifest = all_skills.get(skill_id)
            available = is_skill_available(skill_id) if skill_id else False
            result.append({
                "id": skill_id,
                # Auto-companion skills actually auto-apply this turn -> enabled + active.
                "enabled": True if is_auto else bool(entry.get("enabled", True)),
                "active": True if is_auto else bool(entry.get("active")),
                "auto": is_auto,
                "param": entry.get("param"),
                "name": manifest.get("name", skill_id) if manifest else skill_id,
                "description": manifest.get("description", "") if manifest else "",
                "icon": manifest.get("icon", "") if manifest else "",
                "injection_target": manifest.get("injection_target", "system_prompt") if manifest else "system_prompt",
                "tags": manifest.get("tags", []) if manifest else [],
                "is_builtin": manifest.get("is_builtin", False) if manifest else False,
                # Progressive disclosure (PROP-005): what the skill actually carries
                "progressive": manifest.get("progressive", False) if manifest else False,
                "library_sections": manifest.get("library_sections", 0) if manifest else 0,
                "pack_name": manifest.get("pack_name") if manifest else None,
                "pack_orphaned": manifest.get("pack_orphaned", False) if manifest else False,
                "available": available,
            })

        return jsonify({"skills": result}), 200

    except Exception as e:
        app_logger.error(f"Failed to get profile skills: {e}", exc_info=True)
        return jsonify({"error": f"Failed to get profile skills: {e}"}), 500
