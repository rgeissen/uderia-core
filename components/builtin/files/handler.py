"""Files component handler (TDA_Files, PROP-010 phase 4).

The agent's own file acts — list, read, save — over the SAME file service the Files panel
uses (one write path, one read path; nothing here opens a store). Deterministic, 0 tokens.

Two gates beside the component's enablement:

  * the TOOL is offered only to an agent whose owner opted in
    (``componentConfig.files.agentTool``) — ``offers_tool``. Files stays enabled for every
    agent (its panel, Keep, attachments); only the model's access is opt-in, and the
    companion skill ``files-expert`` follows the tool, not the component. A call that
    arrives without the opt-in (a replayed plan, a leaked call) is refused here too.
  * Agent Authority: an agent whose data authority is its signed data products
    (a surface-only scope) may not read a file that came from a data source — that would
    be data outside its surface by another door.

Saving needs no write grant: a file in the user's own Files changes no data source. Its
provenance is the turn (conversation, turn, agent); the turn's bill of materials is copied
onto the file at its first read after the turn ends (``service._backfill_bom``).
"""
import logging
from typing import Any, Dict, Tuple

from trusted_data_agent.components.base import (
    BaseComponentHandler, ComponentRenderPayload, RenderTarget,
)

logger = logging.getLogger("quart.app")

ACTIONS = ("list", "read", "save")
LIST_LIMIT = 25


def agent_tool_on(profile_config: Dict[str, Any]) -> bool:
    """The owner's opt-in: the agent may use Files itself."""
    cc = ((profile_config or {}).get("componentConfig") or {}).get("files") or {}
    return isinstance(cc, dict) and cc.get("agentTool") is True


