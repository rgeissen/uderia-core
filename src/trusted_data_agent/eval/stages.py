"""The measurement process model — which STAGE of a turn each check measures.

WHY THIS EXISTS
---------------
Measurements used to be filed by *which guarantee owns them* (the station partition).
That question died with the lane cut: nothing owns a measurement any more — every scored
dimension is counted at JUDGED (`assurance_points.MEASUREMENT_STATION`), and the seven
guarantee stations report what their MECHANISMS did. Continuing to group 52 measurements
under station names would keep implying an ownership the code no longer has.

So the organising question changed, from *who owns this* to:

    WHICH STAGE OF THE TURN DOES THIS CHECK MEASURE?

and the answer is this file — one ordered, total, mutually exclusive partition of every
scored dimension, in the sequence a turn actually runs.

WHY A PROCESS MODEL RATHER THAN A TOPIC MODEL
---------------------------------------------
A topic grouping (Answer · Support · Safety · Cost …) was drafted first and rejected, for
reasons worth keeping so it is not re-proposed:

1. **A process has a canonical ORDER; a topic list does not.** Every surface that groups
   measurements — the scorecard, the roster's dimension bars, the band strip, the compare
   heatmap's columns, the Guide's shelf, the admin threshold sliders — can now render in
   the order the turn ran. The retired topic map (`TIER`) needed a hand-picked
   `TIER_ORDER` beside it precisely because its groups had no inherent sequence.
2. **It shares ONE mental model with the runtime.** Mechanisms are already understood by
   *when they fire*; measurements now are too. Two vocabularies for one turn is the
   drift this codebase keeps paying for.
3. **An absent stage is INFORMATION.** A `rag_focused` agent shows no `act` — it does not
   use tools. A `genie` shows a heavy `plan` and a thin `act` — a coordinator decides a
   lot and does little itself. Under a topic grouping an absent group is just absence.
4. **It cuts cleanly across the stations**, so no reader can mistake it for the retired
   ownership: `deliver` alone draws from dimensions formerly labelled `guarded`,
   `proven`, and from the unlabelled residue.

THE ONE THING THAT IS NOT A STAGE
---------------------------------
`the_run` is the whole-turn wrapper, deliberately NOT presented as a step in the
sequence. `cost` / `tokens` / `latency` span every stage, and `clean_termination` /
`provenance_integrity` are about the turn having ended and been recorded. Filing them
under a stage would claim they were measured at a moment they were not — and it was
also what made `deliver` a ten-chip grab-bag before they moved here.

ADDING A SCORER
---------------
Add its dimension to exactly one stage below. `test_eval_stages.py` fails the build on a
dimension that is in no stage, in two stages, or in no scorer registry — so a new scorer
cannot silently land in an "other" bucket that nobody reads.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

#: The stages, IN TURN ORDER. The order is the model's main asset — every consumer
#: renders in this sequence, so a reader scans a turn the way it ran.
#:
#: Wire keys are snake_case and stable; the labels are the user-facing words. A surface
#: renders `STAGE_LABEL[key]`, never the key (a wire token on a trust surface is the
#: defect class `TRUST_STATUS_COMMUNICATION.md` bans).
PLAN, RETRIEVE, ACT, COMPOSE, DELIVER, THE_RUN, FLOW_RUN = (
    "plan", "retrieve", "act", "compose", "deliver", "the_run", "flow_run")

STAGE_ORDER: Tuple[str, ...] = (
    PLAN, RETRIEVE, ACT, COMPOSE, DELIVER, THE_RUN, FLOW_RUN)

STAGE_LABEL: Dict[str, str] = {
    PLAN: "Plan",
    RETRIEVE: "Retrieve",
    ACT: "Act",
    COMPOSE: "Compose",
    DELIVER: "Deliver",
    THE_RUN: "The run",
    FLOW_RUN: "Flow run",
}

#: What each stage tells a reader — one sentence, in the operator's terms. These are the
#: group subtitles; they must say what is MEASURED, never how (rule vs judge is its own
#: channel) and never whether it gates (also its own channel).
STAGE_DESC: Dict[str, str] = {
    PLAN: "How the agent decided what to do — including which experts it routed to.",
    RETRIEVE: "Whether the knowledge lookup actually found and returned usable material.",
    ACT: "How it used its tools and data operations.",
    COMPOSE: "How it turned the evidence it had into an answer.",
    DELIVER: "What actually reached the reader — present, well-formed, safe, on-target.",
    THE_RUN: "Measured over the whole turn rather than at one stage: what it cost, and "
             "whether the turn ended and was recorded soundly.",
    FLOW_RUN: "A flow's own process — its nodes, not a turn's stages.",
}

#: FLOW-ONLY, which is NOT the same as exclusive — a distinction the gate caught after
#: this was first declared the stronger way from a run that never tested the flow class.
#:
#: No turn class produces a `flow_run` dimension, so a profile-agent never renders this
#: group. The converse does NOT hold: a flow-agent also produces `tasks` and
#: `parallel_efficiency` (work volume and speedup apply to a flow as much as to a turn),
#: so it renders PLAN beside FLOW_RUN. Claiming exclusivity would have made a surface
#: hide two real measurements on every flow run.
FLOW_ONLY_STAGES = frozenset({FLOW_RUN})

#: THE PARTITION. Every scored dimension appears under exactly one stage.
#:
#: Two placements are judgement calls and are recorded as such:
#:   * `scope_adherence` / `governed_data_ratio` sit in ACT, not COMPOSE. They count what
#:     the agent DID — data-source calls outside the declared scope, and free-formed SQL
#:     versus the governed semantic layer. Both are operations, measured on the calls.
#:   * `context_health` sits in COMPOSE. It measures context-window pressure, and a
#:     degraded context damages composition most directly. RETRIEVE would also be
#:     defensible; COMPOSE was chosen because the check is about the material the model
#:     writes FROM, not about the lookup that fetched it.
STAGE_DIMENSIONS: Dict[str, Tuple[str, ...]] = {
    PLAN: (
        "plan_adherence", "plan_execution", "steps", "tasks", "efficiency",
        "parallel_efficiency", "routing", "coordination_efficiency",
    ),
    RETRIEVE: (
        "retrieval_performed", "retrieval_relevance", "knowledge_freshness",
        # RETRIEVE, not ACT: it grades what the knowledge-graph LOOKUP brought — whether
        # the entities reached by meaning were worth reaching — measured on the calls
        # that followed, but about the retrieval.
        "kg_seed_utilization",
        # RETRIEVE: of the tables the turn READ, how many the lookup had brought — a miss
        # is a fact about the retrieval, measured on the calls that followed.
        "enrichment_recall",
        # RETRIEVE: whether the agent opened the library section before running the SQL it
        # teaches — a fact about the lookup (did it consult the manual), measured on the
        # statements that followed.
        "library_read_first",
    ),
    ACT: (
        "tool_subset", "must_not_call", "tool_success_rate", "no_tool_loops", "tool_calls",
        "tool_arg_optimality", "self_correction", "error_recovery", "expert_success",
        "scope_adherence", "governed_data_ratio", "surface_violation",
        # ACT: of the signed-action writes the turn put to the database, the share whose
        # every check held — a fact about the act itself.
        "action_safety",
    ),
    COMPOSE: (
        "grounded_overlap", "groundedness", "answer_groundedness", "numeric_fidelity",
        "citations", "honest_absence", "source_consistency", "scope_faithfulness",
        "grain_faithfulness",
        # COMPOSE, not DELIVER: the fetch it failed to make would have happened while the
        # answer was being assembled, and the defect is in the assembly — prose written
        # from recall while attributed to a source. Delivery carried the claim faithfully;
        # composition is where it stopped being true.
        "citation_fidelity", "identifier_provenance",
        "synthesis_quality", "context_utilization", "context_health",
    ),
    DELIVER: (
        "answer_present", "completed", "format", "output_sanity", "safety",
        "content_safety", "answer_correctness", "instruction_following",
    ),
    THE_RUN: (
        "cost", "tokens", "latency", "clean_termination", "provenance_integrity",
        # PROP-011: a turn that went past a code-detected ambiguity without asking — a fact of the whole run
        "guessed_past_ambiguity",
        # S2: the cached share of the turn's prompt tokens — a cost fact over the whole run.
        "cache_efficiency",
        # S5: within / graceful / killed against the declared turn budget — a fact over the run.
        "budget_adherence",
    ),
    FLOW_RUN: (
        "flow_completed", "flow_nodes_succeeded", "flow_nodes_reached",
        "flow_forbidden_nodes", "flow_cost", "flow_efficiency",
    ),
}

_BY_DIM: Dict[str, str] = {
    d: stage for stage, dims in STAGE_DIMENSIONS.items() for d in dims}


def stage_of(dimension: str) -> Optional[str]:
    """The stage a dimension is measured at, or ``None`` if it is not declared.

    ``None`` is deliberately NOT an "other" bucket. A dimension with no stage is a
    registry gap the gate fails on, and quietly filing it under a catch-all is how a new
    scorer lands somewhere nobody reads. A caller that must render something for an
    unknown key should say it is unclassified, not invent a home for it.
    """
    return _BY_DIM.get(dimension)


def stage_dimensions() -> Dict[str, Tuple[str, ...]]:
    """The partition itself — stage → its dimensions, in declaration order."""
    return dict(STAGE_DIMENSIONS)


def ordered_stages() -> Tuple[str, ...]:
    """The stages in turn order. Every grouping surface renders in this sequence."""
    return STAGE_ORDER


def group_dimensions(dimensions) -> List[Tuple[str, List[str]]]:
    """Group an arbitrary set of dimensions into ``[(stage, [dims…])]``, in turn order.

    Only stages that actually received a dimension are returned — an empty stage is
    omitted rather than rendered as a heading over nothing, because on a real turn an
    ABSENT stage is a fact about the agent (a knowledge agent has no `act`), and an empty
    header would read as "this stage ran and measured nothing", which is the opposite.

    A dimension with no declared stage is dropped from the grouping and returned by
    `unclassified()` instead, so a caller can surface it honestly rather than have it
    vanish between the two.
    """
    seen = {d for d in dimensions if isinstance(d, str)}
    out: List[Tuple[str, List[str]]] = []
    for stage in STAGE_ORDER:
        members = [d for d in STAGE_DIMENSIONS[stage] if d in seen]
        if members:
            out.append((stage, members))
    return out


def unclassified(dimensions) -> List[str]:
    """Dimensions with no declared stage — the gap `group_dimensions` refuses to hide."""
    return sorted(d for d in dimensions
                  if isinstance(d, str) and d not in _BY_DIM)


#: How a MOMENT is derived — the sentence its Guide entry adds under the description.
#: Written per stage rather than generated, because what makes each moment distinct is
#: not its position but WHAT IT IS, and a template would say the same thing seven times.
STAGE_DERIVATION: Dict[str, str] = {
    PLAN: "Everything the agent settled before it acted: how many steps it chose, "
          "whether it followed the plan it made, and — for a coordinator — which "
          "experts it routed to. A heavy plan on a thin turn is bloat; a plan the "
          "execution then ignored is a different problem, and both are measured here.",
    RETRIEVE: "Only the lookup itself: did retrieval run, did it return material "
              "relevant to the question, and how old is the freshest thing it found. "
              "Whether the ANSWER then used that material is measured at Compose — "
              "retrieving well and ignoring the result is a real failure mode, and "
              "splitting the two is what makes it visible.",
    ACT: "What the agent DID: which tools it called, whether they succeeded, whether it "
         "stayed inside the tools and data it was allowed, and whether it looped or "
         "corrected itself. A coordinator's experts count here too — delegating is "
         "acting.",
    COMPOSE: "Turning evidence into prose. Everything about whether the answer is "
             "actually supported by what the agent had — overlap with the sources, "
             "numeric fidelity, citations, honest absence when the material is missing "
             "— plus the health of the context it wrote from.",
    DELIVER: "What reached the reader: that an answer exists and is well-formed, that "
             "it is safe, and that it is on-target for what was asked. The last moment "
             "anything can be judged about the answer as an answer.",
    THE_RUN: "Not a moment in the sequence — the whole turn, wrapped. Cost, tokens and "
             "latency span every stage, and clean termination and the provenance seal "
             "are about the turn having ended and been recorded. Filing them under one "
             "stage would claim they were measured at a moment they were not.",
    FLOW_RUN: "A flow is a different process from a turn: nodes and edges, not plan and "
              "answer. Its checks live together here rather than being forced into "
              "moments that do not describe it. A flow-agent still reports Plan, "
              "because work volume and speedup apply to a flow as much as to a turn.",
}


def stage_entry(key: str) -> Optional[Dict[str, object]]:
    """A Guide-shaped entry for a MOMENT, mirroring `assurance_points.station_entry`.

    WHY THIS EXISTS. The Assurance Guide is the mental model of the trust runtime, and
    it was missing an entire class: it could explain a GUARANTEE (station) and a
    MECHANISM, but not a MOMENT OF THE TURN — while a moment is how 52 of its entries
    are grouped. A reader saw a shelf headed "Compose" and had nowhere to ask what that
    meant, and a header route had nothing to point at (which is why one briefly degraded
    to a tooltip — a patch on a missing concept rather than a design).

    The entry carries its checks, its position in the sequence, and its NEIGHBOURS, so a
    reader can walk the turn — Plan → Retrieve → Act → Compose → Deliver — and step into
    the moment from any check taken there.
    """
    if key not in STAGE_DIMENSIONS:
        return None
    seq = [s for s in STAGE_ORDER if s not in FLOW_ONLY_STAGES and s != THE_RUN]
    pos = seq.index(key) if key in seq else -1
    nav: Dict[str, object] = {}
    if pos >= 0:
        nav["position"] = pos + 1
        nav["of"] = len(seq)
        if pos > 0:
            nav["prev"] = f"stage_{seq[pos - 1]}"
        if pos < len(seq) - 1:
            nav["next"] = f"stage_{seq[pos + 1]}"
    desc = STAGE_DESC[key]
    return {
        "k": f"stage_{key}",
        "label": STAGE_LABEL[key],
        "desc": desc,
        "long": f"{desc} {STAGE_DERIVATION.get(key, '')}".strip(),
        "derivation": STAGE_DERIVATION.get(key, ""),
        # The checks taken at this moment — the Guide's own copy of the partition, so a
        # reader can step moment → check → back, both directions.
        "dims": list(STAGE_DIMENSIONS[key]),
        "cls": "stage",
        "measured": "composite",
        "aggregate": True,
        "stage": key,
        **nav,
    }


def stage_entries() -> List[Dict[str, object]]:
    """Every moment, in turn order — what the vocabulary endpoint serves."""
    return [e for e in (stage_entry(s) for s in STAGE_ORDER) if e]


def vocabulary_payload() -> Dict[str, object]:
    """The whole model, shaped for `/v1/assurance/vocabulary`.

    SERVED, never mirrored. Every other cross-language fact in this subsystem is a
    hand-kept JS copy pinned equal by CI — five of them — and each pin exists because the
    copy drifted. A new one would be a sixth. The frontend reads this and declares
    nothing, so the two languages cannot disagree about which stage a check belongs to.

    Carries the ORDER as a list (not a set, and not alphabetical): the sequence is the
    model's main asset, and a consumer that had to re-derive it would re-derive it wrong.
    """
    return {
        "order": list(STAGE_ORDER),
        "labels": dict(STAGE_LABEL),
        "descriptions": dict(STAGE_DESC),
        "flow_only": sorted(FLOW_ONLY_STAGES),
        # dimension → stage. The map a grouping surface actually needs; the reverse
        # (stage → dimensions) is derivable from it and would be a second copy.
        "dimensions": dict(_BY_DIM),
    }


def partition_gaps() -> Dict[str, List[str]]:
    """Registry-vs-declaration drift, both directions. The gate fails on any non-empty
    entry; an empty dict means the declaration and the live scorer suite agree exactly.

    Fails soft to a `registry_unavailable` marker rather than an empty (passing) result
    if the eval suite cannot be imported — "not known here" must never read as "clean".
    """
    try:
        from trusted_data_agent.eval import scorer as _scorer
        import trusted_data_agent.eval.scorers  # noqa: F401 — registers the suite
        registered = {d for d in (getattr(x, "dimension", None)
                                  for x in _scorer.all_scorers()) if d}
    except Exception:  # noqa: BLE001
        return {"registry_unavailable": []}
    declared = set(_BY_DIM)
    gaps: Dict[str, List[str]] = {}
    if registered - declared:
        gaps["undeclared"] = sorted(registered - declared)
    if declared - registered:
        gaps["phantom"] = sorted(declared - registered)
    dupes = [d for stage, dims in STAGE_DIMENSIONS.items() for d in dims
             if sum(d in ds for ds in STAGE_DIMENSIONS.values()) > 1]
    if dupes:
        gaps["duplicated"] = sorted(set(dupes))
    return gaps
