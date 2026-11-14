"""
A persisted turn → an `AssessmentContext`.

The ONE adapter between the eval layer's `RunRecord` and the assurance contract. Every
scorer that binds to a property uses it, so the mapping from "what the turn recorded" to
"what an assessor reads" exists once. Building it per scorer is how two measurements of one
property end up reading different inputs — the defect the symmetry map scores 1/5 at A17,
where the authority measurements grade `case.assertions` while the boundary enforces the
agent's real scope.

DUCK-TYPED ON PURPOSE. It reads accessors, not a type, so `agent/` does not take an import
dependency on `eval/` and a caller may pass any object exposing the same surface (the
benchmark harness does). Every read is guarded: a turn that lacks a field yields the empty
default, and the assessor's own `requires` then decides whether that is an abstention —
absence is never guessed at here.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contract import AssessmentContext


def _call(obj: Any, name: str, default):
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        return fn() if callable(fn) else fn
    except Exception:
        return default


def _doc_text(d: Any) -> str:
    if isinstance(d, Mapping):
        for k in ("text", "content", "document", "chunk"):
            v = d.get(k)
            if v:
                return str(v)
        return ""
    return str(d or "")


def _tool_text(call: Any) -> str:
    if not isinstance(call, Mapping):
        return ""
    r = call.get("result")
    if isinstance(r, Mapping):
        return str(r.get("result_preview") or r.get("tool_output_summary") or r)
    return str(r or "")


def from_run(run: Any) -> AssessmentContext:
    """Build the context for a POST-HOC assessment of a completed turn."""
    raw = getattr(run, "_t", None) or {}
    docs = _call(run, "retrieved_documents", ()) or ()
    scores = _call(run, "retrieval_scores", ()) or ()
    calls = _call(run, "tool_calls", ()) or ()
    scope = _call(run, "kg_scope_databases", ()) or ()

    # What the MECHANISMS recorded on this turn, namespaced by mechanism. This is what lets
    # a measurement READ a finding instead of re-deriving it — the difference between a 4
    # and a 5 in the symmetry map, and the reason a span the gate cleared is not convicted
    # a second time after the fact.
    #
    # Namespaced rather than flattened: two mechanisms may legitimately record a key of the
    # same name, and a flat merge would let one silently overwrite the other — a finding
    # attributed to the wrong mechanism is worse than one that is absent.
    stamp = {}
    if isinstance(raw, Mapping):
        for name in ("grounding", "authority", "guardrail", "guardrail_inbound",
                     "honesty", "honesty_citation", "obda"):
            v = raw.get(name)
            if isinstance(v, Mapping):
                stamp[name] = v

    return AssessmentContext(
        answer=_call(run, "final_answer_text", "") or "",
        question=str((raw or {}).get("user_query") or "") if isinstance(raw, Mapping) else "",
        tool_calls=tuple(calls),
        # the ONE definition of a chunk's support text, shared with the in-flight gate
        # (content + the citation line the model was shown) — two extractors had drifted
        # + the turn's attachments (`support_chunk_texts`: S9 readings, else the attachment text)
        chunks=tuple(_call(run, "support_chunk_texts", ()) or ()) or tuple(t for t in (_doc_text(d) for d in docs) if t),
        # Mirrors `RunRecord.grounding_text()`'s own definition of tool evidence, so the
        # support set an assessor sees is the one the turn actually had.
        tool_outputs=tuple(t for t in (_tool_text(c) for c in calls) if t),
        retrieval_scores=tuple(float(s) for s in scores if isinstance(s, (int, float))),
        scope_databases=tuple(str(s) for s in scope),
        stamp=stamp,
        # ONE definition of "the delivered text is not the model's", shared with every
        # scorer that already asks it (RunRecord.platform_authored_answer).
        platform_authored=str(_call(run, "platform_authored_answer", "") or ""),
        evidence_lost=str(_call(run, "attachment_evidence_lost_reason", "") or ""),
    )