class FilesComponentHandler(BaseComponentHandler):
    @property
    def component_id(self) -> str:
        return "files"

    @property
    def tool_name(self) -> str:
        return "TDA_Files"

    def offers_tool(self, profile_config: Dict[str, Any]) -> bool:
        try:
            return agent_tool_on(profile_config)
        except Exception:  # noqa: BLE001 — inert on error: the tool is opt-in
            return False

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        action = (arguments.get("action") or "").strip().lower()
        if action not in ACTIONS:
            return False, "action must be 'list', 'read' or 'save'"
        if action == "read" and not (arguments.get("file_id") or arguments.get("name")):
            return False, "read requires 'file_id' (from 'list') or 'name'"
        if action == "save" and not (arguments.get("content") or arguments.get("statement")):
            return False, "save requires 'content' or 'statement'"
        return True, ""

    # ── payload shapes ─────────────────────────────────────────────────────────
    def _headless(self, action: str, spec: Dict[str, Any]) -> ComponentRenderPayload:
        """list / read: the result is for the MODEL (spec); the answer carries no card."""
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE, spec=spec, html="",
            metadata={"tool_name": self.tool_name, "action": action, "suppress_inline": True})

    def _err(self, action: str, message: str, code: str = "files_error") -> ComponentRenderPayload:
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE,
            spec={"error": message}, html="",
            metadata={"tool_name": self.tool_name, "action": action, "error_code": code,
                      "error": message, "suppress_inline": True})

    @staticmethod
    def _profile(context: Dict[str, Any]) -> Dict[str, Any]:
        pid, uid = context.get("profile_id"), context.get("user_uuid")
        if not (pid and uid):
            return {}
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            return get_config_manager().get_profile(pid, uid) or {}
        except Exception:  # noqa: BLE001
            return {}

    async def process(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ComponentRenderPayload:
        import asyncio
        from trusted_data_agent.filestore import service as fsvc

        action = (arguments.get("action") or "").strip().lower()
        user_uuid = context.get("user_uuid") or ""
        session_id = context.get("session_id")
        turn_id = context.get("current_turn_number")
        profile = self._profile(context)
        if not agent_tool_on(profile):
            return self._err(action, "This agent may not use Files itself — its owner has not turned that on "
                                     "(Agent profile → Capabilities → Files → the agent can list, read and save files). "
                                     "Do not retry; tell the user.", "agent_tool_off")
        try:
            if action == "list":
                out = await asyncio.to_thread(
                    fsvc.list_files, user_uuid, q=(arguments.get("query") or "").strip(),
                    kind=(arguments.get("kind") or "all").strip().lower(), limit=LIST_LIMIT)
                files = [{"file_id": f["id"], "name": f["filename"], "type": f["type_label"], "size": f["size_label"],
                          "kept": f.get("created_at"), "conversation": f.get("session_title"),
                          "question": f.get("question"), "agent": f.get("profile_tag"),
                          "readable": f["ext"] in ("csv", "tsv", "xlsx", "pdf", "docx") or f["ext"] in fsvc.TEXT_EXT,
                          "from_a_data_source": f.get("data_derived")} for f in out["files"]]
                return self._headless("list", {"action": "list", "total": out["total"], "shown": len(files),
                                               "files": files})

            if action == "read":
                row = await asyncio.to_thread(fsvc.find_for_agent, user_uuid, arguments.get("file_id"), arguments.get("name"))
                refusal = self._authority_refusal(row)
                if refusal:
                    return self._err("read", refusal, "surface_only")
                got = await asyncio.to_thread(fsvc.agent_read, row)
                if session_id:
                    await asyncio.to_thread(fsvc.record_use, row["id"], user_uuid, session_id, turn_id)
                spec: Dict[str, Any] = {"action": "read", "file_id": row["id"], "name": row["filename"],
                                        "from_a_data_source": bool(row.get("data_derived"))}
                if got["kind"] == "table":
                    n = self._register_table(session_id, row, got)
                    spec.update({"columns": got["columns"], "row_count": got["row_count"], "rows": got["rows"],
                                 "truncated": got["truncated"]})
                    if n:
                        spec["statement"] = n   # the whole table is this turn's statement n (TDA_Compute reads it)
                    if got["truncated"]:
                        spec["note"] = (f"The first {len(got['rows'])} of {got['row_count']} rows are shown"
                                        + (f"; all of them are statement {n} of this turn." if n else "."))
                else:
                    spec.update({"text": got["text"], "chars": got["chars"], "truncated": got["truncated"]})
                return self._headless("read", spec)

            # save
            file, already = await fsvc.agent_save(
                user_uuid, filename=(arguments.get("name") or "").strip(), session_id=session_id, turn_id=turn_id,
                profile_id=context.get("profile_id"), question=context.get("user_query") or context.get("user_input"),
                content=arguments.get("content"), statement=arguments.get("statement"))
            message = (f"{file['filename']} is already in Files." if already
                       else f"Saved {file['filename']} to Files ({file['size_label']}).")
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                spec={"action": "save", "already": already, "message": message,
                      "file": {k: file.get(k) for k in ("id", "filename", "type_label", "size_label", "store",
                                                        "encrypted", "data_derived", "formats", "category")}},
                title=message, tts_text=message,
                metadata={"tool_name": self.tool_name, "action": "save"})
        except fsvc.FilesError as e:
            return self._err(action, e.message, e.code)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[files] TDA_Files {action} failed: {e}")
            return self._err(action, f"Files could not {action}: {e}")

    @staticmethod
    def _authority_refusal(row: Dict[str, Any]):
        """A surface-only agent reads data only through its signed products — a file that came
        from a data source is data by another door."""
        if not row.get("data_derived"):
            return None
        try:
            from trusted_data_agent.core.agent_authority import SURFACE_DENIAL_MARKER, get_current_principal
            p = get_current_principal()
            scope = getattr(p, "scope", None)
            if scope is not None and scope.data_surface_exclusive and not scope.data_surface_advisory:
                return (f"this agent is {SURFACE_DENIAL_MARKER} — {row['filename']} came from a data source, and its "
                        "data authority is its signed data products. Answer from those, or tell the user.")
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _register_table(session_id, row: Dict[str, Any], got: Dict[str, Any]):
        """A kept table read by the agent is a table of THIS turn (PROP-006), so the combine step
        can join it with warehouse rows and a figure quoted from it traces to the file."""
        if not session_id:
            return None
        try:
            from trusted_data_agent.agent import result_sets
            return result_sets.register(session_id, "file", f"file {row['filename']}", got["columns"], got["all_rows"],
                                        source={"file_id": row["id"], "filename": row["filename"], "kind": "file"})
        except Exception:  # noqa: BLE001
            return None
