"""Core eval types: verdicts, cases, and a normalized view over a turn's trace.

A *case* is data (query + declarative assertions). A *RunRecord* wraps the
``workflow_history`` turn dict a run produced (execution_trace, raw_llm_plan,
provenance_chain, final answer, tokens/cost, genie/knowledge metadata) and
exposes profile-class-agnostic accessors so scorers don't re-parse the raw dict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# The five profile classes WS3 must be world-class for.
ALL_PROFILE_CLASSES = (
    "llm_only",                 # Ideate
    "rag_focused",              # Focus
    "tool_enabled",             # Optimize (Planner/Executor)
    "genie",                    # Coordinate
    "conversation_with_tools",  # Ideate + MCP tools (ReAct)
)


class ScoreKind(str, Enum):
    DETERMINISTIC = "deterministic"  # code grader — runs always, cheap, CI-pure
    JUDGE = "judge"                  # LLM-as-judge — open-ended dims only
    METRIC = "metric"                # measured number (cost, latency, steps)


@dataclass
class Verdict:
    """One scorer's result for one dimension on one run.

    ``passed`` is None for "unknown / not applicable" (judges must be able to
    abstain rather than fabricate). ``score`` is 0..1 where meaningful.
    ``evidence`` cites the trace artifact(s) the verdict is grounded in.
    """
    dimension: str
    kind: ScoreKind
    passed: Optional[bool]
    score: Optional[float] = None
    explanation: str = ""
    evidence: list = field(default_factory=list)
    threshold: Optional[float] = None
    # 4-class health band for this metric: 'healthy' | 'review' | 'danger' | None.
    # Set by banded metric scorers (two cut points). Review (amber) passes the
    # gate but is flagged; only 'danger' (red) fails. None = no band applies.
    band: Optional[str] = None
    # LLM-judge verdicts only: the per-model panel votes behind the consensus —
    # [{model, verdict, score?}] in panel order. The consensus explanation carries
    # the tally ("2✓/1✗"); this carries the ATTRIBUTION (which model ruled what),
    # the transparency element the "Agent Quality Judged" card renders. None for
    # every deterministic scorer.
    votes: Optional[list] = None

    @property
    def gating(self) -> bool:
        """Does failing this dimension CONVICT the turn, or merely flag it?

        DERIVED from the scorer registry — the one place the fact is declared
        (`Scorer.gating`) — rather than stored on the verdict. A stored field is a
        second copy every construction site must remember to set, and none of them
        did: `Verdict.gating` used to be an implicit `True` default, so
        `eval.advisory_failures` (which reads `gating is False`) could only ever be
        0, and the frontend, having no flag at all, counted every failure as gating
        and called an advisory `numeric_fidelity` a "gating failure". A property
        cannot be forgotten.

        Unknown dimensions resolve to gating — treating something unrecognised as
        advisory would hide a real failure.
        """
        from trusted_data_agent.eval.scorer import is_gating_dimension
        return is_gating_dimension(self.dimension)

    def to_dict(self) -> dict:
        out = {
            "dimension": self.dimension,
            "kind": self.kind.value,
            "passed": self.passed,
            "score": self.score,
            "explanation": self.explanation,
            "evidence": self.evidence,
            "threshold": self.threshold,
            "band": self.band,
            # Shipped so the scorecard can tell a conviction from a flag. Without it
            # the card re-derived the policy from nothing and got it wrong; with it
            # there is one source and every surface reads the same answer.
            "gating": self.gating,
        }
        # Additive: absent votes keep the serialized shape byte-identical.
        if self.votes:
            out["votes"] = self.votes
        return out


@dataclass
class EvalCase:
    """A single golden case. Assertions are declarative so cases are data."""
    id: str
    query: str
    profile_class: str
    profile_tag: Optional[str] = None
    profile_id: Optional[str] = None
    cohort: str = "default"
    reference_answer: Optional[str] = None
    assertions: dict = field(default_factory=dict)
    dataset_id: Optional[str] = None
    source_turn_ref: Optional[dict] = None  # {session_id, turn} when mined from prod
    flow_id: Optional[str] = None           # set when profile_class == "flow" (flow-agent case)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "query": self.query, "profile_class": self.profile_class,
            "profile_tag": self.profile_tag, "profile_id": self.profile_id,
            "cohort": self.cohort, "reference_answer": self.reference_answer,
            "assertions": self.assertions, "dataset_id": self.dataset_id,
            "source_turn_ref": self.source_turn_ref, "flow_id": self.flow_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        return cls(
            id=d["id"], query=d.get("query", ""),
            profile_class=d.get("profile_class", "tool_enabled"),
            profile_tag=d.get("profile_tag"), profile_id=d.get("profile_id"),
            cohort=d.get("cohort", "default"), reference_answer=d.get("reference_answer"),
            assertions=d.get("assertions") or {}, dataset_id=d.get("dataset_id"),
            source_turn_ref=d.get("source_turn_ref"), flow_id=d.get("flow_id"),
        )


# Framework-internal tools that should never count as a "data tool" in trajectory
# scoring (they are scaffolding, not the agent acquiring/acting on data).
FRAMEWORK_TOOLS = frozenset({
    "TDA_FinalReport", "TDA_SystemLog", "TDA_ContextReport", "TDA_CurrentDate",
})


def chunk_support_text(doc, collection_names=()) -> str:
    """One retrieved chunk → the text it lends an answer as support: its content, then the
    citation labels it travelled with (document title / filename / source, repository).
    Empty content ⇒ "" (a label alone supports nothing — there is no chunk to cite)."""
    if not isinstance(doc, dict):
        return str(doc or "").strip()
    body = ""
    for k in ("content", "text", "document", "chunk"):
        v = doc.get(k)
        if v and str(v).strip():
            body = str(v)
            break
    if not body:
        return ""
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    labels = []
    for v in (doc.get("source"), doc.get("title"), doc.get("filename"),
              meta.get("title"), meta.get("filename"), meta.get("source")):
        if v and str(v).strip() and str(v).strip() not in labels:
            labels.append(str(v).strip())
    colls = [str(doc.get("collection_name")).strip()] if doc.get("collection_name") else []
    for c in collection_names or ():
        if c not in colls:
            colls.append(c)
    if not labels and not colls:
        return body
    line = "Source: " + "; ".join(labels) if labels else "Source:"
    if colls:
        line += f" (Collection: {'; '.join(colls)})"
    return f"{line}\n{body}"


class RunRecord:
    """Normalized, profile-class-agnostic view over a workflow_history turn dict.

    Wraps the raw turn (``last_turn_data.workflow_history[-1]``) so scorers read
    trajectory/answer/cost via stable accessors regardless of engine.
    """

    def __init__(self, turn: dict):
        self._t = turn or {}

    # ── identity / status ────────────────────────────────────────────────────
    @property
    def raw(self) -> dict:
        return self._t

    @property
    def profile_type(self) -> Optional[str]:
        return self._t.get("profile_type") or self._t.get("provenance_meta", {}).get("profile_type")

    @property
    def status(self) -> str:
        return self._t.get("status") or "success"

    @property
    def is_valid(self) -> bool:
        return bool(self._t.get("isValid", True))

    @property
    def error_message(self) -> Optional[str]:
        return self._t.get("error_message")

    # ── answer ───────────────────────────────────────────────────────────────
    @property
    def final_answer_text(self) -> str:
        # `final_response` is the genie coordinator's legacy answer key (also a
        # truncated preview on older turns); read it last so answer_present holds
        # for genie turns persisted before final_summary_text was added.
        text = (self._t.get("final_summary_text")
                or self._t.get("final_summary")
                or self._t.get("final_response")
                or "")
        return self._without_platform_note(text)

    def _without_platform_note(self, text: str) -> str:
        """The delivered text minus the note the grounding gate APPENDED on an annotate
        (`adapter.apply_to_answer`: `answer + "\n\n> " + annotation_md`). What is graded is
        the model's words; the note is the platform's, and it quotes the very spans it
        reports, so grading it manufactured a second failure for one finding."""
        g = self._t.get("grounding") if isinstance(self._t, dict) else None
        note = g.get("annotation_md") if isinstance(g, dict) and g.get("decision") == "annotate" else None
        if note and text:
            for form in (f"\n\n> {note}", f"\n> {note}", f"> {note}"):
                if text.endswith(form):
                    return text[:-len(form)].rstrip()
        return text

    def platform_authored_answer(self) -> Optional[str]:
        """Why the delivered text is NOT the model's own words — or ``None``.

        When a gate ACTS it replaces the answer with the platform's own text: a
        withholding notice, an annotation, an honest-refusal rewrite. Any scorer that
        compares the delivered text against the sources is then grading Uderia's prose
        as if the model had written it — and the withholding notice is the worst
        possible subject, because it QUOTES the very spans it is reporting as
        unsupported. The gate acting therefore manufactures a second failure at the
        same station: the same shortfall counted twice, and the measurements go blind
        at the exact moment an operator most needs them to explain the block.

        Stated ONCE here rather than remembered per scorer. It was remembered in
        exactly one of the three text-comparing scorers, which is how a blocked turn
        came to show `grounded_overlap` correctly abstaining beside `numeric_fidelity`
        red and `context_utilization` at 3% — both of them grading a 31-word notice.

        A guardrail REDACT is deliberately NOT included: it masks values inside an
        otherwise model-authored answer, so the text is still the model's and
        abstaining would discard real signal.
        """
        t = self._t if isinstance(self._t, dict) else {}
        g = t.get("grounding")
        if isinstance(g, dict) and g.get("decision") == "block":
            return ("withheld by the grounding gate — the delivered text is the platform's "
                    "notice, not a model claim")
        # An ANNOTATED answer is the model's text with a note appended; `final_answer_text`
        # drops the note, so the scorers grade the model's words and show what they read
        # (prod 510f7011: three modules "abstained" on a correct answer, and nothing on the
        # panel showed the reading that would have exposed the annotation as wrong).
        h = t.get("honesty")
        if isinstance(h, dict) and h.get("decision") == "block":
            return ("the answer was rewritten by the honesty gate — "
                    "the delivered text is the platform's message, not a model claim")
        gr = t.get("guardrail")
        if isinstance(gr, dict) and gr.get("action") == "block":
            return ("the answer was withheld by the outbound guardrail — "
                    "the delivered text is the platform's notice, not a model claim")
        return None

    # ── trajectory (tool/phase calls) ────────────────────────────────────────
    @property
    def execution_trace(self) -> list:
        tr = self._t.get("execution_trace")
        return tr if isinstance(tr, list) else []

    def tool_calls(self, include_framework: bool = False) -> list[dict]:
        """List of {tool_name, arguments, status, result} for each executed tool call.

        Different engines record tool calls differently: the Optimize/Planner engine
        uses ``execution_trace`` (action/result); the ReAct ``conversation_with_tools``
        engine uses ``conversation_agent_events``. Read whichever is populated so the
        tool scorers work for both tool-using classes."""
        out = self._tool_calls_from_trace(include_framework)
        if out:
            return out
        return self._tool_calls_from_conversation(include_framework)

    def _tool_calls_from_trace(self, include_framework: bool) -> list[dict]:
        out = []
        for entry in self.execution_trace:
            if not isinstance(entry, dict):
                continue
            action = entry.get("action") or {}
            name = action.get("tool_name")
            if not name:
                continue
            if not include_framework and name in FRAMEWORK_TOOLS:
                continue
            result = entry.get("result")
            status = "success"
            if isinstance(result, dict):
                summ = result.get("tool_output_summary")
                if isinstance(summ, dict):
                    status = summ.get("status", "success")
                else:
                    status = result.get("status", "success")
            out.append({
                "tool_name": name,
                "arguments": action.get("arguments") or {},
                "status": status,
                "result": result,
            })
        return out

    def _tool_calls_from_conversation(self, include_framework: bool) -> list[dict]:
        """ReAct (conversation_with_tools): pair conversation_tool_invoked with the
        following conversation_tool_completed (by order) for name/args/status."""
        events = self._t.get("conversation_agent_events")
        if not isinstance(events, list):
            return []
        invoked = [e.get("payload") or {} for e in events
                   if isinstance(e, dict) and e.get("type") == "conversation_tool_invoked"]
        completed = [e.get("payload") or {} for e in events
                     if isinstance(e, dict) and e.get("type") == "conversation_tool_completed"]
        out = []
        for i, p in enumerate(invoked):
            name = p.get("tool_name")
            if not name:
                continue
            if not include_framework and name in FRAMEWORK_TOOLS:
                continue
            comp = completed[i] if i < len(completed) else {}
            status = "success" if comp.get("success", True) else "error"
            out.append({
                "tool_name": name,
                "arguments": p.get("arguments") or {},
                "status": status,
                "result": comp or None,
            })
        return out

    def tool_names(self, include_framework: bool = False) -> list[str]:
        return [c["tool_name"] for c in self.tool_calls(include_framework)]

    # ── plan structure (Optimize) ────────────────────────────────────────────
    @property
    def plan(self) -> Optional[dict]:
        return self._t.get("raw_llm_plan") or self._t.get("original_plan")

    def planned_tools(self) -> list[str]:
        """Tools the strategic plan declared as relevant (Optimize).

        The plan is stored either as a dict ``{phases: [...]}`` or — for the
        Optimize/Planner engine — as a bare list of phase dicts. Handle both;
        anything else yields no planned tools (never raises)."""
        plan = self.plan
        if isinstance(plan, dict):
            phases = plan.get("phases") or []
        elif isinstance(plan, list):
            phases = plan
        else:
            phases = []
        names: list[str] = []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            for t in (phase.get("relevant_tools") or []):
                if t and t not in names:
                    names.append(t)
        return names

    # ── self-correction ──────────────────────────────────────────────────────
    @property
    def self_correction_count(self) -> int:
        # Primary signal: provenance steps (self_correction / error:* added by the
        # Planner/Executor on each correction attempt).
        n = 0
        for step in self.provenance_steps:
            if not isinstance(step, dict):
                continue
            st = (step.get("step_type") or "")
            if "self_correction" in st or st.startswith("error:"):
                n += 1
        # Robustness: an errored tool call implies a correction attempt; count those
        # from the trace too (covers turns whose corrections weren't recorded as
        # provenance steps). Take the larger signal to avoid double-counting the
        # same event recorded in both places.
        trace_errors = 0
        for c in self.tool_calls():
            status = str(c.get("status", "success")).lower()
            if status not in ("success", "ok", "complete", "completed"):
                trace_errors += 1
        return max(n, trace_errors)

    # ── knowledge / RAG (Focus) ──────────────────────────────────────────────
    @property
    def knowledge_event(self) -> dict:
        ev = self._t.get("knowledge_retrieval_event")
        return ev if isinstance(ev, dict) else {}

    def retrieved_documents(self) -> list:
        ev = self.knowledge_event
        docs = ev.get("documents") or ev.get("chunks") or ev.get("results") or []
        return docs if isinstance(docs, list) else []

    def retrieved_chunk_texts(self) -> tuple:
        """THE support text of each retrieved chunk — one definition for both assessment
        lanes (the in-flight grounding gate and the post-hoc assessors). A chunk is its
        content AND the citation line the model was shown beside it ("Source: <document>
        (Collection: <repository>)", executor._format_knowledge_for_prompt): a model citing
        the document it read is quoting its evidence, never an unsupported claim."""
        names = [str(c) for c in (self.knowledge_event.get("collections") or [])
                 if isinstance(c, (str, int, float)) and str(c).strip()]
        return tuple(t for t in (chunk_support_text(d, names) for d in self.retrieved_documents()) if t)

    def support_chunk_texts(self) -> tuple:
        """Everything the answer may legitimately draw on, as chunks: the retrieved chunks
        (`retrieved_chunk_texts`) and the turn's attachments — S9's verified readings when the
        turn carries them, else the attachment text the scorer was handed
        (`attachment_evidence_texts`). ONE definition for the in-flight gate and every post-hoc
        reader of corpus support; a figure quoted from the attached contract is not unsupported."""
        out = list(self.retrieved_chunk_texts())
        s9 = [t for t in (chunk_support_text(d) for d in self.attachment_chunks()) if t.strip()]
        out.extend(s9 or self.attachment_evidence_texts())
        return tuple(out)

    @property
    def rag_collection_id(self):
        return self._t.get("rag_source_collection_id")

    def kg_scope_databases(self) -> set:
        """The DEPLOYED, queryable database(s) the KG enrichment declared as the
        model's scope (lower-cased). Empty when no KG enrichment fired or the graph
        is logical-only/undeployed — in which case scope adherence can't be judged."""
        ev = self._t.get("kg_enrichment_event")
        if not isinstance(ev, dict):
            return set()
        out = set()
        for e in ev.get("enrichments", []) or []:
            if isinstance(e, dict):
                for db in (e.get("scope_databases") or []):
                    if db:
                        out.add(str(db).lower())
        return out

    def kg_scope_entities(self) -> set:
        """The in-scope concept/entity NAMES the KG enrichment declared (lower-cased) —
        the allowlist the recall-first grounding trigger checks answer-referenced
        identifiers against, so an in-scope table/entity named in prose is recognized and
        an UNRECOGNIZED one trips the judge. Empty when no enrichment fired (trigger then
        degrades to database-only). Reads the stamped scope_entities
        (handler._derive_scope_entities)."""
        ev = self._t.get("kg_enrichment_event")
        if not isinstance(ev, dict):
            return set()
        out = set()
        for e in ev.get("enrichments", []) or []:
            if isinstance(e, dict):
                for n in (e.get("scope_entities") or []):
                    if n:
                        out.add(str(n).lower())
        return out

    def vouched_numbers(self) -> set:
        """Numbers the turn's INPUTS account for, so a figure check never calls them
        "not matched to the data": the user's own words (this question, plus the earlier
        user turns handed in as ``context_text`` — a session primer states dates the answer
        may repeat) and the codes / year spans the knowledge graph DECLARES (value domains,
        the values a correspondence binds, a text date's measured span). Session f622f5ef
        turn 2 flagged `120`, `220` (declared offence codes) and `2018`, `2023` (the
        primer's own years) as unmatched figures.

        Declared values count only from three digits up: a one- or two-digit code would
        otherwise vouch for any small count the answer invents. ONE definition, read by the
        grounding gate and the `numeric_fidelity` scorer."""
        from trusted_data_agent.agent.grounding.primitives import norm_nums
        out = set(norm_nums(str(self._t.get("user_query") or "")))
        out |= norm_nums(str(self._t.get("context_text") or ""))
        # the findings block the platform showed the model (verified results of earlier turns)
        out |= norm_nums(str(self._t.get("findings_context") or ""))
        _g = self._t.get("grounding")
        if isinstance(_g, dict):                       # the gate's persisted copy of the above
            out |= {str(n) for n in (_g.get("context_numbers") or [])}
        declared = []
        ev = self._t.get("kg_enrichment_event")
        for e in ((ev or {}).get("enrichments", []) if isinstance(ev, dict) else []) or []:
            if not isinstance(e, dict):
                continue
            for vd in e.get("value_domains") or []:
                for c in (vd or {}).get("columns") or []:
                    declared.extend(str(k) for k in ((c or {}).get("values") or {}).keys())
            for b in e.get("class_bridges") or []:
                declared.extend(str(v) for v in ((b or {}).get("from_values") or []))
                declared.extend(str(v) for v in ((b or {}).get("to_values") or []))
            for td in e.get("text_date_columns") or []:
                for c in (td or {}).get("columns") or []:
                    declared.extend(str(v)[:4] for v in ((c or {}).get("span") or []))
        for d in declared:
            d = d.strip()
            if d.isdigit() and len(d) >= 3:
                out.add(d)
        return out

    def kg_seeding_reports(self) -> list:
        """The per-graph ``semantic_seeding`` reports the KG enrichment stamped
        ({state, hits, bridged:[{name, score}]}). Empty when no enrichment fired or the
        event predates the channel."""
        ev = self._t.get("kg_enrichment_event")
        if not isinstance(ev, dict):
            return []
        out = []
        for e in ev.get("enrichments", []) or []:
            if isinstance(e, dict) and isinstance(e.get("semantic_seeding"), dict):
                out.append(e["semantic_seeding"])
        return out

    def kg_bridged_entities(self) -> list:
        """Qualified names of the entities the question reached ONLY by meaning — the
        semantic channel's contribution on this turn. Empty on a lexical-only turn."""
        out = []
        for r in self.kg_seeding_reports():
            for b in r.get("bridged") or []:
                n = (b or {}).get("name") if isinstance(b, dict) else b
                if n and str(n) not in out:
                    out.append(str(n))
        return out

    def kg_entity_names(self) -> list:
        """Qualified names of every entity the enrichment rendered to the model."""
        ev = self._t.get("kg_enrichment_event")
        if not isinstance(ev, dict):
            return []
        out = []
        for e in ev.get("enrichments", []) or []:
            if isinstance(e, dict):
                for n in e.get("entity_names") or []:
                    if n and str(n) not in out:
                        out.append(str(n))
        return out

    def kg_deployed_names(self) -> list:
        """Deployed identifiers of the entities the enrichment rendered (from its
        ``entity_facts``) — what the SQL names when a logical entity is deployed under
        another name. ONE reader: ``kgstore.usage.deployed_names``."""
        ev = self._t.get("kg_enrichment_event")
        if not isinstance(ev, dict):
            return []
        try:
            from trusted_data_agent.kgstore.usage import deployed_names
            return deployed_names(ev.get("enrichments") or [])
        except Exception:  # noqa: BLE001
            return []

    def kg_scope_description(self) -> str:
        """A human-readable description of the KG-declared TRUSTED scope — the
        database(s) and the concepts they cover — for the scope_faithfulness judge and
        transparent reload/audit. Empty when no scope was declared (judge then abstains).
        Reads the self-describing scope_databases + scope_entities the KG enrichment
        stamped (handler._derive_scope_databases / _derive_scope_entities)."""
        ev = self._t.get("kg_enrichment_event")
        if not isinstance(ev, dict):
            return ""
        dbs, ents = set(), []
        for e in ev.get("enrichments", []) or []:
            if isinstance(e, dict):
                for db in (e.get("scope_databases") or []):
                    if db:
                        dbs.add(str(db))
                for n in (e.get("scope_entities") or []):
                    if n and str(n) not in ents:
                        ents.append(str(n))
        if not dbs and not ents:
            return ""
        parts = []
        if dbs:
            parts.append("database(s): " + ", ".join(sorted(dbs)))
        if ents:
            shown = ents[:40]
            parts.append("covering: " + ", ".join(shown) + ("…" if len(ents) > 40 else ""))
        return "; ".join(parts)

    # ── genie / Coordinate ───────────────────────────────────────────────────
    @property
    def genie_metadata(self) -> dict:
        gm = self._t.get("genie_metadata")
        return gm if isinstance(gm, dict) else {}

    def slaves_invoked(self) -> list[dict]:
        # Coordinator turns persist the per-expert summary at the top level
        # (tag/status/tokens/cost); fall back to the genie_metadata shapes.
        top = self._t.get("slaves_invoked")
        if isinstance(top, list) and top:
            return top
        gm = self.genie_metadata
        sl = gm.get("slaves_invoked") or gm.get("profiles_invoked") or gm.get("slaves") or []
        return sl if isinstance(sl, list) else []

    @property
    def genie_rollup(self) -> dict:
        """Coordinator turn's inclusive resource attribution (coordinator + experts).

        Present only on genie turns. Drives the eval cost/token KPIs so the
        coordinator's Quality card reflects the true cost of the delegated turn
        (the experts billed their own child sessions — this is attribution, not a
        second charge)."""
        r = self._t.get("genie_rollup")
        return r if isinstance(r, dict) else {}

    # ── cost / tokens / latency ──────────────────────────────────────────────
    @property
    def turn_cost_usd(self) -> float:
        # SELF-cost: every turn (coordinator AND each expert) carries its OWN cost as
        # the scored metric, so portfolio sums never double-count. The coordinator's
        # inclusive (coordinator + experts) figure lives in genie_rollup and is shown
        # as a display-only "delegated / turn total" annotation, never summed.
        return float(self._t.get("turn_cost") or 0.0)

    @property
    def total_tokens(self) -> int:
        return int(self._t.get("turn_input_tokens") or 0) + int(self._t.get("turn_output_tokens") or 0)

    @property
    def duration_ms(self) -> int:
        """Turn wall-clock latency in ms.

        New turns carry a top-level ``duration_ms`` (stamped by every engine from the
        canonical turn timer). For turns persisted before that — or any engine that
        only records it inside its completion event — fall back to the per-class
        completion-event payload, which every class emits ("execution complete" in the
        Live Status). This keeps the latency KPI populated for all profile classes
        across historical data, without re-running the turn.
        """
        top = int(self._t.get("duration_ms") or 0)
        if top > 0:
            return top
        # Defensive fallback for turns persisted without a top-level duration_ms:
        # read it from the per-class completion event ("execution complete" in the
        # Live Status). The turn encloses every sub-step, so the whole-turn latency
        # is the MAX of the whole-turn candidates — which excludes sub-step timings
        # (e.g. the 78ms retrieval step or an inner agent's 2931ms) and lands on the
        # outermost execution_complete. Whole-turn candidates are: any total_duration_ms
        # (only emitted on completion), and duration_ms but ONLY on completion-typed
        # events (an engine like Optimize records the whole turn there).
        best = 0
        for ev_key in ("tool_enabled_events", "knowledge_events",
                       "conversation_agent_events", "genie_events"):
            events = self._t.get(ev_key)
            if not isinstance(events, list):
                continue
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev
                cands = [payload.get("total_duration_ms")]
                if "complete" in str(ev.get("type") or ""):
                    cands.append(payload.get("duration_ms"))
                for val in cands:
                    if isinstance(val, (int, float)) and val > best:
                        best = int(val)
        return best

    # ── context-window health (data-loss-under-pressure signals) ──────────────
    @property
    def context_snapshot(self) -> dict:
        cs = self._t.get("context_window_snapshot_event")
        return cs if isinstance(cs, dict) else {}

    def context_condensations(self) -> int:
        """How many modules were condensed / RAG-offloaded (recent context dropped)."""
        c = self.context_snapshot.get("condensations")
        return len(c) if isinstance(c, list) else 0

    def context_distillations(self) -> int:
        """How many large tool outputs were distilled (summarized, detail lost)."""
        d = self.context_snapshot.get("distillation_events")
        return len(d) if isinstance(d, list) else 0

    # ── retrieval scores (Focus) ──────────────────────────────────────────────
    def retrieval_scores(self) -> list:
        out = []
        for d in self.retrieved_documents():
            if not isinstance(d, dict):
                continue
            s = d.get("similarity_score")
            if s is None:
                s = d.get("score") or d.get("similarity") or d.get("relevance")
            if s is not None:
                try:
                    out.append(float(s))
                except (TypeError, ValueError):
                    pass
        return out

    # ── grounding source text (tool results + retrieved docs) ─────────────────
    def grounding_text(self, exclude_call=None) -> str:
        """``exclude_call``: a predicate dropping a tool call's result (the scope trigger's
        value text leaves catalog calls out — `grounding.primitives.is_catalog_call`)."""
        parts = []
        for c in self.tool_calls():
            if exclude_call is not None and exclude_call(c):
                continue
            r = c.get("result")
            if isinstance(r, dict):
                parts.append(str(r.get("result_preview") or r.get("tool_output_summary") or r))
            elif r:
                parts.append(str(r))
        # A retrieved chunk is its content AND the citation line the model was shown — the
        # ONE support-text definition (`retrieved_chunk_texts`). Reading content alone here
        # made the numeric-fidelity scorer fail an answer for citing its source document
        # ("…Oct 2025 v0 2", "…_20251028") while the corpus gate, reading the citation line,
        # correctly passed the same numbers (prod 49dac4d2, Oct 2026).
        parts.extend(self.retrieved_chunk_texts())
        # S9: what the platform READ from the turn's attachments (verified sources only — a
        # model transcription is never evidence for the model's own answer)
        at = self.attachment_grounding_text()
        if at:
            parts.append(at)
        else:
            parts.extend(self.attachment_evidence_texts())
        return " ".join(parts)

    # ── attachment evidence (PROP-010) ───────────────────────────────────────
    # The turn records WHICH attachments it read (`attachment_evidence.files`); the text is
    # filled in by whoever builds the record with access to the conversation
    # (`session_files.with_attachment_evidence`) — this class stays pure.
    def attachment_evidence_texts(self) -> list:
        ev = self._t.get("attachment_evidence")
        tx = ev.get("texts") if isinstance(ev, dict) else None
        return [str(t) for t in tx if t] if isinstance(tx, list) else []

    def attachment_evidence_lost(self) -> list:
        """The attachments this turn read that are no longer readable (deleted from Files
        since): their evidence cannot be re-read, so a support score would be guesswork."""
        ev = self._t.get("attachment_evidence")
        lost = ev.get("lost") if isinstance(ev, dict) else None
        return [str(x) for x in lost if x] if isinstance(lost, list) else []

    def attachment_evidence_lost_reason(self) -> str:
        """ONE wording for every support reader that must abstain: "" when nothing was lost."""
        lost = self.attachment_evidence_lost()
        if not lost:
            return ""
        names = ", ".join(lost[:3]) + (f" and {len(lost) - 3} more" if len(lost) > 3 else "")
        return (f"the attached {'document' if len(lost) == 1 else 'documents'} {names} "
                f"{'was' if len(lost) == 1 else 'were'} deleted since — the answer's evidence "
                f"can no longer be read")

    # ── attachment sources (S9) ──────────────────────────────────────────────
    def attachment_sources(self) -> list[dict]:
        """The turn's attachment sources (`turn['attachment_sources']['sources']`): every
        upload the platform read, with method / verified / tables. Empty when none."""
        a = self._t.get("attachment_sources")
        ss = a.get("sources") if isinstance(a, dict) else None
        return [s for s in ss if isinstance(s, dict)] if isinstance(ss, list) else []

    def attachment_grounding_text(self) -> str:
        a = self._t.get("attachment_sources")
        return str(a.get("grounding_text") or "") if isinstance(a, dict) else ""

    def attachment_chunks(self) -> list[dict]:
        """The verified attachment sources as retrieved-chunk-shaped dicts, so the corpus-
        support family checks the answer's claims against what the platform read."""
        out = []
        for s in self.attachment_sources():
            if not s.get("verified"):
                continue
            txt = self.attachment_grounding_text()
            if txt:
                out.append({"content": txt, "source": s.get("filename"), "attachment": True, "file_id": s.get("file_id")})
                break   # one chunk carries every verified source's text already
        return out

    # ── compute lineage (S3) ─────────────────────────────────────────────────
    def compute_receipts(self) -> list[dict]:
        """The turn's compute receipts (`turn['compute']['receipts']`) — the figures a
        deterministic computation vouches for. Empty when the turn computed nothing."""
        c = self._t.get("compute")
        rs = c.get("receipts") if isinstance(c, dict) else None
        return [r for r in rs if isinstance(r, dict)] if isinstance(rs, list) else []

    def compute_lineage(self) -> Optional[dict]:
        """The persisted number-lineage event (`turn['compute_lineage']`), or None."""
        e = self._t.get("compute_lineage")
        return e if isinstance(e, dict) else None

    # ── progressive skill libraries (§4a) ────────────────────────────────────
    def library_use(self) -> Optional[dict]:
        """The turn's read → ran record (`turn['library_use']`, `skills.library_use.record`),
        or None when the agent carries no library or the turn neither read a section nor ran
        a statement using a library operator."""
        e = self._t.get("library_use")
        return e if isinstance(e, dict) else None

    # ── provenance / EPC ─────────────────────────────────────────────────────
    @property
    def provenance_steps(self) -> list:
        ch = self._t.get("provenance_chain")
        return ch if isinstance(ch, list) else []

    @property
    def provenance_meta(self) -> dict:
        meta = self._t.get("provenance_meta")
        return meta if isinstance(meta, dict) else {}

    def step_types(self) -> list[str]:
        return [s.get("step_type", "") for s in self.provenance_steps if isinstance(s, dict)]
