"""Tier 1.1 — grounding gate adapter.

The only grounding module that knows the executor/turn shape. It (1) extracts
``GroundingInputs`` from a turn, (2) resolves the gate config from the profile + the
admin global flag, (3) runs the pure ``GroundingGate``, and (4) applies the verdict to
the answer text. The gate stays pure; this layer owns the I/O-adjacent concerns.

Extraction reuses ``eval.types.RunRecord`` — the SAME normalized view the eval scorer
will read from the persisted turn — so the live gate and the post-hoc
``ScopeAdherenceScorer`` see byte-identical scope/calls/grounding (no divergence).
``RunRecord`` is a dependency-light dict wrapper (``eval/types.py`` imports only
dataclasses/enum/typing), so this is cheap on the request path; we do NOT import the
scorer registry. ``RunRecord.tool_calls()`` already normalizes BOTH engine sources
(ReAct ``conversation_agent_events`` and Optimize ``execution_trace``), so one adapter
serves every engine.

See docs/roadmap/TIER_1.1_GROUNDING_GATE.md (§4 adapter, §14 decision 3).
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from trusted_data_agent.agent.grounding.gate import GroundingGate
from trusted_data_agent.agent.grounding.primitives import is_catalog_call
from trusted_data_agent.agent.grounding.types import (
    Decision, GroundingConfig, GroundingInputs, GroundingVerdict,
)
from trusted_data_agent.eval.types import RunRecord

logger = logging.getLogger("quart.app")


async def ensure_trusted_scope(kg_enrichment_event, profile, user_uuid,
                               session_id: str = None):
    """Union the profile's bound-KG ALWAYS-ON deployed trusted scope (databases + concepts)
    onto the enrichment event, independent of per-query relevance. This is the SINGLE source
    of the gate's query-invariant scope — it is what lets the gate catch *out-of-domain* drift
    (a query with no per-query KG match still carries the profile's declared scope, closing the
    230-day blind spot). Returns the (possibly newly-created) event dict; the original on no
    trusted scope / error. Idempotent — safe to call even if the engine already stamped it, so
    every engine (incl. Coordinate/genie, which never pre-stamps) gets it via one code path
    (charter #14). Never raises."""
    try:
        profile_id = (profile or {}).get("id") if isinstance(profile, dict) else None
        if not profile_id or not user_uuid:
            return kg_enrichment_event
        from trusted_data_agent.components.manager import get_component_trusted_scope
        t_dbs, t_ents = await get_component_trusted_scope(profile_id, user_uuid)
        if not t_dbs:
            return kg_enrichment_event
        ev = kg_enrichment_event
        if not ev:
            ev = {"enrichments": [], "total_entities": 0, "total_relationships": 0,
                  "session_id": session_id}
        enr = ev.setdefault("enrichments", [])
        if not enr:
            enr.append({"trusted_scope_only": True})
        for e in enr:
            if isinstance(e, dict):
                e["scope_databases"] = sorted(set(e.get("scope_databases") or []) | set(t_dbs))
                # Cap generously (was 60, which silently dropped in-scope entities on a real KG →
                # deterministic over-triggering of the scope-faithfulness judge for any answer
                # naming an entity sorted past the cut). 500 covers realistic ontologies while
                # still bounding memory on a pathological graph.
                e["scope_entities"] = sorted(set(e.get("scope_entities") or []) | set(t_ents))[:500]
        return ev
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GroundingGate] trusted-scope apply failed: {e}")
        return kg_enrichment_event


