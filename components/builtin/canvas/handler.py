"""
Canvas Component Handler.

Processes TDA_Canvas tool calls into interactive canvas render specifications.
As of the persistence upgrade (Option A), canvases are first-class, addressable,
versioned entities (see canvas_db.py) rather than ephemeral message-embedded blobs.

Backward compatibility: a bare call with just {content, language, title} (no
`action`) is treated as a create/update — exactly the LLM's historical usage —
but it now persists the canvas and returns a stable canvas_id plus durable
version history. Explicit actions (open/list/update/delete/restore/get) allow a
canvas created in one session to be opened from any other session.
"""

import logging
from typing import Any, Dict, List, Tuple

from trusted_data_agent.components.base import (
    BaseComponentHandler,
    ComponentRenderPayload,
    RenderTarget,
)

logger = logging.getLogger("quart.app")


class CanvasComponentHandler(BaseComponentHandler):
    """Handler for the TDA_Canvas tool — interactive code/document workspace."""

    SUPPORTED_LANGUAGES = {
        "html", "css", "javascript", "python", "sql",
        "markdown", "json", "svg", "mermaid",
    }

    EXTENSION_MAP = {
        "html": ".html",
        "css": ".css",
        "javascript": ".js",
        "python": ".py",
        "sql": ".sql",
        "markdown": ".md",
        "json": ".json",
        "svg": ".svg",
        "mermaid": ".mmd",
    }

    _CONTENT_ACTIONS = {"", "create", "update"}

    # Canvas is a stateful, portable component instance (like flow / dashboard):
    # it can be exported to a file, imported, and published/forked/subscribed via
    # the marketplace. A canvas carries no remappable cross-references (no profile
    # or tool refs), so its instance-IO is the simplest case — see methods below.
    supports_instance_io = True

    @property
    def component_id(self) -> str:
        return "canvas"

    @property
    def tool_name(self) -> str:
        return "TDA_Canvas"

    @property
    def is_deterministic(self) -> bool:
        return True

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        action = (arguments.get("action") or "").strip().lower()
        if action in self._CONTENT_ACTIONS:
            content = arguments.get("content")
            if not content or not isinstance(content, str) or not content.strip():
                return False, "The 'content' argument is required and must be a non-empty string."
        return True, ""

    async def process(
        self,
        arguments: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> ComponentRenderPayload:
        context = context or {}
        user_uuid = context.get("user_uuid", "")
        session_id = context.get("session_id")
        action = (arguments.get("action") or "").strip().lower()
        if not action:
            action = "create"  # backward-compat: bare content call

        try:
            if action in ("create", "update"):
                return await self._handle_upsert(arguments, user_uuid, session_id)
            elif action == "open":
                return await self._handle_open(arguments, user_uuid)
            elif action == "list":
                return await self._handle_list(arguments, user_uuid)
            elif action == "delete":
                return await self._handle_delete(arguments, user_uuid)
            elif action == "restore":
                return await self._handle_restore(arguments, user_uuid)
            elif action == "get":
                return await self._handle_get(arguments, user_uuid)
            else:
                return self._error_payload(f"Unknown action: '{action}'")
        except Exception as exc:
            logger.error(f"CanvasComponentHandler error ({action}): {exc}", exc_info=True)
            return self._error_payload(str(exc))

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _handle_upsert(
        self, args: Dict[str, Any], user_uuid: str, session_id: str
    ) -> ComponentRenderPayload:
        """Create or update a canvas. The default (action-less) path."""
        from trusted_data_agent.core.canvas_db import (
            list_versions, update_canvas, upsert_canvas,
        )

        content = args.get("content", "")
        language = self._normalize_language(content, args.get("language"))
        title = args.get("title") or "Canvas"
        sources = args.get("sources") or None
        canvas_id = args.get("canvas_id")

        if canvas_id:
            canvas = update_canvas(
                canvas_id, user_uuid,
                content=content, title=args.get("title"),
                language=language, sources=sources,
            )
            if not canvas:
                return self._error_payload(f"Canvas '{canvas_id}' not found")
        else:
            result = upsert_canvas(user_uuid, title, content, language, sources, session_id)
            canvas = result["canvas"]

        versions = list_versions(canvas["canvas_id"], user_uuid)
        # action='open' so the renderer auto-opens the canvas — matches the
        # historical behavior where a freshly created canvas pops out immediately.
        return self._canvas_payload(canvas, action="open", versions=versions)

    async def _handle_open(self, args: Dict[str, Any], user_uuid: str) -> ComponentRenderPayload:
        """Open an existing canvas from any session, by canvas_id or title."""
        from trusted_data_agent.core.canvas_db import (
            get_canvas, get_canvas_by_title, list_versions,
        )

        canvas_id = args.get("canvas_id")
        if canvas_id:
            canvas = get_canvas(canvas_id, user_uuid)
        elif args.get("title"):
            canvas = get_canvas_by_title(user_uuid, args["title"])
        else:
            return self._error_payload("'open' requires 'canvas_id' or 'title'")

        if not canvas:
            return self._error_payload("Canvas not found")

        versions = list_versions(canvas["canvas_id"], user_uuid)
        return self._canvas_payload(canvas, action="open", versions=versions)

    async def _handle_list(self, args: Dict[str, Any], user_uuid: str) -> ComponentRenderPayload:
        """List the user's canvases — silent payload (the LLM reads ids/titles)."""
        from trusted_data_agent.core.canvas_db import list_canvases

        canvases = list_canvases(user_uuid)
        return ComponentRenderPayload(
            component_id="canvas",
            render_target=RenderTarget.INLINE,
            spec={"view": "silent_list", "canvases": canvases},
            title="Your Canvases",
            metadata={"tool_name": self.tool_name, "action": "list", "count": len(canvases)},
        )

    async def _handle_delete(self, args: Dict[str, Any], user_uuid: str) -> ComponentRenderPayload:
        """Delete a canvas by id."""
        from trusted_data_agent.core.canvas_db import delete_canvas

        canvas_id = args.get("canvas_id")
        if not canvas_id:
            return self._error_payload("'delete' requires 'canvas_id'")
        deleted = delete_canvas(canvas_id, user_uuid)
        return ComponentRenderPayload(
            component_id="canvas",
            render_target=RenderTarget.INLINE,
            spec={"view": "silent_delete", "deleted": deleted, "canvas_id": canvas_id},
            title="Canvas deleted",
            metadata={"tool_name": self.tool_name, "action": "delete", "deleted": deleted},
        )

    async def _handle_restore(self, args: Dict[str, Any], user_uuid: str) -> ComponentRenderPayload:
        """Restore a canvas to a prior version and re-open it."""
        from trusted_data_agent.core.canvas_db import list_versions, restore_version

        version_id = args.get("version_id")
        canvas_id = args.get("canvas_id")
        if not version_id or not canvas_id:
            return self._error_payload("'restore' requires 'canvas_id' and 'version_id'")
        canvas = restore_version(version_id, canvas_id, user_uuid)
        if not canvas:
            return self._error_payload("Canvas or version not found")
        versions = list_versions(canvas["canvas_id"], user_uuid)
        return self._canvas_payload(canvas, action="open", versions=versions)

    async def _handle_get(self, args: Dict[str, Any], user_uuid: str) -> ComponentRenderPayload:
        """Silent fetch — returns canvas_id/content to the LLM without a chat card."""
        from trusted_data_agent.core.canvas_db import (
            get_canvas, get_canvas_by_title,
        )

        canvas_id = args.get("canvas_id")
        if canvas_id:
            canvas = get_canvas(canvas_id, user_uuid)
        elif args.get("title"):
            canvas = get_canvas_by_title(user_uuid, args["title"])
        else:
            return self._error_payload("'get' requires 'canvas_id' or 'title'")
        if not canvas:
            return self._error_payload("Canvas not found")
        return ComponentRenderPayload(
            component_id="canvas",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "silent_get",
                "canvas_id": canvas["canvas_id"],
                "title": canvas.get("title"),
                "language": canvas.get("language"),
                "content": canvas.get("content"),
            },
            title="",
            metadata={"tool_name": self.tool_name, "action": "get", "canvas_id": canvas["canvas_id"]},
        )

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    def _canvas_payload(
        self,
        canvas: Dict[str, Any],
        action: str,
        versions: List[Dict[str, Any]] = None,
    ) -> ComponentRenderPayload:
        content = canvas.get("content", "")
        language = (canvas.get("language") or "").lower()
        previewable = language in {"html", "svg", "markdown"}
        line_count = content.count("\n") + 1
        return ComponentRenderPayload(
            component_id="canvas",
            render_target=RenderTarget.INLINE,
            spec={
                "content": content,
                "language": language,
                "title": canvas.get("title") or "Canvas",
                "previewable": previewable,
                "line_count": line_count,
                "file_extension": self.EXTENSION_MAP.get(language, ".txt"),
                "sources": canvas.get("sources") or None,
                "canvas_id": canvas["canvas_id"],
                "versions": versions or [],
                "action": action,
            },
            title=f"{canvas.get('title') or 'Canvas'} · {line_count} line{'' if line_count == 1 else 's'} · {language}",
            metadata={
                "tool_name": self.tool_name,
                "language": language,
                "content_length": len(content),
                "line_count": line_count,
                "canvas_id": canvas["canvas_id"],
                "action": action,
            },
        )

    def _error_payload(self, message: str) -> ComponentRenderPayload:
        """Silent payload — the error text goes to the LLM tool result, not the chat UI."""
        return ComponentRenderPayload(
            component_id="canvas",
            render_target=RenderTarget.INLINE,
            spec={"view": "silent_error", "error": message},
            title="",
            metadata={"tool_name": self.tool_name, "error": True, "message": message},
        )

    # ------------------------------------------------------------------
    # Instance portability (export / import) — see instance_portability.py
    # ------------------------------------------------------------------
    #
    # A canvas is pure content (code/document text + language + optional source
    # citations + version history). It has NO remappable cross-references — no
    # execution profile, no MCP tools — so `refs` is empty and the shared import
    # remap dialog degrades to a name-only confirm. Version history is per-user
    # and is reset on import (the latest content becomes v1 of the new canvas),
    # mirroring how the dashboard importer resets per-user provenance.

    async def list_instances(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        from trusted_data_agent.core.canvas_db import list_canvases

        user_uuid = context.get("user_uuid", "")
        return [
            {
                "id": c["canvas_id"],
                "name": c.get("title") or "Canvas",
                "updated_at": c.get("updated_at"),
            }
            for c in list_canvases(user_uuid)
        ]

    async def export_instance(self, instance_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        from datetime import datetime, timezone

        from trusted_data_agent.components import instance_portability as ip
        from trusted_data_agent.core.canvas_db import get_canvas, list_versions

        user_uuid = context.get("user_uuid", "")
        canvas = get_canvas(instance_id, user_uuid)
        if not canvas:
            raise ValueError(f"Canvas '{instance_id}' not found")

        out_versions = [
            {
                "content": v.get("content"),
                "language": v.get("language"),
                "version_num": v.get("version_num"),
                "label": v.get("label"),
            }
            for v in list_versions(instance_id, user_uuid)
        ]
        content = canvas.get("content") or ""
        language = canvas.get("language") or ""
        envelope = {
            "uderia_artifact": "component_instance",
            "component_id": "canvas",
            "schema_version": ip.PORTABILITY_SCHEMA_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "name": canvas.get("title") or "Canvas",
            "description": "",
            "spec": {
                "content": content,
                "language": language,
                "sources": canvas.get("sources") or None,
                "versions": out_versions,
            },
            # No remappable cross-references: a canvas is self-contained content.
            "refs": {},
            "summary": {
                "language": language or "text",
                "lines": content.count("\n") + 1,
                "versions": len(out_versions),
            },
        }
        return ip.stamp_content_hash(envelope)

    async def import_instance(
        self,
        payload: Dict[str, Any],
        context: Dict[str, Any],
        *,
        mode: str = "create",
        target_id: str = None,
        mapping: Dict[str, Any] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        import sqlite3
        import uuid

        from trusted_data_agent.components import instance_portability as ip
        from trusted_data_agent.core.canvas_db import (
            create_canvas, get_canvas, list_canvases, update_canvas,
        )

        user_uuid = context.get("user_uuid", "")
        if (payload or {}).get("component_id") != "canvas":
            raise ValueError("This file is not a canvas export")

        spec = payload.get("spec") or {}
        content = spec.get("content") or ""
        language = spec.get("language") or None
        sources = spec.get("sources") or None
        n_lines = content.count("\n") + 1

        # ---- Preflight ----
        if dry_run:
            lang = spec.get("language") or "text"
            return {
                "status": "preflight",
                "component_id": "canvas",
                "name": payload.get("name") or "Imported Canvas",
                "description": payload.get("description") or "",
                "summary": payload.get("summary") or {"lines": n_lines},
                "summary_text": f"{lang} · {n_lines} {'line' if n_lines == 1 else 'lines'}",
                "modes": ["create", "update"],
                "profiles": [],   # canvas carries no profile references
                "candidates": [],
                "tools": [],
            }

        # ---- Commit: in-place re-materialization (marketplace subscribe-sync) ----
        if mode == "update" and target_id:
            existing = get_canvas(target_id, user_uuid)
            if not existing:
                raise ValueError(f"Canvas '{target_id}' not found for update")
            # Sync content/language/sources but PRESERVE the local title: the canvas
            # (user_id, title_key) unique index means forcing the publisher's title
            # could collide with a different canvas the subscriber already owns, and
            # a sync should refresh content, not re-identify the instance.
            updated = update_canvas(
                target_id, user_uuid,
                content=content, language=language, sources=sources,
            )
            return {
                "status": "success", "mode": "update",
                "canvas_id": target_id, "instance_id": target_id,
                "name": (updated or existing).get("title"), "warnings": [],
            }

        # ---- Commit: create a fresh canvas (file import / install / fork) ----
        # The (user_id, title_key) unique index requires a distinct title; dedupe
        # against existing names, with an IntegrityError fallback to cover a
        # case-insensitive collision the case-sensitive dedupe could miss.
        base = payload.get("name") or "Imported Canvas"
        existing_titles = {c.get("title") for c in list_canvases(user_uuid)}
        name = ip.dedupe_name(base, existing_titles)
        try:
            created = create_canvas(user_uuid, name, content, language, sources)
        except sqlite3.IntegrityError:
            name = f"{base} ({uuid.uuid4().hex[:6]})"
            created = create_canvas(user_uuid, name, content, language, sources)
        return {
            "status": "success", "mode": "create",
            "canvas_id": created["canvas_id"], "instance_id": created["canvas_id"],
            "name": created.get("title"), "warnings": [],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_language(self, content: str, language: Any) -> str:
        language = (language or "").lower().strip()
        if language not in self.SUPPORTED_LANGUAGES:
            language = self._detect_language(content) or "html"
        return language

    @staticmethod
    def _detect_language(content: str) -> str | None:
        """Heuristic language detection fallback."""
        c = content.strip()
        if not c:
            return None
        if c.startswith("<!DOCTYPE") or c.startswith("<html") or "<head" in c[:200]:
            return "html"
        if c.startswith("<svg"):
            return "svg"
        if c.startswith("graph ") or c.startswith("sequenceDiagram"):
            return "mermaid"
        if "def " in c[:500] or "import " in c[:200]:
            return "python"
        if any(kw in c.upper()[:300] for kw in ["SELECT ", "CREATE TABLE", "INSERT INTO"]):
            return "sql"
        if c.startswith("{") and c.endswith("}"):
            return "json"
        if c.startswith("# ") or "\n## " in c:
            return "markdown"
        return None
