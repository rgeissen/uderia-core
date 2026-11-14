"""Shared utilities for component HTML generation and payload extraction.

These functions consolidate logic that was previously duplicated across
executor.py, execution_service.py, conversation_agent.py, and
genie_coordinator.py — ensuring a single source of truth for the
component rendering contract.
"""

import json
import uuid
from typing import Any, Dict, List, Optional


def generate_component_html(component_payloads: List[Dict[str, Any]]) -> str:
    """Generate generic component HTML from extracted payloads.

    Produces ``<div data-component-id="..." data-spec='...'>`` containers
    that the frontend's ComponentRendererRegistry dispatches to the
    correct renderer.

    Used by all profile types (llm_only, rag_focused, genie) after
    component payloads have been extracted from tool output.
    """
    html = ""
    for _cp in component_payloads:
        # Skip sub_window payloads — they are delivered via SSE component_render events
        if _cp.get("render_target") == "sub_window":
            continue
        # Headless components (PROP-005): suppress_inline is honored HERE — the
        # one seam all four engines share — so a data-only payload never renders
        # an empty .response-card box (previously only the conversation-agent
        # path checked it; Genie and leaked-call recovery did not).
        if (_cp.get("metadata") or {}).get("suppress_inline"):
            continue
        _comp_id = _cp.get("component_id", "unknown")
        _spec = _cp.get("spec", {})
        # Pre-rendered HTML wins whenever the handler supplied it — that is the
        # ComponentRenderPayload contract ("when non-empty, the frontend inserts this
        # directly instead of using spec"). It used to win only when the spec was EMPTY,
        # so a handler that ships BOTH (compute: the receipt card as html, the receipt as
        # spec for the lineage/persistence readers) fell to the data-spec route — and a
        # component with no frontend renderer (compute has none by design) then rendered
        # as an empty .response-card: the blank box under every TDA_Compute answer.
        _html = _cp.get("html", "")
        if _html:
            # THE cohesion seam (docs/roadmap/ANSWER_SURFACE_GRAMMAR.md): a component's
            # pre-rendered BODY becomes one evidence row of the Answer Surface, its identity
            # (kind word, accent) read from the component's manifest and its disclosure
            # default from `answer_surface.open`. A component never draws its own frame —
            # so a new component integrates by declaring two manifest fields, and cannot
            # render outside the grammar. A body that already IS a row passes through.
            html += f"\n{_component_row_html(_comp_id, _cp, _html)}"
            continue
        _cid = f"component-{uuid.uuid4().hex[:12]}"
        try:
            _spec_json = json.dumps(_spec).replace("'", "&apos;")
        except (TypeError, ValueError):
            continue
        # The spec route — the component's renderer.js draws the body in the browser.
        # It enters the grammar through the SAME seam as the html route: the container is
        # the row's body, the row is the frame (never a `.response-card`). A silent
        # payload — the renderers' own convention `view: "silent_*"` (an act with nothing to
        # show), or a producer declaring `answer_surface.row: false` — keeps its container
        # (the renderer must still run: a panel to open, a patch to deliver) but gets no
        # row, so an act never reads as an empty evidence row.
        _meta_as = (_cp.get("metadata") or {}).get("answer_surface")
        _silent = (str((_spec or {}).get("view") or "").startswith("silent")
                   or (isinstance(_meta_as, dict) and _meta_as.get("row") is False)
                   or not _spec)   # an empty spec and no html: nothing to show, never an empty row
        try:
            from trusted_data_agent.agent import answer_surface as _ans
            _container = _ans.component_container(_comp_id, _cid, _spec_json, silent=_silent)
        except Exception:  # noqa: BLE001
            _container = f'<div id="{_cid}" data-component-id="{_comp_id}" data-spec=\'{_spec_json}\'></div>'
        html += f"\n{_container if _silent else _component_row_html(_comp_id, _cp, _container)}"
    return html


def _component_row_html(component_id: str, payload: Dict[str, Any], body_html: str) -> str:
    stripped = body_html.lstrip()
    if stripped.startswith('<details class="ans-ev"'):
        return body_html          # the producer already speaks the grammar (never double-framed)
    try:
        from trusted_data_agent.agent import answer_surface as _ans
    except Exception:  # noqa: BLE001
        return body_html
    manifest: Dict[str, Any] = {}
    try:
        from trusted_data_agent.components.manager import get_component_manager
        comp = get_component_manager().get_component(component_id)
        manifest = dict(getattr(comp, "manifest", None) or {}) if comp else {}
    except Exception:  # noqa: BLE001
        manifest = {}
    meta = payload.get("metadata") or {}
    summary = str(payload.get("title") or meta.get("summary") or manifest.get("display_name") or component_id)
    tool = str(meta.get("tool_name") or (manifest.get("tool_definition") or {}).get("name") or "")
    return _ans.component_row(component_id, summary, body_html, manifest=manifest, tool_name=tool,
                              override=meta.get("answer_surface"))


def extract_component_payload(tool_output: Any) -> Optional[Dict[str, Any]]:
    """Parse tool output and extract a component render payload if present.

    Returns the parsed payload dict if it matches the component contract
    ``{status: "success", component_id: str, spec: dict}``, else ``None``.

    Handles both LangChain ToolMessage objects (with ``.content``) and
    plain strings.
    """
    try:
        artifact = None
        if isinstance(tool_output, tuple) and len(tool_output) == 2:
            # a content_and_artifact tool invoked outside a ToolNode (no tool_call_id)
            tool_output, artifact = tool_output
        elif hasattr(tool_output, "artifact"):
            artifact = getattr(tool_output, "artifact", None)
        _raw = tool_output.content if hasattr(tool_output, 'content') else str(tool_output)
        if isinstance(_raw, list):
            # LangChain content blocks: take the first text block
            _raw = next((b.get("text") if isinstance(b, dict) else getattr(b, "text", str(b))
                         for b in _raw), "")
        _parsed = json.loads(_raw)
        if (isinstance(_parsed, dict)
                and _parsed.get("status") == "success"
                and _parsed.get("component_id")
                and "spec" in _parsed):
            # The reader's HTML rides the artifact (components.manager keeps it out of
            # the model-facing content); re-attach it so every renderer sees the
            # payload it always saw. An older tool that still inlines html is unchanged.
            if "html" not in _parsed and isinstance(artifact, dict) and artifact.get("html") is not None:
                _parsed["html"] = artifact["html"]
            return _parsed
    except (json.JSONDecodeError, TypeError, AttributeError, StopIteration):
        pass
    return None


def canvas_contents(component_payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The code a turn put into canvases, as data: [{title, language, content}].

    A canvas payload (a real TDA_Canvas call, or a fenced block the conversation agent
    auto-converted) removes the code from the turn's clean text — right for the chat,
    where the canvas card shows it, but a programmatic caller reading the text (a flow
    node, a REST client) would never see the code. This hands it back as data."""
    out: List[Dict[str, Any]] = []
    for p in component_payloads or []:
        if not isinstance(p, dict) or p.get("component_id") != "canvas":
            continue
        spec = p.get("spec") or {}
        content = spec.get("content")
        if isinstance(content, str) and content.strip():
            out.append({"title": spec.get("title") or p.get("title") or "",
                        "language": spec.get("language") or "", "content": content})
    return out