def _as_int(v, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _as_float(v, default: float) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _chunk_texts(rr: RunRecord) -> tuple:
    """The retrieved chunks as support (the corpus-support family's whitelist) — content plus
    the citation line the model was shown, via the ONE definition both lanes share
    (`RunRecord.retrieved_chunk_texts`)."""
    # retrieved chunks + the turn's attachments (S9 readings, else the attachment text) — the
    # ONE definition the post-hoc readers share (`RunRecord.support_chunk_texts`)
    return rr.support_chunk_texts()


def build_inputs(turn: dict, answer_text: str) -> GroundingInputs:
    """Normalize a turn-shaped dict into ``GroundingInputs`` via ``RunRecord`` so the
    live gate's extraction matches the eval scorer exactly.

    ``turn`` carries whatever the engine has at the seam: ``conversation_agent_events``
    (ReAct) or ``execution_trace`` (Optimize), plus ``kg_enrichment_event`` and (Wave 1)
    ``knowledge_retrieval_event`` with the retrieved chunks. ``answer_text`` is the live
    answer (overrides any stored answer in the turn)."""
    rr = RunRecord(turn or {})
    return GroundingInputs(
        answer_text=answer_text or rr.final_answer_text or "",
        scope_databases=frozenset(rr.kg_scope_databases()),
        tool_calls=tuple(rr.tool_calls()),
        grounding_text=rr.grounding_text(),
        scope_entities=frozenset(rr.kg_scope_entities()),
        retrieved_chunks=_chunk_texts(rr),
        question=str((turn or {}).get("user_query") or ""),
        vouched_numbers=frozenset(rr.vouched_numbers()),
        value_text=rr.grounding_text(exclude_call=is_catalog_call),
    )


def has_groundable_content(inputs: GroundingInputs) -> bool:
    """Whether this turn has anything to ground: a declared KG scope AND some data to
    check the answer against (tool calls or retrieved text) — OR (Wave 1) retrieved
    knowledge chunks for the corpus-support family. A pure Ideate turn with neither
    abstains cleanly (charter #14). Returns False ⇒ engines skip the gate entirely
    (no event, answer untouched)."""
    scope_groundable = bool(inputs.scope_databases) and (
        bool(inputs.tool_calls) or bool((inputs.grounding_text or "").strip()))
    return scope_groundable or bool(inputs.retrieved_chunks)


def resolve_config(profile: Optional[dict], global_enabled: bool,
                   default_thresholds: Optional[dict] = None) -> GroundingConfig:
    """Resolve the gate config: admin global flag → profile ``groundingConfig`` →
    admin default thresholds. When the global flag is off the gate is ``off`` (skipped).

    ``default_thresholds`` is the admin-resolved scalar map (the danger/block cut for
    ``max_out_of_scope_dbs`` and the floor for ``min_numeric_fidelity``); a profile-level
    value in ``groundingConfig`` overrides it."""
    if not global_enabled:
        return GroundingConfig(mode="off")
    # Shape-tolerant at every level — see guardrails/adapter for why a malformed block
    # must degrade rather than raise (it took all four gates down on one turn).
    from trusted_data_agent.core.profile_config_contract import config_block
    gc = config_block(profile, "groundingConfig")
    th = default_thresholds if isinstance(default_thresholds, dict) else {}
    corpus_cfg = config_block(gc, "corpus")
    # Agent-level inheritance: no own mode ⇒ postureConfig.mode (ONE resolver —
    # core.posture_modes.gate_raw_or_default). The corpus family keeps its own chain
    # (corpus mode → the resolved gate mode) via GroundingConfig.normalized_corpus_mode.
    from trusted_data_agent.core import posture_modes as _pm
    return GroundingConfig(
        mode=_pm.gate_raw_or_default(gc.get("mode"), profile) or "observe",
        allow_out_of_scope=bool(gc.get("allowOutOfScope",
                                       gc.get("allow_out_of_scope", False))),
        max_out_of_scope_dbs=_as_int(
            gc.get("maxOutOfScopeDbs", th.get("max_out_of_scope_dbs")), 0),
        min_numeric_fidelity=_as_float(
            gc.get("minNumericFidelity", th.get("min_numeric_fidelity")), 1.0),
        # Wave 1 (§1.5): optional independent mode for the corpus-support family;
        # absent ⇒ inherit `mode` (one ladder governs both families).
        corpus_mode=(str(corpus_cfg.get("mode")).lower()
                     if corpus_cfg.get("mode") else None),
    )


def apply_to_answer(answer_text: str, verdict: GroundingVerdict) -> str:
    """Apply the verdict to the answer text.

    PASS / no annotation → unchanged (``observe`` is always PASS, so non-regressive).
    ANNOTATE → append the note as a blockquote. BLOCK → the out-of-scope answer is
    withheld; the note (which explains why + names the unexpected source) becomes the
    answer. Claim-level surgery (keep the groundable part, withhold only the bad claim)
    is a later refinement; v1 withholds the whole out-of-scope answer in enforce."""
    if verdict.decision is Decision.PASS or not verdict.annotation_md:
        return answer_text
    if verdict.blocked:
        return verdict.annotation_md
    return f"{answer_text}\n\n> {verdict.annotation_md}"


def annotation_html(event: Optional[dict]) -> str:
    """The grounding annotation as an Answer-Surface NOTE (kind `scope`, warn tone) — the
    platform's statement that the answer reached beyond its declared scope, one line under
    the answer, drawn by the ONE note renderer so it reads like every other note."""
    md = (event or {}).get("annotation_md") or ""
    if not md:
        return ""
    from trusted_data_agent.agent import answer_surface as _ans
    # the claims are code-ticked in the text form ("`tools?`."); without the ticks a claim
    # that ends in its own punctuation would read "tools?." — keep one terminal mark
    import re as _re
    text = _re.sub(r'([.?!])\.(\s|$)', r'\1\2', md.replace("`", "").lstrip("> ").strip())
    return _ans.note("scope", text)


def _sentinel_indexes(segment: str, sentinel: str) -> list:
    """The positions (in the masked-tag list) of the tags a masked segment contains."""
    import re as _re
    out = []
    for k in _re.findall(_re.escape(sentinel) + r"([a-z]+)" + _re.escape(sentinel), segment):
        n = 0
        for ch in k:
            n = n * 26 + (ord(ch) - 96)
        out.append(n - 1)
    return out


def _balanced(tags: list) -> bool:
    """True when the tags a mark would enclose open and close within it (or are void), so
    wrapping them never produces mis-nested HTML."""
    import re as _re
    stack = []
    for t in tags:
        m = _re.match(r"<\s*(/?)\s*([a-zA-Z0-9]+)", t)
        if not m:
            return False
        closing, name = m.group(1), m.group(2).lower()
        if name in ("br", "hr", "img", "input", "wbr") or t.rstrip().endswith("/>"):
            continue
        if closing:
            if not stack or stack[-1] != name:
                return False
            stack.pop()
        else:
            stack.append(name)
    return not stack


def mark_unsupported_claims_html(final_html: str, event: Optional[dict]) -> str:
    """U2 — inline claim transparency (RAG_TRUST_HARDENING §6.1). In ``annotate`` mode the
    corpus family's unsupported spans are marked IN the rendered answer: a dotted-underline
    ``<mark class="claim-unsupported">`` with a themed ``u-tip`` — the reader sees exactly
    which statement the banner refers to, in place. Conservative by design: only exact
    (HTML-escaped) text occurrences are wrapped, first occurrence per span, never inside a
    tag; a span that doesn't cleanly match is simply not marked (the annotation blockquote
    still names it). Best-effort — returns the original HTML on any error."""
    try:
        if not final_html or not event:
            return final_html
        if event.get("decision") != "annotate":
            return final_html
        spans = [s for s in (event.get("unsupported_claims") or []) if s and len(str(s)) >= 2]
        if not spans:
            return final_html
        import re as _re
        from trusted_data_agent.agent.formatter import md_inline
        from trusted_data_agent.agent.html_marks import mask_tags, unmask_tags, _TAG_SENTINEL
        # text only: every tag (a component's data-spec JSON included) is masked first
        out, _tags = mask_tags(final_html)
        tok = _re.escape(_TAG_SENTINEL) + r"[a-z]+" + _re.escape(_TAG_SENTINEL)
        tip = "Unsupported by the knowledge base (unsupported ≠ false) — details in the Grounding Gate card"
        for span in spans[:8]:
            # A claim is extracted from the answer's MARKDOWN ("the *learning* system");
            # the page shows it RENDERED ("the <em>learning</em> system"). Search for what
            # the reader sees: the claim through the same inline renderer as the answer,
            # its tags dropped, any formatting tag allowed between its characters.
            vis = _re.sub(r"<[^>]+>", "", md_inline(str(span)))
            if len(vis.strip()) < 2:
                continue
            core = "".join(_re.escape(ch) + (f"(?:{tok})*" if i < len(vis) - 1 else "")
                           for i, ch in enumerate(vis))
            # short/numeric spans need hard word boundaries so "42" never marks inside
            # "1042" or "x42y"
            if len(vis) < 6 or vis.replace(",", "").replace(".", "").isdigit():
                core = r"(?<![\w.])" + core + r"(?![\w.])"
            for m in _re.finditer(core, out):
                inner = [_tags[_idx] for _idx in _sentinel_indexes(m.group(0), _TAG_SENTINEL)]
                if not _balanced(inner):
                    continue          # the match would split an element — leave it unmarked
                out = (out[:m.start()] + f'<mark class="claim-unsupported u-tip" data-tip="{tip}">'
                       + m.group(0) + "</mark>" + out[m.end():])
                break                 # first clean occurrence per claim
        return unmask_tags(out, _tags)
    except Exception:
        return final_html


def apply_to_html(final_html: str, event: Optional[dict]) -> str:
    """Apply a gate verdict to rendered answer HTML: PASS → unchanged; ANNOTATE → mark
    unsupported claims inline (U2) + append the note; BLOCK → the answer is withheld, the
    note replaces it. Mirrors ``apply_to_answer`` for the HTML surface so display and
    stored text stay consistent."""
    decision = (event or {}).get("decision")
    note = annotation_html(event)
    if not note or decision == "pass":
        return final_html
    if decision == "block":
        return note
    marked = mark_unsupported_claims_html(final_html, event)
    from trusted_data_agent.agent import answer_surface as _ans
    return _ans.place_note(marked, note)


def gate_turn(turn: dict, answer_text: str, *, profile: Optional[dict],
              global_enabled: bool, default_thresholds: Optional[dict] = None
              ) -> Tuple[str, GroundingVerdict]:
    """One-call entrypoint for an engine: resolve config → build inputs → run the gate
    → apply the verdict. Returns ``(possibly-modified answer_text, verdict)``.

    Fails OPEN (charter #8): any gate *malfunction* returns the original answer + a PASS
    verdict, so a bug here can never break a turn. (A detected *breach* in enforce mode
    still withholds — that is the gate working, not failing.)"""
    try:
        cfg = resolve_config(profile, global_enabled, default_thresholds)
        verdict = GroundingGate(cfg).evaluate(build_inputs(turn, answer_text))
        return apply_to_answer(answer_text, verdict), verdict
    except Exception as e:  # never break the request path
        return answer_text, GroundingVerdict(Decision.PASS,
                                              f"gate error (failed open): {e}")


def session_context_text(workflow_history: Any) -> str:
    """The user's earlier words in this session, as one text: a session primer states dates
    and names a later answer may repeat, so they are part of what the turn's inputs vouch
    for. ONE definition, read by the grounding gate (`runtime.run_grounding`) and the
    compute-lineage seam (`executor._run_compute_lineage`) — two copies once let the gate
    accept a primer's year the lineage check then called the agent's own arithmetic."""
    turns = workflow_history if isinstance(workflow_history, list) else []
    return "\n".join(str(t.get("user_query") or "") for t in turns[-12:] if isinstance(t, dict))[:20000]

