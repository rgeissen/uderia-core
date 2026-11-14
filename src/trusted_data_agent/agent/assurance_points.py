"""The intervention registry — every point that can alter, withhold, constrain or
attest an answer, declared once.

WHY THIS EXISTS
---------------
The Trust Journey strip's ten stages were **enumerated, not derived**: a hand-written
list in `trustJourney.js`. Six real interventions existed in code and appeared nowhere
on it — preflight SQL, the retrieval firewall, corpus quarantine, the sensitivity
ceiling, review parking, and the memory firewall. A surface that claims to show the
assurance path while silently omitting six of its gates is worse than no surface: it
teaches the reader that what they see is what ran.

So the registry is the declaration, and every surface is a **view over it** (contract
invariant V1). A gate added to the codebase without a row here fails the build before
it can fail a reader.

WHAT IS AND IS NOT A POINT
--------------------------
A point must be able to **alter, withhold, constrain or attest THIS turn's answer**.
That rule is what keeps the registry from becoming a list of everything the platform
does. It also produces one deliberate exclusion — the memory firewall, which screens
what a turn WRITES, protecting future turns rather than this one. It is recorded here
with `excluded=True` and its reason, because an omission that is written down is a
decision and an omission that is not is a defect.

READING A TURN
--------------
`read_turn()` is the single place a persisted turn becomes states. The JS strip had
its own ladder; two ladders over the same facts is the divergence class this codebase
keeps paying for (see the posture-mode ladder that omitted `review`). Deriving here
means the frontend renders readings it is given rather than re-deriving them.

Every reader is defensive: a malformed turn yields `unknown`, never an exception.
Reading a turn must never be able to break the surface that displays it.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# ── the eight Trust Circle stations — ONE registry (TRUST_CIRCLE_LEGIBILITY.md W5) ──
#
# key   the wire key — persisted in spans, payloads and Guide keys; never renamed
#       (`scored` is displayed JUDGED: TRUSTED's value is a conduct SCORE, so "Scored"
#       carried two meanings on one ring)
# label the display name (Title case; surfaces upper-case it as they see fit)
# sub   the station's CHARACTER — what makes its verdict what it is (Rule 3.3b)
# q     the QUESTION the station answers — the lead a reader needs before the
#       participle means anything
# lane  WHEN it speaks (W3): in flight (a mechanism that can change the answer) ·
#       after the turn (a measurement that grades only) · an act · a decision ·
#       the record · the aggregate — the lane cut (Rule 3b.6) carried where a
#       reader can see it, not in a paragraph
#
# Seven JS registries and four subtitle copies used to hold this table; they drifted
# (JUDGED carried two subtitles, `scored→JUDGED` was hand-written at eight sites).
# trustGrammar.STATIONS mirrors this tuple byte-for-byte (pinned by
# test_assurance_points) until the vocabulary endpoint serves it.
STATION_TABLE: Tuple[Dict[str, str], ...] = (
    {"key": "signed",   "label": "Signed",   "sub": "born, then attested",
     "q": "Is it signed, and attested here?",
     "lane": "an act · attested, not measured"},
    {"key": "grounded", "label": "Grounded", "sub": "truth · faithfulness",
     "q": "Does it stay on its corpus?",
     "lane": "in flight · can change the answer"},
    {"key": "guarded",  "label": "Guarded",  "sub": "rules · safety",
     "q": "Does it stay within the rules?",
     "lane": "in flight · can change the answer"},
    {"key": "bounded",  "label": "Bounded",  "sub": "authority, borrowed",
     "q": "Does it act only with the authority it was given?",
     "lane": "in flight · can change the answer"},
    {"key": "scored",   "label": "Judged",   "sub": "execution quality, graded",
     "q": "How did its answers grade?",
     "lane": "after the turn · grades only"},
    {"key": "trusted",  "label": "Trusted",  "sub": "the badge breathes",
     "q": "Is it trusted over time?",
     "lane": "the aggregate · one standing over the record"},
    {"key": "reviewed", "label": "Reviewed", "sub": "a human decides",
     "q": "Is a person deciding?",
     "lane": "a decision · a person in the path"},
    {"key": "proven",   "label": "Proven",   "sub": "receipts, not promises",
     "q": "Can its answers be proven?",
     "lane": "the record · sealed after delivery"},
)
STATIONS: Tuple[str, ...] = tuple(r["key"] for r in STATION_TABLE)
_STATION_ROW: Dict[str, Dict[str, str]] = {r["key"]: r for r in STATION_TABLE}


def station_row(key: str) -> Optional[Dict[str, str]]:
    """The registry row for a station key; ``None`` for an unknown key."""
    return _STATION_ROW.get(str(key or "").lower())


def station_label(key: str) -> str:
    """The upper-case display label every surface prints (``scored`` → ``JUDGED``).
    An unknown key is upper-cased so it stays visible rather than silently renamed."""
    row = station_row(key)
    return (row["label"] if row else str(key or "")).upper()

# ── reading states — the same four-word vocabulary the ring uses ───────────────
# `pass`    the point ran and found nothing
# `warn`    it acted in a way that kept the answer (annotate / redact / defuse)
# `danger`  it withheld or blocked
# `na`      it did not apply, or is not switched on — an honest absence
# `unknown` the turn does not say; NEVER shown as a pass
# `pending` is NOT a fifth flavour of absence — it means "has not reported YET",
# and folding it into `na` (switched off) is the defect Rule 9.1 was written for.
STATES = ("pass", "warn", "danger", "na", "unknown", "pending")

# ── PROP-002: ONE severity source for the derived inbox events ────────────────
# The severity a derived Assurance-Inbox item carries and the state the dial
# reads for the SAME turn stamp must come from one table: an honesty block once
# read `warning` in the inbox while this registry read the identical stamp as
# `danger` on the dial — one fact, two colours, two surfaces. The map lives
# beside the readings it must agree with; `core/trust_events.py` imports it
# (lazily, with an equal fallback literal) rather than restating it.
#
# Consistency rule, gate-asserted (test_assurance_points): a `danger` here must
# correspond to a reading this registry marks `danger` for the same stamp, and a
# `warning` to `warn`.
DERIVED_EVENT_SEVERITY = {
    "grounding_block": "danger",
    "grounding_annotate": "warning",
    "guardrail_outbound_block": "danger",
    "guardrail_outbound_redact": "warning",
    "guardrail_outbound_annotate": "warning",
    "guardrail_inbound_block": "danger",
    "guardrail_inbound_redact": "warning",
    "guardrail_inbound_annotate": "warning",
    # The dial reads an honesty BLOCK as danger (_read_honesty) — the inbox
    # carried `warning` for the same stamp until PROP-002 unified them.
    "honesty_rewrite": "danger",
    "honesty_flag": "warning",
    "session_sentinel": "danger",
    # PROP-001: a delivered answer unanimously condemned by a post-hoc judge.
    "judge_condemnation": "danger",
    # OBDA-READ E5 — a surface-only agent executed free-formed SQL: the structural
    # confinement LEAKED. Always danger: it is a hole report about the platform's
    # own enforcement, the rarest and most decision-relevant event this map holds.
    "surface_breach": "danger",
    # The OBDA policy under OBSERVE recorded a call that `enforce` would refuse (a raw
    # write, raw SQL under a contracts-only read policy, a direct graph edit, a command
    # tool). `warning`: the pre-enforcement rehearsal — nothing was refused; an operator
    # reads which agents would break before switching the plane to enforce.
    "policy_observed": "warning",
    # PROP-003 · the binding studio. A RECEIPT — it records an act the operator just
    # performed, so it is `warning` (worth a look in the history), never `danger`.
    "assurance_binding_changed": "warning",
    # A bound module that is no longer valid (uninstalled, renamed, or — for a
    # third-party pack — no longer signed and intact). `danger`: the property is still
    # being assessed by something whose provenance can no longer be established, and the
    # act that is owed (re-sign, or unbind) is the operator's.
    "assurance_module_drift": "danger",
}

# ── PROP-002b: the severity CONSTRUCT — one level, one behaviour, everywhere ──
# What a severity level MEANS is defined once, as behaviour across every surface:
# the attention word, the ring state it folds to while OPEN, the banner tone,
# whether it counts in the badge, and what it demands. These were four scattered
# conventions ("receipts never count", "danger → error tone", …) in four files;
# as a construct the level IS the behaviour. This is the grammar itself — code,
# never settings (Rule 3b.13: configure the numbers, never the semantics).
# Mirrored in static/js/handlers/trustGrammar.js; test_assurance_points pins the
# two equal.
SEVERITY_CONSTRUCT = {
    "danger": {"word": "needs you now", "state": "danger", "tone": "error",
               "badge": True, "fold": True, "demand": "ack_or_decision",
               # The FLOOR: danger always surfaces, on every station, regardless
               # of the station's reporting level — a breaker trip that can be
               # configured silent is the false green this grammar exists to
               # prevent.
               "floor": True},
    "warning": {"word": "worth a look", "state": "warn", "tone": "warning",
                "badge": True, "fold": True, "demand": "ack", "floor": False},
    "info": {"word": "for the record", "state": None, "tone": "info",
             "badge": False, "fold": False, "demand": None, "floor": False},
}

# Per-station REPORTING levels — the one legitimately configurable layer
# (operator-assigned, from the one construct): the minimum severity that
# SURFACES (badge · colour fold · default inbox view) from a station.
# Presentation, never creation: everything below the level is still recorded.
REPORTING_LEVELS = ("all", "warning", "danger")
STATION_REPORTING_DEFAULT = "warning"

_SEV_RANK = {"info": 0, "warning": 1, "danger": 2}
_LEVEL_MIN = {"all": 0, "warning": 1, "danger": 2}


def normalize_station_reporting(raw) -> Dict[str, str]:
    """A complete, validated station→level map — unknown stations/levels fall back
    to the default rather than silently widening or narrowing anything."""
    out = {s: STATION_REPORTING_DEFAULT for s in STATIONS}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out and str(v) in REPORTING_LEVELS:
                out[k] = str(v)
    return out


def severity_surfaces(severity: str, station: str, reporting: Optional[dict]) -> bool:
    """Does an item at ``severity`` SURFACE under its station's reporting level?
    Danger always (the floor). Unknown severities behave as warning — an item we
    cannot rank must not vanish."""
    if severity == "danger" or SEVERITY_CONSTRUCT.get(severity, {}).get("floor"):
        return True
    lvl = (reporting or {}).get(station, STATION_REPORTING_DEFAULT)
    return _SEV_RANK.get(severity, 1) >= _LEVEL_MIN.get(lvl, 1)


def station_of_event(event_type: str) -> str:
    """Which STATION an inbox event belongs to — the backend mirror of the
    frontend ``_stationOfEvent`` (agentPerformanceHandler.js); the two are pinned
    in agreement by test_assurance_points, the same cross-language contract as
    the STATION_DIMS mirror."""
    t = str(event_type or "").lower()
    if t.startswith("station_shortfall_"):
        st = t[len("station_shortfall_"):].split("::", 1)[0]
        return st if st in STATIONS else "scored"
    if "review" in t:
        return "reviewed"
    if "breaker" in t or "trust_behavior" in t:
        return "trusted"
    if "ground" in t or "honesty" in t or "judge_condemnation" in t:
        return "grounded"
    if "surface" in t:
        return "bounded"   # E5: a data-surface breach is an AUTHORITY event
    return "guarded"   # quarantine / defuse / ingest / injection / sentinel / exfil


@dataclass(frozen=True)
class Reading:
    """One point's state for one turn."""
    key: str
    label: str
    station: str
    state: str
    detail: str
    #: True when this point changed what the user saw on this turn.
    acted: bool = False
    #: Compact per-dimension records for the Quality Dimensions Guide, so it opens on
    #: THIS turn's dimensions rather than the full glossary. Same shape the Quality card
    #: already uses (`data-dim-context`), reused rather than re-invented — a second
    #: context format would be a second glossary in disguise.
    dims: tuple = ()
    #: A Guide-shaped entry for this mechanism ({k, long}). Present for every point,
    #: so nothing the instrument lists is a dead end.
    guide: Optional[dict] = None
    #: The modules currently BOUND to this mechanism's properties, with what they found.
    #: A mechanism could say it acted but never WHICH module produced the finding it acted
    #: on, nor what that module measured — so the ring could report "1 withheld" and stop.
    modules: tuple = ()
    #: False when the mechanism does not apply to this agent's CLASS (TRUST_CIRCLE_
    #: LEGIBILITY follow-up): it is still LISTED — dashed, dimmed, counted apart — so the
    #: dial's station and the Guide's station entry name the same mechanisms. Dropping it
    #: made GROUNDED read six mechanisms on an Ideate turn beside nine in the Guide, with
    #: nothing on either surface saying why. `classes` names where it does run (IFOC words).
    applies: bool = True
    classes: tuple = ()
    #: A short phrase overriding the state's generic attention word.
    #:
    #: `na` collapses at least six different facts — a control switched off, a control
    #: that cannot speak about ONE answer, one that had nothing to act on, one that does
    #: not apply to this agent's class. Printing "not turned on" for all of them is
    #: false for most, and it is the meaning loss this whole grammar exists to prevent.
    #: The reader knows which fact it is, so the reader supplies the word.
    word: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "station": self.station,
                "state": self.state, "detail": self.detail, "acted": self.acted,
                "word": self.word, "dims": list(self.dims),
                "guide": self.guide, "modules": list(self.modules),
                "applies": self.applies, "classes": list(self.classes)}


#: Which scored dimensions measure each mechanism.
#:
#: This is what makes a gate row's link CONTEXT-SPECIFIC: the Guide opens on the
#: dimensions that actually measure THIS mechanism, at the bands they scored on THIS
#: turn — not on the full 52-entry glossary, which answers a question nobody asked.
#:
#: Declared here, beside the mechanisms, because the registry is the only place that
#: knows what each gate is. A mechanism with no scored dimension gets an empty tuple and
#: offers no link — deliberately, since a link that opens the whole glossary is worse
#: than none. Keys are verified against `qualityDimensions.DIM` by the gate.
#:
#: THE PARTITION INVARIANT: this map is also the single source of the station
#: partition — every dimension belongs to exactly ONE station (the station of the
#: mechanism that owns it; mechanisms on one station may share, e.g. `safety` across
#: the three guarded screens). No dimension is counted by two stations: JUDGED
#: (`evaluation`) carries only the unowned RESIDUE, and TRUSTED is the single
#: aggregate that composes every station's evidence into the standing. Enforced by
#: `partition_conflicts()` + the gate; the ring mirrors it as `STATION_DIMS`.
POINT_DIMENSIONS: Dict[str, Tuple[str, ...]] = {
    "trusted_scope": ("scope_adherence", "kg_seed_utilization", "enrichment_recall"),
    # PROP-011: the mechanism that stops a turn to ask owns the measure of a turn that did NOT
    # stop where code saw an ambiguity (a raiser in `observe`, or a guess the model made past it).
    "needs_input": ("guessed_past_ambiguity",),
    # `retrieval_relevance` and `context_utilization` were removed from the retrieval
    # FIREWALL — correctly, since they measure retrieval quality rather than whether it
    # was screened — but that left them owned by nothing, so two of three grounding
    # failures reached no station and GROUNDED read quiet beside them. They belong here:
    # the grounding gate is the mechanism that judges whether the answer is supported by
    # what was retrieved, which is exactly what they measure.
    # All five are statements about whether the ANSWER is supported by what the turn
    # actually had. `numeric_fidelity` is not a judgement call: the gate itself carries a
    # `min_numeric_fidelity` threshold and acts on it, so the dimension reaching no
    # station meant the gate could act while its own measure stayed invisible.
    # `groundedness` and `scope_faithfulness` are the gate's OWN escalation judges
    # (GroundednessJudge on the corpus trigger, ScopeFaithfulnessJudge on the scope
    # trigger) — the ring's GROUNDED station has always counted them, so owning them
    # nowhere left the gate's deciding judges chip-less in the Guide and invisible to
    # the dial's GROUNDED (a failing judge raised only JUDGED: the same instrument
    # disagreement that re-homed `retrieval_relevance`/`context_utilization` here).
    "grounding_gate": ("answer_groundedness", "scope_adherence", "grounded_overlap",
                       "retrieval_relevance", "context_utilization",
                       "numeric_fidelity", "citations",
                       "groundedness", "scope_faithfulness"),
    "honesty_gate": ("honest_absence", "grain_faithfulness", "citation_fidelity",
                     "identifier_provenance"),
    "inbound_guard": ("safety",),
    # `output_sanity` measures whether internals leaked into the DELIVERED answer —
    # the outbound screen's own subject. The ring's GUARDED station has always
    # counted it; owned by nothing, it had no home on the dial and no chips in the
    # Guide (same drift class as the grounding judges above).
    "outbound_guard": ("safety", "content_safety", "output_sanity"),
    # Retrieval hygiene: `safety` is the dimension that scores whether unsafe content
    # reached the answer, which is what the firewall exists to prevent. The retrieval
    # QUALITY dimensions were listed here first and removed — they measure how good the
    # retrieval was, not whether it was screened, and a link that opens the wrong
    # dimension is worse than no link.
    "retrieval_firewall": ("safety",),
    # A10 — was ("knowledge_freshness", "source_consistency", "retrieval_performed"),
    # argued by PROXIMITY: "this mechanism sits at the retrieval seam". That is the weaker
    # criterion, and the very next entry up rejects it — the retrieval-quality dimensions
    # were listed on `retrieval_firewall` first and removed because "a link that opens the
    # wrong dimension is worse than no link". The strict rule was applied there and not
    # here.
    #
    # A label answers "which machinery explains a poor score". Quarantine EXCLUDES flagged
    # documents; a poor `knowledge_freshness` points at CDC sync, a poor
    # `retrieval_performed` at the retrieval configuration, and `source_consistency` at
    # nothing this mechanism does. Three pointers that strand the reader they exist to
    # help.
    "corpus_quarantine": (),
    # A18 — was ("tool_success_rate", "no_tool_loops"). Same criterion, same answer: this
    # enforces WHICH tools may run, while those grade HOW WELL the ones that ran behaved.
    # A failing tool points at the server; a looping agent points at the planner and
    # self-correction. Neither points here.
    "tool_enforcement": (),
    "authority": ("must_not_call", "tool_subset", "surface_violation"),
    "governed_data": ("governed_data_ratio",),
    "provenance_seal": ("provenance_integrity", "clean_termination"),
    # S2 — deliberately owns NOTHING. `cache_efficiency` (the cached share of the turn's
    # prompt tokens, a cost fact measured over THE RUN) stays in JUDGED's residue: this
    # mechanism's own reading is the CORROBORATION (the prefix the provider was asked to
    # cache is the prefix it served), and a cost share must not colour SIGNED.
    "prompt_cache": (),
    "llm_recovery": (),
    # S5 — a budget is a BOUNDARY, and its adherence is a measurement of that boundary:
    # owned here so the reading (armed · exhausted · graceful · killed) and the score
    # (1.0 / 0.5 / 0.0) are one fact at one station.
    "turn_budget": ("budget_adherence",),
    # OBDA-ACT — a signed action is a boundary the agent writes THROUGH (the grant is the list, the
    # contract the shape). It owns `action_safety` (B4d): the share of its writes whose every
    # check held — a measurement OF this boundary, so the reading (acted · parked · refused) and
    # the score are one fact at one station, as the turn budget owns `budget_adherence`.
    "actions": ("action_safety",),
    # S6 — owns nothing: the surface changes what the model SEES first, never a quality of the
    # answer; its reading is the offered/held/denied split and the named finding of a denied
    # tool the model asked for.
    "tool_surface": (),
    # S7 — owns nothing: what the loop changes is graded by every other measurement exactly as
    # any answer is; its reading is the act — revised, cleared or not — and both drafts kept.
    "outcome_loop": (),
    # S9 — owns nothing: what the platform read from an attachment is EVIDENCE the grounding
    # gate's own measurements consume; its reading is the fact that a source was read, how,
    # and whether the answer leaned on an unverified transcription.
    "attachment_sources": (),
    # S3 — deliberately owns NOTHING. `numeric_fidelity` stays with the grounding gate; what
    # this mechanism adds is the RECEIPT that measurement now counts as grounded (the gate
    # moves from flagging arithmetic to verifying it) — the lineage is evidence for the
    # same guarantee, not a second measurement of it.
    "compute_lineage": (),
    # `evaluation` is deliberately absent — but NOT for the reason this comment used to
    # give ("the union over everything scored — it takes the whole set"). That described
    # the pre-partition model, where JUDGED was the union and GROUNDED/GUARDED were
    # lenses onto it. The partition retired that: `_read_evaluation` filters the foreign
    # dimensions out, so evaluation carries the RESIDUE — the checks no mechanism owns.
    #
    # It stays out of this map because this map is what each mechanism OWNS, and the
    # residue is defined by exclusion from it: listing it here would make the map
    # self-referential and would have to be re-edited every time a scorer is added.
    # `residue_dimensions()` derives it instead, from this map and the scorer registry.
}


def _scorer_census(scope: Optional[Iterable[str]] = None) -> Optional[dict]:
    """How many checks a SCOPE holds, and how they split — counted, never stated.

    `scope` is the dimension set being described; `None` means the whole registry.
    Passing it matters: a count is a fact ABOUT something, and the platform-wide split
    printed on a station's own entry describes the platform, not the station. The
    Evaluation entry carried "42 of the 52 dimensions are deterministic code; 9 are LLM
    judges" one sentence before "only the residue is counted here" — two scopes in one
    paragraph, and the reader has no way to tell which number belongs to what they are
    looking at.

    Returns None when the eval package is unavailable or the scope is empty, so a caller
    omits the sentence rather than printing a number it cannot stand behind.
    """
    try:
        from trusted_data_agent.eval import scorer as _scorer
        import trusted_data_agent.eval.scorers  # noqa: F401 — registers the suite
        every = _scorer.all_scorers()
    except Exception:  # noqa: BLE001
        return None
    dims = {getattr(x, "dimension", None) for x in every} - {None}
    judges = {getattr(x, "dimension", None) for x in every
              if str(getattr(x, "kind", "")).split(".")[-1].lower() == "judge"} & dims
    if scope is not None:
        scope = set(scope)
        dims &= scope
        judges &= scope
    if not dims:
        return None
    return {"total": len(dims), "judges": len(judges),
            "deterministic": len(dims) - len(judges)}


def mechanism_derivation(key: str) -> str:
    """HOW a mechanism's reading is derived, as one string (the steps joined)."""
    d = MECHANISM_DERIVATION.get(key) or ""
    return " ".join(d) if isinstance(d, (tuple, list)) else d


def mechanism_how(key: str) -> List[str]:
    """HOW a mechanism decides, as steps — one line each, in the order they happen. A
    derivation declared as a string is one step."""
    d = MECHANISM_DERIVATION.get(key) or ""
    return [x for x in d] if isinstance(d, (tuple, list)) else ([d] if d else [])


def station_derivation(key: str) -> str:
    """A station's derivation prose, with any counted fact counted AT READ TIME.

    JUDGED's is prepended with a live census of the scored suite, so the stated split
    can never drift from the registry that decides it — the rule `MECHANISM_LIMITS`
    already states one declaration over: numbers are read from what decides them, never
    typed in, because a Guide stating a count the code no longer produces is worse than
    one stating none. Omitted entirely when the census is unavailable (Rule 3.2 — an
    absent fact is silence, not a stale number).

    This moved off `mechanism_derivation` with the retirement of `evaluation`: the fact
    is about the station, and it was only ever on a mechanism because a mechanism had
    been invented to stand in front of the station.
    """
    # The live census (total · deterministic · judges) rides the station's "its checks"
    # reading row (`station_reads`), counted at read time there; the derivation is the
    # folded reasoning alone, so the census is stated once.
    return STATION_DERIVATION.get(key, "")


#: THE COUNTING STATION. Every scored dimension is counted here and nowhere else.
#:
#: A station is a GUARANTEE, and the eight of them split on two different axes at once
#: until this was settled: seven describe something that RAN DURING the turn and could
#: change the answer, while measurement is something taken AFTER it that can only report.
#: Folding measurements into the seven made a station's colour blend the two — a red
#: GROUNDED meant either "a gate acted" or "a measurement is low", and no reader could
#: tell which without a drill-down.
#:
#: So the cut moved to the lane: mechanisms colour their guarantee, measurements colour
#: JUDGED. This is NOT the pre-Aug-2026 union (which counted a grounding failure at
#: GROUNDED *and* JUDGED — a double count that made the routing summary say "2 need you
#: now" for one root cause). It is exclusive, like the partition it replaces; only the
#: axis changed. See docs/roadmap/MEASUREMENT_AT_JUDGED.md §1.1 for the three-way table.
#:
#: Named rather than written `"scored"` at each site so the wire key stays in one place.
MEASUREMENT_STATION = "scored"


#: Memoised `judge_dimensions()`. `None` = not resolved yet; an EMPTY result is never
#: cached — see the function's note on why latching one would be a silent protection loss.
_JUDGE_DIMS_CACHE: Optional[frozenset] = None


def judge_dimensions() -> frozenset:
    """Scored dimensions produced by an LLM JUDGE rather than by a rule.

    Read from the scorer registry's own `kind` in ONE pass, never from a name pattern:
    the platform has judges whose dimension does not say "judge" (`groundedness`,
    `synthesis_quality`) and rules whose name suggests one. A pattern would misfile both.

    Fails soft to an empty set, which makes every dimension read as rule-measured. That
    is the conservative direction on purpose: a rule convicts and a judge does not
    (`dimension_state`), so an unavailable registry over-reports attention rather than
    silently withdrawing a conviction.

    Memoised, but **an empty result is never cached.** The scorer registry fills at import
    of `eval.scorers`, so the first call can legitimately land early; latching the empty
    set would make every judge read as a rule for the life of the process — permanently
    and invisibly re-introducing exactly the conviction the cap exists to prevent. This is
    the frontend's `ensureVocabulary` lesson (a latch that recorded the ATTEMPT rather
    than the SUCCESS turned one failed fetch into a session-long wrong answer), applied
    on the same shape of problem.
    """
    global _JUDGE_DIMS_CACHE
    if _JUDGE_DIMS_CACHE:
        return _JUDGE_DIMS_CACHE
    try:
        from trusted_data_agent.eval import scorer as _scorer
        import trusted_data_agent.eval.scorers  # noqa: F401 — registers the suite
        found = frozenset(
            d for d, kind in (
                (getattr(x, "dimension", None),
                 str(getattr(x, "kind", "")).split(".")[-1].lower())
                for x in _scorer.all_scorers())
            if d and kind == "judge")
    except Exception:  # noqa: BLE001 — an absent eval package is not a broken registry
        return frozenset()
    if found:
        _JUDGE_DIMS_CACHE = found
    return found


def _moment_keys() -> List[str]:
    """The Guide keys of the seven MOMENTS, in turn order — what `evaluation` counts,
    named at the altitude a reader can actually take in. Fails soft to `[]` (the row
    simply does not render) rather than to a half-list that would read as complete."""
    try:
        from trusted_data_agent.eval import stages as _stg
        return [f"stage_{s}" for s in _stg.STAGE_ORDER]
    except Exception:  # noqa: BLE001
        return []


def residue_dimensions() -> Tuple[str, ...]:
    """The checks no mechanism DECLARES — i.e. the ones with no guarantee label.

    Derived, never listed: every scored dimension minus every dimension some mechanism
    declares in `POINT_DIMENSIONS`. A hardcoded list would be wrong the first time a
    scorer is added.

    NOTE the narrowed meaning. This used to answer "what JUDGED counts", because JUDGED
    was the residue. JUDGED now counts EVERY scored dimension (`MEASUREMENT_STATION`), so
    this answers only "which checks carry no guarantee label" — useful for the Guide,
    never for a count. `station_dimensions()` is its complement.

    Returns the union across profile classes — a turn's actual set is narrower, because a
    class only produces the scorers that apply to it. Callers presenting this to a reader
    must say so; it answers "what can appear here", not "what did".

    Fails soft to `()` if the eval package is unavailable (the registry must stay
    importable without it), which reads as "not known here" rather than "nothing".
    """
    try:
        from trusted_data_agent.eval import scorer as _scorer
        import trusted_data_agent.eval.scorers  # noqa: F401 — registers the suite
        every = {getattr(x, "dimension", None) for x in _scorer.all_scorers()}
    except Exception:  # noqa: BLE001 — an absent eval package is not a broken registry
        return ()
    owned = {d for dims in station_dimensions().values() for d in dims}
    return tuple(sorted(d for d in every if d and d not in owned))

#: Of a mechanism's dimensions, the ones it CONSULTS IN-PATH to make its own decision —
#: not measurements taken afterwards.
#:
#: The distinction is causal and it is the one readers get wrong: everything in
#: `POINT_DIMENSIONS` was presented under a single "measured by" heading, so the two
#: judges the grounding gate actually escalates to sat in the same list as the seven
#: scorers that merely grade it. Proximity read as input, and an operator concluded the
#: measurements open and close the gates. They do not — except these, which do.
#:
#: DECLARED, never inferred: a name pattern ("…_judge", "is it a judge dimension?") would
#: catch judges that are purely post-hoc (`answer_correctness` grades, it never decides).
#: Whether a mechanism consults something in-path is a fact about that mechanism, so it
#: is stated beside it. Must be a subset of the mechanism's own `POINT_DIMENSIONS`.
POINT_JUDGES: Dict[str, Tuple[str, ...]] = {
    # `grounding/runtime.py` escalates to exactly these two; `verdict.judge_family`
    # selects which one adjudicates a given trigger (corpus → groundedness,
    # scope → scope_faithfulness).
    "grounding_gate": ("groundedness", "scope_faithfulness"),
    # `honesty/runtime.run_honesty_async` escalates the heuristic (entity-trigger)
    # grain-substitution path to this judge when the agent opts in
    # (`honestyConfig.grainJudge`); a judge PASS withdraws the verdict.
    "honesty_gate": ("grain_faithfulness",),
}


def judge_partition_conflicts() -> Tuple[str, ...]:
    """In-path judges a mechanism does not own — the declaration would be unreachable."""
    bad = []
    for mech, judges in POINT_JUDGES.items():
        owned = set(POINT_DIMENSIONS.get(mech) or ())
        bad.extend(f"{mech}:{j}" for j in judges if j not in owned)
    return tuple(sorted(bad))


#: What a mechanism IS, for the Guide.
#:
#: The rule: any item the instrument lists must be lookupable. Where a mechanism is
#: measured by scored dimensions the entry names them; where it spans several, it says
#: so rather than picking one and implying that one is the whole story; where none
#: measure it, it says that too — a gate can be real and enforced without being scored,
#: and pretending otherwise would make the link lie about what it opens.
MECHANISM_NOTE: Dict[str, str] = {
    "agent_integrity":
        "Whether the agent was signed and unchanged when this answer was produced. It is "
        "a cryptographic check, not a scored dimension — nothing measures it on a scale.",
    "compute_lineage": (
        'Where every figure in the answer came from: a computation receipt, a result row, or '
        'neither.'
    ),
    "attachment_sources": (
        "What the platform itself read from the turn's attachments, and which of them the answer "
        'rests on.'
    ),
    "outcome_loop": (
        'Whether an answer that failed the grounding check on something the model could fix was '
        'revised once before delivery and re-graded by the same check.'
    ),
    "tool_surface": (
        'Which tools the model was shown for this turn, which it could bring in by asking, and '
        'which its authority denies.'
    ),
    "actions": (
        'The signed actions the agent invoked on this turn: each a write contract on the '
        'ontology, performed only when granted, signed and intact.'
    ),
    "turn_budget": (
        'How much of one turn the agent may spend: steps, tokens, seconds. Declared on the agent '
        'and shown to the model at every tool boundary.'
    ),
    "llm_recovery": (
        'What the platform did when the model could not answer as configured: a retry, a '
        'continuation, the signed fallback or a refusal. For SIGNED it records which model '
        'answered, and whether it is signed.'
    ),
    "prompt_cache": (
        "Whether the provider served this turn's unchanged prompt start (the system prompt and "
        "the history) from its cache. A miss counts only under an explicit cache breakpoint."
    ),
    "inbound_guard":
        "Screens the incoming request for prompt injection before any model sees it.",
    "outbound_guard":
        "Screens the answer for personal data and abusive content before it is delivered.",
    "retrieval_firewall":
        "Screens retrieved documents for injected instructions and labels them as "
        "untrusted data rather than dropping them.",
    "corpus_quarantine":
        "Holds back documents an owner has quarantined, so they cannot reach an answer.",
    "trusted_scope":
        "The data scope the agent's knowledge model declares — the boundary the grounding "
        "gate then judges the answer against.",
    "preflight_sql": (
        "Blocks a query naming a database the session's list of known databases proves does not "
        'exist, before it runs.'
    ),
    "grounding_gate":
        "Checks the answer against the agent's sources or its declared scope, and withholds "
        "it or adds a note when it does not hold.",
    "honesty_gate": (
        'Catches an answer that claims an absence its failed queries cannot back, answers at a '
        'coarser grain than asked, or cites a section it never opened.'
    ),
    "authority":
        "Whose authority the turn ran under. An autonomous run acts as the agent, a subset of "
        "your permissions. An interactive turn acts as you, and names any agent limits not "
        "in force.",
    "tool_enforcement":
        "Every tool call passes one chokepoint that can deny it.",
    "sensitivity_ceiling":
        "The highest source sensitivity this agent may retrieve from. An access control, "
        "not a quality measure, so no dimension scores it.",
    "governed_data":
        "Whether data operations ran through governed metrics or were free-formed.",
    "review_parking":
        "In review mode a gate withholds the answer AND parks the original for a human. "
        "Its outcome is a human decision, so no dimension scores it.",
    "needs_input":
        "A turn that cannot go on without the person stops and asks instead of guessing: the "
        "question is a record, the turn ends input_required, and the answer re-enters as theirs.",
    "trailing_trust": (
        "The agent's standing across its recent turns: the one aggregate, each check counted "
        'once, capped by its signature. It describes the agent, not this answer.'
    ),
    "provenance_seal":
        "Whether the turn was sealed into the signed chain, so the receipt can be "
        "verified offline.",
    # Registered although it never appears on a turn's path: someone who meets the name
    # must be able to look it up, and the entry is where the exclusion is explained.
    "memory_firewall": (
        'Screens what a turn writes to memory, quarantining a write that carries an injected '
        "instruction. It protects future turns, not this one, so it is never on an answer's path."
    ),
}


#: HOW each reading is produced. The Trust Circle's `?` explainer established that a
#: figure without its derivation invites the reader to guess, and the guess is usually
#: "a model decided" — which is wrong for most of these: the great majority are
#: deterministic code, and saying so is the difference between a control you can audit
#: and one you have to trust.
MECHANISM_DERIVATION: Dict[str, Any] = {
    "compute_lineage": (
        "every number in the delivered answer is matched, at the precision it was written, against the turn's compute receipts, then against its result rows",
        'a figure counts only at three digits or more, and never one the question itself carried',
        'the receipts are sealed into the provenance chain; deterministic, no model involved',
    ),
    "attachment_sources": (
        "each upload is read once, at upload, by deterministic extractors: OCR text boxes clustered into rows and columns, a PDF's table and text layers, a CSV parser, a text reader; no model",
        'a verified table is registered as a result set the compute component reads; verified text joins the evidence the grounding gate checks against',
        'the reading counts verified, unverified and unread sources; only the optional transcription fallback costs a model call',
    ),
    "outcome_loop": (
        "the grounding gate's deterministic findings are the rubric; no prose rubric exists",
        'the first draft and a note naming each finding and its fix go to one revision call',
        'the gate re-grades the revision exactly as it graded the draft',
        "the record carries both drafts, the findings before and after, and the revision's cost; the grader is exact and free, the revision is the only LLM cost",
    ),
    "tool_surface": (
        "Agent Authority's denials are applied first",
        "the enabled catalog is split by word overlap between each tool's identity and the question plus the entities the knowledge graph named; no model, 0 tokens",
        "the tools nearest in meaning to the question widen the offered set under one rule: a floor, a share of the question's own best match, a cap",
        'finds and denials are recorded on the turn',
    ),
    "actions": (
        'every invocation is recorded on the turn with its outcome, and every confirmation a person gave afterwards',
        'the reading folds the two, so a confirmed act reads as its outcome, once',
        'a performed act is sealed; deterministic, no model involved',
    ),
    "turn_budget": (
        'steps are counted at every tool boundary; framework TDA_* tools never count',
        'tokens are counted at every LLM call, seconds against the clock',
        'the end state is stamped on the turn; the reading names the unit that ran out and how the turn ended',
        'deterministic, no model involved',
    ),
    "llm_recovery": (
        'every recovery act is recorded on the turn and sealed into the provenance chain',
        'the model that answered is compared with the two the signature names: the primary and the signed fallback',
        'either one is inside the signature; a model that is neither is the finding',
        'retries, continuations and a refusal are named in the text and never colour SIGNED',
    ),
    "prompt_cache": (
        "the start of each call the provider was asked to cache is hashed; the provider's usage says how many prompt tokens came from cache",
        "the same hash on consecutive calls, within the cache's lifetime and above the provider's minimum, with nothing served from cache, is a miss",
        "under an explicit cache breakpoint the miss is a finding: the platform asked for those bytes, so something outside them moved; under best-effort caching it is the provider's choice, reported only",
        'deterministic, no model involved',
    ),
    "agent_integrity": (
        "the agent's composition is re-hashed and compared with what was signed",
        "resolved at answer time and sealed onto the turn, so the reading replays what was true then, never today's badge",
        'a signature check: deterministic, no model involved',
    ),
    "inbound_guard": (
        'a fixed set of injection patterns is matched against the request before any model sees it; no model, 0 tokens',
        'a hit is recorded on the turn; the setting decides whether it is noted, redacted or blocked',
    ),
    "outbound_guard": (
        'personal-data patterns with checksum validation, a closed toxicity vocabulary and a URL-shape check are run over the answer; no model, 0 tokens',
        'a hit is recorded on the turn; the setting decides whether it is noted, redacted or blocked',
    ),
    "retrieval_firewall": (
        "the inbound guard's patterns are run over each retrieved chunk as it is formatted into context",
        'a hit marks the chunk untrusted in what the model sees, and is recorded on the turn',
    ),
    "corpus_quarantine":
        "A set lookup against the documents an owner marked quarantined, applied at the "
        "one retrieval seam.",
    "trusted_scope":
        "Read from the deployed identifiers in the agent's bound knowledge model — a "
        "configuration fact, stamped before the turn runs.",
    "preflight_sql": (
        "the query's FROM and JOIN targets are parsed and checked against the databases this session observed",
        'only a complete list is used, so an unknown database is provably unknown, not merely unseen',
        'no scored check measures it: the query it would have failed on never happens',
    ),
    "grounding_gate": (
        'a deterministic trigger first: numbers, quotes and identifiers in the answer that the scope cannot account for',
        'only then an LLM judge, on the flagged turns alone',
        'the judge decides; the trigger decides whether to ask it',
    ),
    "honesty_gate": (
        "deterministic: the answer's claims are checked against the turn's own query results, retrieval and read records; no model",
        'triggers 1 to 3 are observed facts; trigger 4 is a heuristic, and the agent can ask the Grain Faithfulness judge before acting on it',
        "a cited section is checked against the sections actually opened: the skill library's index is a closed vocabulary",
    ),
    "authority":
        "Read from the turn's origin — a coordinator, schedule or flow run acts as the "
        "agent principal; anything else acts as you.",
    "tool_enforcement":
        "Counted at the single chokepoint every tool call passes through, which is also "
        "where a denial happens.",
    "sensitivity_ceiling":
        "A rank comparison between each source collection's label and the agent's "
        "configured ceiling, applied before retrieval returns.",
    "governed_data":
        "Each data operation is classified by shape: one that ran through a governed "
        "metric, or one carrying raw SQL. Deterministic, 0 tokens.",
    "review_parking":
        "Present when a gate ran in review mode and withheld something; the original is "
        "stored and the state then follows the owner's decision.",
    "needs_input": (
        "present when the ledger holds a question for the user, raised in code or by the agent's one explicit act",
        "from then on every tool call is refused at the chokepoints, and the plan ends at the question",
        "the turn's record reads input_required and carries the question; the person's answer re-enters as theirs",
        "in observe the ambiguity is recorded on the turn instead — what the measure at JUDGED reads",
    ),
    # The scorer counts are NOT written here — see `_scorer_census()` and
    # `mechanism_derivation()`. They were ("42 of the 52 dimensions are deterministic
    # code; 9 are LLM judges"), and by the time anyone read it the split was 43/9 of 52:
    # the sentence had not even closed its own arithmetic (42 + 9 = 51), because a
    # scorer was added and the prose was not. The rule `MECHANISM_LIMITS` already lives
    # under — numbers are read from what decides them, never typed in — applies here too.
    "trailing_trust": (
        "a recency-weighted blend over the agent's recent turns",
        'the trust-critical checks carry their configured weight; every other check shares the rest, so none is counted twice',
        "capped by the agent's signing state",
    ),
    "provenance_seal":
        "The turn's steps are hashed into a chain and the tip is Ed25519-signed. "
        "Verifiable offline, without this instance.",
    "memory_firewall": (
        "the inbound guard's patterns are run at the one chokepoint every memory write passes",
    ),
}


#: What KIND of thing a mechanism is — the facet that determines what SETTING it takes.
#:
#: The practical test that produced these four: if two entries take the same kind of
#: action, they are the same class. You tune a threshold, you set a mode, you configure
#: a scope, you verify a signature — four settings, four classes.
#:
#:   measure      taken AFTER the answer; cannot change it. Has a band and a threshold.
#:   control      runs DURING the turn and can alter or withhold. Has a MODE.
#:   boundary     standing configuration limiting what is reachable. Has a SCOPE.
#:   attestation  a cryptographic fact. You verify it; there is no scale.
#:
#: This replaces the older `power` field as the classifier: `power` had `evaluation`,
#: `governed_data` and `trailing_trust` under `attest`, and all three are measurements.
POINT_CLASS: Dict[str, str] = {
    "agent_integrity": "attestation",
    "prompt_cache": "measure",
    "llm_recovery": "measure",
    "compute_lineage": "control",
    "turn_budget": "boundary",
    "actions": "boundary",
    "tool_surface": "boundary",
    "attachment_sources": "measure",
    "outcome_loop": "control",
    "provenance_seal": "attestation",

    "inbound_guard": "control",
    "outbound_guard": "control",
    "retrieval_firewall": "control",
    "corpus_quarantine": "control",
    "preflight_sql": "control",
    "grounding_gate": "control",
    "honesty_gate": "control",
    "review_parking": "control",
    "needs_input": "control",
    "tool_enforcement": "control",

    # Classified even though it is a deliberate exclusion from a turn's path: an
    # unclassified point is one the Guide cannot file, and the exclusion is about WHERE
    # it appears, not about what kind of thing it is.
    "memory_firewall": "control",

    "authority": "boundary",
    "trusted_scope": "boundary",
    "sensitivity_ceiling": "boundary",

    "governed_data": "measure",
    "trailing_trust": "measure",
}

#: How MANY scored dimensions measure it — the facet that determines what a CLICK gives
#: you. Independent of class: every class contains all three, which is how we know it is
#: a second facet rather than a fifth class.
#:
#:   atomic      one dimension — its band IS this item's reading
#:   composite   several — no single score stands for it; the chips are the reading
#:   unmeasured  none — the reading comes from the mechanism's own state
#:
#: `evaluation` is the maximal composite: it is the union over every applicable
#: dimension, filled from the turn rather than declared, so it is stated explicitly here
#: instead of being read as `unmeasured` — an implementation detail leaking into the
#: classification.
#: `evaluation` used to be the maximal composite here. It is retired (see
#: `measurement_status`), and no surviving mechanism needs an override.
_MEASURED_OVERRIDE: Dict[str, str] = {}


def measured_as(key: str) -> str:
    if key in _MEASURED_OVERRIDE:
        return _MEASURED_OVERRIDE[key]
    n = len(POINT_DIMENSIONS.get(key) or ())
    return "composite" if n > 1 else ("atomic" if n == 1 else "unmeasured")


def _corpus_consts() -> Dict[str, int]:
    """The corpus trigger's real constants, read from the module that uses them.

    Import-local and fail-soft: if grounding is unavailable the Guide omits the numbers
    rather than stating stale ones."""
    try:
        from trusted_data_agent.agent.grounding import corpus as _cm
        return {"floor": _cm.IDENTIFIER_FLOOR, "cap": _cm.SECOND_CHANCE_CAP,
                "quote": _cm.QUOTE_MIN_CHARS}
    except Exception:  # noqa: BLE001
        return {"floor": 0, "cap": 0, "quote": 0}


#: What each mechanism does at its edges: WHEN it acts, and WHAT an operator can change.
#:
#: The Guide already said what a mechanism IS and how its reading is DERIVED. It never
#: said when it FIRES or what you can tune — so an operator choosing a mode could not
#: tell that the grounding trigger fires on a SINGLE unsupported number, nor that its
#: sensitivity is not configurable at all. Both facts have to be read out of four source
#: files, which is not a surface.
#:
#: `fires`   — the acting condition, STRUCTURED so the Guide renders it as rows:
#:             `when`    the triggers, any one of them, each one line
#:             `then`    what happens to a hit before anything is reported
#:             `also`    a side effect worth knowing
#:             `ignores` what the check looks past, because it is not a claim
#:             `none`    the point does not fire; what it does instead
#: `tunable` — the honest control surface, as (setting, effect) pairs. For most mechanisms
#:             this is "the mode", and saying so plainly is the point.
#: `fixed`   — what a reader might expect to tune and cannot, with the reason. A negative
#:             fact under "you can change" reads as a contradiction; it gets its own row.
#: `why`     — the design's reasoning, folded behind the facts on the Guide.
#:
#: Every sentence states ONE fact and stays short (gate: test_assurance_points §R counts
#: the words). The reasoning goes under `why`, never into the reading line.
#:
#: NUMBERS ARE DERIVED FROM THE CONSTANTS, never typed in. A Guide stating a threshold
#: the code no longer uses is worse than one stating none — that is the defect class
#: this whole registry exists to prevent, and it recurs the moment a value is copied.
_C = _corpus_consts()
MECHANISM_LIMITS: Dict[str, Dict[str, Any]] = {
    "grounding_gate": {
        "fires": {
            "when": ["one unsupported number",
                     f"a quoted run of {_C['quote']}+ characters the sources do not carry",
                     "one spelled-out acronym the sources never spell out that way",
                     f"{_C['floor']}+ distinct unsupported identifiers"],
            "then": f"up to {_C['cap']} flagged pieces of the answer are searched for again in the "
                    f"sources; only what is still missing reaches the judge",
            "ignores": "the answer's own structure, which is not a claim: headings, bolded labels, "
                       "section and list numbers, a table's header row and bolded row labels",
        },
        "tunable": [("mode", "per family: knowledge-graph scope · knowledge repositories"),
                    ("minNumericFidelity", "shifts the numeric concern; annotates, never withholds")],
        "fixed": [("trigger sensitivity", "not configurable"),
                  ("quality bands", "colour a chip; they never loosen the gate")],
        "why": "A single miss is enough for the first three triggers: the trigger is tuned for "
               "recall, and the second-chance retrieval absorbs the cost of that.",
    },
    "inbound_guard": {
        "fires": {"when": ["the request text matches a prompt-injection pattern"]},
        "tunable": [("mode", "what happens on a hit: watch, disclose, enforce"),
                    ("injection", "on or off")],
        "fixed": [("the patterns", "not configurable")],
    },
    "outbound_guard": {
        "fires": {"when": ["the answer carries personal data", "toxic content",
                           "a novel URL shaped to carry data out"]},
        "tunable": [("mode", "what happens on a hit: watch, disclose, enforce"),
                    ("pii · toxicity · exfil", "each detector on or off")],
        "fixed": [("the detectors", "not configurable")],
    },
    "retrieval_firewall": {
        "fires": {"when": ["a retrieved chunk matches an injection pattern"],
                  "then": "the chunk is kept and labelled untrusted rather than dropped"},
        "tunable": [("guardrails master switch", "platform-wide")],
        "why": "Removing the chunk silently would hide the fact.",
    },
    "memory_firewall": {
        "fires": {"when": ["a memory being written matches an injection pattern"],
                  "then": "it is stored quarantined"},
        "tunable": [("guardrails master switch", "platform-wide")],
        "why": "A planted instruction must not survive into a later session.",
    },
    "honesty_gate": {
        "fires": {"when": ["the answer asserts something does not exist while every query that "
                           "would have shown it failed",
                           "retrieval found almost nothing yet the answer states specifics",
                           "a data product's refused grain was silently answered at a coarser one",
                           "a ranking question names an entity but none of its queried dimensions",
                           "the answer cites a section of its skill library it never opened"],
                  "then": "for a grain substitution the disclosure is appended; the figure is never replaced"},
        "tunable": [("mode", "what happens on a hit: watch, disclose, enforce"),
                    ("honestyConfig.grainJudge", "opt-in: the grain_faithfulness judge confirms the "
                                                 "heuristic grain trigger before it acts")],
    },
    "authority": {
        "fires": {"when": ["a turn no person started attempts a write", "a denied tool",
                           "a connector it may not use", "a run past its cost cap"]},
        "tunable": [("authorityConfig", "allow-autonomous, deny-writes, denied tools, allowed "
                                        "connectors, per-run budget")],
        "fixed": [("enforcement", "fails closed; it cannot be switched off for a turn no person started")],
    },
    "sensitivity_ceiling": {
        "fires": {"when": ["a retrieved document is classified above the agent's ceiling"],
                  "then": "it is withheld from retrieval"},
        "tunable": [("knowledgeConfig.maxSensitivity", "the ceiling; absent means none")],
    },
    "corpus_quarantine": {
        "fires": {"when": ["a document was quarantined by an operator or by the ingest screen"],
                  "then": "it is excluded from retrieval until released"},
        "fixed": [("quarantine and release", "per-document acts, not a threshold")],
    },
    "trusted_scope": {
        "fires": {"none": "it acts on nothing. It reports the data scope the agent's bound "
                          "knowledge model declares, which the grounding gate measures against"},
        "tunable": [("the knowledge model", "which one the agent is bound to; a configuration "
                                            "fact stamped before the turn runs")],
    },
    "preflight_sql": {
        "fires": {"when": ["a SQL call names a database absent from the session's list of known databases"]},
        "tunable": [("APP_CONFIG.PREFLIGHT_SQL_MODE", "deployment-wide: observe or enforce")],
    },
    "tool_enforcement": {
        "fires": {"none": "reported, not acted. It reads how the turn's tool calls went and shows "
                          "a denial when Agent Authority refused one"},
        "fixed": [("nothing here", "the acting is Agent Authority's")],
    },
    "provenance_seal": {
        "fires": {"none": "it always runs. Each step is added to a chain that cannot be edited, and "
                          "the turn is signed with the deployment's key"},
        "fixed": [("sealing", "unconditional; that is what makes it evidence")],
    },
    "agent_integrity": {
        "fires": {"none": "never automatically. It reports whether this agent still matches what "
                          "was signed off; changing the agent changes the reading"},
        "fixed": [("sign-off and attestation", "deliberate acts, not settings")],
    },
    "compute_lineage": {
        "fires": {"when": ["a figure in the answer matches no compute receipt and no result row"],
                  "then": "the disclosure names it; the answer is appended to, never rewritten",
                  "also": "a figure with a receipt is marked with its lineage"},
        "tunable": [("compute-lineage master switch", "platform-wide"),
                    ("honesty mode", "whether the note is appended (disclose, enforce) "
                                     "or only recorded (watch)")],
    },
    "attachment_sources": {
        "fires": {"when": ["a turn carries attachments"],
                  "then": "each is reported as verified (read by the platform), unverified "
                          "(transcribed by the model) or unread (no extractor could read it)",
                  "also": "an unverified transcription the answer rests on is the finding"},
        "tunable": [("attachment-sources master switch", "platform-wide"),
                    ("attachmentConfig.transcribeImages", "per agent: the unverified vision "
                                                          "fallback, off by default"),
                    ("OCR · PDF engines", "which are installed")],
    },
    "outcome_loop": {
        "fires": {"when": ["the grounding gate found something the model can fix and did not block"],
                  "then": "the draft is revised once (at most twice) with the finding named, "
                          "re-graded, and delivered only if it cleared; else the annotated first draft"},
        "tunable": [("outcome-loop master switch", "platform-wide"),
                    ("outcomeConfig", "per agent: enabled, maxRevisions (0–2), revisableKinds")],
    },
    "tool_surface": {
        "fires": {"when": ["every turn of a governed-surface agent"],
                  "then": "the tools the question cannot need are held back, findable but never "
                          "lost; a tool authority denies is withheld from the surface and the search",
                  "also": "a denied tool the model asked for is named"},
        "tunable": [("toolSurface.mode", "per agent: all enabled tools, or relevant per turn (governed)")],
        "fixed": [("semantic rule", "floor 0.18 · 80% of the question's best match · at most 5 additions"),
                  ("Tool Finder", "offered exactly when the surface is governed; absent → nothing is held")],
    },
    "actions": {
        "fires": {"when": ["the agent invokes a signed action"],
                  "then": "on an interactive turn it is prepared (the write is run and always rolled "
                          "back) and waits for a person's Confirm; a turn no person started performs, "
                          "parks or only records, as the contract's setting says",
                  "also": "an action not granted, not signed or refused by a check writes nothing "
                          "and is named"},
        "tunable": [("OBDA-MODEL · OBDA-ACT master switch", "platform-wide"),
                    ("authorityConfig.actions.types", "per agent: the grant is the list"),
                    ("setting per action", "watch · review · disclose · enforce")],
    },
    "turn_budget": {
        "fires": {"when": ["a unit of the declared budget is used up"],
                  "then": "the next tool call is refused, naming what is left, and the model "
                          "answers with what it has; on the planning lane the phases the budget "
                          "cannot afford are skipped and the report says what remains",
                  "also": "near the limit (75%) the model is warned"},
        "tunable": [("turn-budget master switch", "platform-wide"),
                    ("turnBudget {steps, tokens, seconds}", "per agent; each unit optional")],
        "fixed": [("a raised ceiling", "changes the agent's signed setup, so it shows on its signature")],
        "why": 'The hard stops stay the last line, one unit above; a turn they kill is recorded as killed, never as graceful.',
    },
    "llm_recovery": {
        "fires": {"when": ["a provider error a retry can change",
                           "a completion cut off at the output limit",
                           "the primary model exhausted, or its breaker open after repeated failures",
                           "a provider refusal"],
                  "then": "retried after the wait the provider asks for; continued, at most twice; "
                          "served by the signed fallback model and only that one; an honest answer "
                          "naming the category"},
        "tunable": [("resilience master switch", "platform-wide"),
                    ("llmFallbackConfigurationId", "per agent: the signed fallback"),
                    ("retry count · base delay", "how often and how long to wait")],
        "why": 'The fallback is part of the signed deployment, so a turn it served is still inside the signature, and says which model answered. A recovery keeps the turn alive; it is not a guarantee of its own, so only who answered bears on SIGNED.',
    },
    "prompt_cache": {
        "fires": {"none": "it reports what the provider served from cache and whether an unchanged "
                          "prefix missed it"},
        "tunable": [("prompt-caching master switch", "platform-wide"),
                    ("cache lifetime", "per agent: 5 minutes or 1 hour, or an opt-out")],
    },
    "review_parking": {
        "fires": {"when": ["a gate acted while set to keep withheld originals"],
                  "then": "the original is parked as owed human work instead of being lost"},
        "tunable": [("retention", "the keep-for-review option on any gate's enforce setting")],
    },
    "needs_input": {
        "fires": {"when": ["a name matches two or more of the user's things",
                           "the model asks by its one act (TDA_AskUser, plan_type input_required, ASK_USER:, the uderia-ask block)",
                           "a data source elicits, a child agent asks, a user connector is not connected"],
                  "then": "the turn ends input_required with the question; every further tool is refused; the answer re-enters as the person's",
                  "ignores": "an ambiguity code cannot see — the model's judgement, measured only where it guessed past a code-detected one"},
        "tunable": [("inputPolicy.raisers", "enforce stops the turn; observe records the ambiguity and lets the step fail honestly"),
                    ("inputPolicy.interactive", "never closes a question by policy and completes the turn with an honest sentence"),
                    ("inputPolicy.maxPerTurn", "the question budget of a turn")],
    },
    "governed_data": {
        "fires": {"none": "it reports the split between governed, deterministic data operations "
                          "and free-formed ones"},
        "fixed": [("nothing", "it measures what the turn did")],
    },
    "trailing_trust": {
        "fires": {"when": ["a sustained at-risk standing, through the circuit breaker"],
                  "then": "it can notify, or freeze the agent's autonomy"},
        "tunable": [("behavioral-trust settings", "the weights and the window"),
                    ("breaker mode", "off · notify · freeze")],
    },
}


def limits_text(key: str) -> List[str]:
    """Every sentence a point's limits carry, flat — for the prose gates."""
    lim = MECHANISM_LIMITS.get(key) or {}
    out: List[str] = []
    f = lim.get("fires") or {}
    out.extend(f.get("when") or [])
    for k in ("then", "also", "ignores", "none"):
        if f.get(k):
            out.append(f[k])
    for k in ("tunable", "fixed"):
        for pair in lim.get(k) or []:
            out.extend(pair)
    if lim.get("why"):
        out.append(lim["why"])
    return out


#: HOW a station READS — the fact the station's description never carried, as DATA:
#: phrases, not sentences. Three blocks, each a surface (the sub-label under the lead)
#: and a few (label, phrase) rows:
#:   answer  the Live Status dial, the instrument under one answer
#:   agent   the Trust Circle, the ring that reads an agent over its recent turns —
#:           its two readings (configuration: could it act? · execution: what did it do?),
#:           the rule for several agents, the rule for an open finding
#:           (agentPerformanceHandler `_attention` / `_assemble`; test_assurance_points §S)
#:   checks  where this station's checks count
#: The member kinds and counts are not written here: the Guide derives them from the
#: station's own rows at render time, so they can never drift.
_SCALE = "green · amber · red · not set · unknown · not yet reported"
_SEVERAL = ("several agents", "the weakest decides")
_OPEN = ("open finding", "amber or red until decided · Assurance Inbox")
_NO_COLOUR = "as text · never a colour"
_CHECKS_AT_JUDGED = "{n} · colour JUDGED, not this station · under graded by"
STATION_READS: Dict[str, Dict[str, Any]] = {
    "signed": {
        "answer": {"where": "Live Status dial", "rows": [("reading", "worst of its members"), ("scale", _SCALE)]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "signed and unchanged → green · changed since → amber · "
                                                  "signed by its publisher, not confirmed here → blue · never signed → not set"),
            ("execution · what did it do?", "none · signing is an act, not a measurement"),
            _SEVERAL, _OPEN]},
        "checks": "none · a signature is verified, not scored",
    },
    "grounded": {
        "answer": {"where": "Live Status dial", "rows": [("reading", "worst of its members"), ("scale", _SCALE)]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "the grounding gate’s setting · enforce → green · "
                                                  "watch → hollow green, watching · off → not set"),
            ("execution · what did it do?", "answers withheld, checks cleared · " + _NO_COLOUR),
            _SEVERAL, _OPEN]},
        "checks": _CHECKS_AT_JUDGED,
    },
    "guarded": {
        "answer": {"where": "Live Status dial", "rows": [("reading", "worst of its members"), ("scale", _SCALE)]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "the outbound guardrail’s setting · enforce → green · "
                                                  "watch → hollow green, watching · off, or master switch off → not set"),
            ("execution · what did it do?", "refused, redacted, withheld · " + _NO_COLOUR),
            _SEVERAL, _OPEN]},
        "checks": _CHECKS_AT_JUDGED,
    },
    "bounded": {
        "answer": {"where": "Live Status dial", "rows": [("reading", "worst of its members"), ("scale", _SCALE)]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "authority limits something (a denied tool, a connector allow-list, "
                                                  "a cost cap) → green · limits nothing → not bounded"),
            ("execution · what did it do?", "denials and refusals · " + _NO_COLOUR),
            _SEVERAL, _OPEN]},
        "checks": _CHECKS_AT_JUDGED,
    },
    "scored": {
        "answer": {"where": "Live Status dial", "rows": [("reading", "worst grade of its measures"), ("scale", "green · amber · red")]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "live scoring on, with the pass rates for green and red · off → not set"),
            ("execution · what did it do?", "worst grade of its measures, per agent"),
            _SEVERAL,
            ("a judge alone in red", "holds amber · judges stay advisory until people have checked enough verdicts"),
            _OPEN]},
        "checks": "{census} · grouped by the moment of the turn they measure",
    },
    "trusted": {
        "answer": {"where": "Live Status dial", "rows": [("reading", "none · the score reads an agent, not one answer")]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "score on · breaker: notify, or freeze an agent whose score falls"),
            ("execution · what did it do?", "the score · one number over recent turns · the measures that matter most "
                                                "for trust weigh more · a broken signature caps it"),
            _SEVERAL, _OPEN]},
        "checks": "all · each counted once",
    },
    "reviewed": {
        # two members since PROP-011: human review (parked answers) and needs input (a turn that
        # stopped to ask) — the station reads the worst of them, like every multi-member guarantee
        "answer": {"where": "Live Status dial", "rows": [("reading", "worst of its members"), ("scale", _SCALE)]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "any gate set to review → can park an answer for a person; "
                                                  "the raisers on enforce → can stop to ask the person"),
            ("execution · what did it do?", "waiting for you: parked answers, questions asked + findings nobody acknowledged"),
            _SEVERAL, _OPEN]},
        "checks": _CHECKS_AT_JUDGED,
    },
    "proven": {
        "answer": {"where": "Live Status dial", "rows": [("reading", "sealed, or not")]},
        "agent": {"where": "Trust Circle · recent turns", "rows": [
            ("configuration · could it act?", "sealing on → records are produced"),
            ("execution · what did it do?", "turns sealed · a count, not a pass rate"),
            _SEVERAL, _OPEN]},
        "checks": _CHECKS_AT_JUDGED,
    },
}



#: TERMS — every technical word a Guide row may use, defined once in the reader's words
#: and served with the vocabulary ("Terms" in the Guide's glossary). The rule the rows
#: live under: a term is used on screen only if the screen explains it. A word not in
#: plain language and not in this table is a defect (gate: test_assurance_points §T).
GUIDE_TERMS: Tuple[Tuple[str, str], ...] = (
    ("setting", "What a gate does on a hit: watch (record only) · watch and disclose (record and add a "
                "note) · enforce (withhold or replace) · enforce and keep for review (withhold and park "
                "the original for a person). Off means the gate does not run."),
    ("judge", "A measure an LLM panel returns, sampled. A gate may ask one before deciding; the rest grade afterwards."),
    ("grade", "What a measure reports against its thresholds: green, amber or red."),
    ("chunk", "A passage of a source document, the unit retrieval brings back."),
    ("corpus", "The documents an agent may draw on: its knowledge repositories."),
    ("grain", "The level of detail a figure is at, such as per day or per customer."),
    ("data product", "A signed, governed view of data an agent queries through a contract."),
    ("contract", "A signed description of one data product or one action: what it reads or writes, and how."),
    ("Agent Authority", "The one chokepoint every tool call passes, which can deny it."),
    ("master switch", "A platform-wide switch that turns a feature off for every agent."),
    ("window", "The recent turns the ring reads for an agent."),
    ("Assurance Inbox", "The list of findings waiting for a person's decision."),
    ("signature", "The record that an agent was signed off, and whether it has changed since."),
    ("sealed", "Recorded in a signed chain that cannot be edited, so it can be verified later."),
    ("receipt", "A record of what a computation did, kept so a figure can be traced."),
    ("disclosure", "A note appended to the answer, naming what the gate found."),
    ("parked", "Withheld and kept for a person to decide."),
    ("connector", "A platform tool the agent reaches another system through, such as mail or files."),
    ("quarantine", "A document held back from retrieval until an owner releases it."),
    ("breaker", "A stop that opens after repeated failures, so a failing provider or a falling score is acted on."),
    ("cache", "The provider keeping the unchanged start of a prompt, so it is not processed again."),
    ("lineage", "Where a figure came from: a computation receipt, a result row, or neither."),
    ("personal data", "Names, addresses and identifiers of people."),
    ("fallback", "The second, signed model served when the primary cannot answer."),
)


def guide_terms() -> List[Dict[str, str]]:
    """The Terms glossary, in the order it is read."""
    return [{"term": t, "def": d} for t, d in GUIDE_TERMS]


#: Words that belong to the code and never to a row. The rows speak the reader's words;
#: these are the registry's (state tokens, fold names, crypto names, scheduler names).
ROW_BANNED = re.compile(
    r"na|warn|pending|danger|pass(?! rate| line)|worst-of|aggregated?|posture|"
    r"lane|partition|residue|wire|trajectory|recency-weighted|composite|"
    r"Ed25519|calibration|breaker-open|retry-after|backoff|exfiltration|autonomous|"
    r"annotate|observe|verdict set|authoritative", re.I)


def station_reads(key: str) -> Dict[str, Any]:
    """The three reading blocks of a station, with the counts filled from the partition."""
    src = STATION_READS.get(key)
    if not src:
        return {}
    n = len(station_dimensions().get(key) or ())
    census = _scorer_census() or {}
    checks = src["checks"].format(
        n=n,
        census=(f"{census.get('total', 0)} · {census.get('deterministic', 0)} deterministic · "
                f"{census.get('judges', 0)} judges, sampled" if census else ""))
    return {
        "answer": {"where": src["answer"]["where"], "rows": [list(r) for r in src["answer"]["rows"]]},
        "agent": {"where": src["agent"]["where"], "rows": [list(r) for r in src["agent"]["rows"]]},
        "checks": checks,
    }


def reads_text(key: str) -> List[str]:
    """Every phrase a station's reading blocks carry, flat — for the prose gates."""
    r = station_reads(key)
    out: List[str] = []
    for blk in ("answer", "agent"):
        b = r.get(blk) or {}
        out.append(b.get("where", ""))
        for lab, val in b.get("rows") or []:
            out.extend([lab, val])
    out.append(r.get("checks", ""))
    return [t for t in out if t]


def limits_of(key: str) -> Dict[str, Any]:
    """The structured `fires` / `tunable` / `fixed` / `why` block for a mechanism, or empty."""
    return MECHANISM_LIMITS.get(key) or {}


#: WHAT a mechanism can do to THIS answer — the reader's question the Guide could not
#: answer in one glance ("which of these withhold, and which only classify?").
#:
#: `power` is the registry's coarse verb (alter · withhold · constrain · attest) and it
#: drives whether an omission is a safety matter. These are the concrete FATES a
#: mechanism can cause at its strongest setting, in the reader's words:
#:
#:   stopped   the answer is withheld (kept for a person when the gate is set to review)
#:   changed   the answer is revised, redacted or replaced before delivery
#:   noted     a disclosure is appended; the answer itself is untouched
#:   refused   a step the agent wanted — a query, a tool call, a document, an action —
#:             is refused; the answer goes on with less
#:
#: An EMPTY tuple is a declaration too: the point reports or attests and cannot touch
#: the answer. Every registered point must appear here (gate: test_assurance_points),
#: and the tuple must agree with `power` — a `withhold` point names `stopped` or
#: `refused`, an `attest` point names nothing, a `constrain` point names only `refused`,
#: an `alter` point names `changed` and/or `noted` and never `stopped`.
FATES: Tuple[str, ...] = ("stopped", "changed", "noted", "refused")
MECHANISM_FATES: Dict[str, Tuple[str, ...]] = {
    "agent_integrity": (),
    "prompt_cache": (),
    "llm_recovery": ("changed",),
    "actions": ("refused",),
    "turn_budget": ("refused",),
    "attachment_sources": (),
    "outcome_loop": ("changed",),
    "tool_surface": ("refused",),
    "inbound_guard": ("stopped", "changed", "noted"),
    "authority": ("refused",),
    "trusted_scope": (),
    "preflight_sql": ("refused",),
    "tool_enforcement": ("refused",),
    "corpus_quarantine": ("refused",),
    "sensitivity_ceiling": ("refused",),
    "retrieval_firewall": ("changed",),
    "outbound_guard": ("stopped", "changed", "noted"),
    "grounding_gate": ("stopped", "noted"),
    "honesty_gate": ("changed", "noted"),
    "compute_lineage": ("noted",),
    "governed_data": (),
    "review_parking": ("stopped",),
    "needs_input": ("stopped",),
    "trailing_trust": (),
    "provenance_seal": (),
    "memory_firewall": ("refused",),
}


def fates_of(key: str) -> Tuple[str, ...]:
    """The fates a mechanism can cause, or an empty tuple (reports / attests only)."""
    return tuple(MECHANISM_FATES.get(key) or ())


#: WHY a mechanism has no measurement — declared ONLY where it cannot be derived.
#:
#: The Guide shipped ONE sentence for every ungraded mechanism: *"No scored dimension
#: measures this one — it is a yes/no control."* It was wrong for three of the six
#: (`agent_integrity` is an attestation, `sensitivity_ceiling` a boundary, `trailing_trust`
#: a measure), and it presented every absence as CORRECT — concealing that three genuine
#: controls run and are never graded at all (symmetry map Part B).
#:
#: Two readings, and the difference is the whole point: `by_design` means nothing should
#: grade this, `gap` means nothing does yet. A reader who cannot tell them apart cannot
#: tell a finished design from an unfinished one.
UNGRADED_REASON = {
    "agent_integrity": ("by_design",
                        "Signing is an ACT, not a per-turn assessment. The integrity state "
                        "is a fact about the agent, not a grade of a turn."),
    "compute_lineage": ("by_design",
                        "Its receipts are what `numeric_fidelity` (owned by the grounding gate) "
                        "now counts as grounded — the lineage is evidence for that measurement, "
                        "not a second one beside it."),
    "attachment_sources": ("by_design",
                           "What was read from an attachment is EVIDENCE — the grounding gate's own "
                           "measurements (numeric fidelity, corpus support) grade the answer against "
                           "it; this station records the read and its verification, not a score."),
    "outcome_loop": ("by_design",
                     "The revision it delivers is graded by every measurement exactly as any answer "
                     "is; what this station adds is that a revision HAPPENED, whether it cleared, "
                     "and that both drafts are on record — an act, not a score."),
    "tool_surface": ("by_design",
                     "What the model was SHOWN is not a quality of the answer it gave: the tools "
                     "it used are graded by every other measurement exactly as before; what this "
                     "station adds is the offered/held/denied split and the denied tool it asked "
                     "for, which no score can carry."),
    "llm_recovery": ("by_design",
                     "A recovery is an act, not a quality: the answer a retry or a fallback "
                     "delivered is graded by every other measurement exactly as any answer is; "
                     "what this station adds is WHO served it, which no score can carry."),
    "prompt_cache": ("by_design",
                     "Its reading IS the corroboration — served from cache, or (under an "
                     "explicit breakpoint) an unchanged prefix that missed. The cached SHARE "
                     "of the turn's prompt tokens is a "
                     "cost fact measured over the whole run (`cache_efficiency`, counted at "
                     "JUDGED), and a cost share must not colour SIGNED."),
    "review_parking": ("by_design",
                       "Parking is an act. The queue's own counts are the reading — a "
                       "measurement would restate them."),
    "trailing_trust": ("by_design",
                       "This is a MEASUREMENT wearing a mechanism's name: a "
                       "trailing-window composite, so nothing acted during the turn for a "
                       "measurement to grade. Recorded as a known labelling defect."),
    "corpus_quarantine": ("by_design",
                          "Its honest measurement would be the fraction of retrieved "
                          "chunks that came from quarantined documents — 0 by "
                          "construction, since quarantine removes them BEFORE retrieval. "
                          "A structural zero is not a measurement."),
    "tool_enforcement": ("gap",
                         "Its honest measurement is the DENIAL count — how often it "
                         "refused a call — which is not aggregated per station today."),
    "preflight_sql": ("gap",
                      "A counterpart is nearly free — the fraction of SQL calls whose "
                      "databases were in the authoritative catalog, which the guard "
                      "already computes per call."),
    "sensitivity_ceiling": ("gap",
                            "A counterpart would be the fraction of retrieved chunks "
                            "withheld by the ceiling. Low value until ceilings are widely "
                            "configured, but it is an absence, not a design."),
    "memory_firewall": ("gap",
                        "A quarantined memory write is recorded and never graded. The "
                        "detector's own precision is measured against a curated corpus "
                        "(the resilience self-test), which is a reading about the "
                        "DETECTOR, not about this turn."),
}


#: Which assessable PROPERTIES each mechanism decides on.
#:
#: The missing link. `POINT_DIMENSIONS` says which measurements a mechanism is ABOUT;
#: nothing said which properties it actually ASSESSES — so a reader could see that a gate
#: acted but never which module produced the finding it acted on, or what that module
#: found. The Trust Circle could say "1 withheld" and stop there.
#:
#: Declared, not inferred: a mechanism can decide on more than one property (the grounding
#: gate runs a scope family and a corpus family), and no naming convention expresses that.
#: A mechanism absent here simply lists no modules.
POINT_PROPERTIES = {
    "grounding_gate": ("answer_support", "answer_scope", "context_use"),
    "trusted_scope": ("answer_scope",),
    "honesty_gate": ("absence_claim", "retrieval_sufficiency", "citation_provenance",
                     "identifier_provenance"),
    "outbound_guard": ("output_sensitivity",),
    "governed_data": ("data_governance",),
    "authority": ("tool_authority",),
    "tool_enforcement": ("tool_repetition",),
    "inbound_guard": ("request_integrity",),
}


#: Which scored measures a WINDOW aggregate weights — mechanism → the key of the list in
#: `eval/trust_behavior.DEFAULTS` that names them.
#:
#: Neither relation above fits trailing trust. It has no module (`POINT_PROPERTIES`): it
#: is a blend over the record, not an assessment of this turn, and an M chip would claim a
#: shared computation that does not exist. And the measures are not graded ABOUT it
#: (`POINT_DIMENSIONS`): they feed it. Its "how it reads" row said the trust-critical
#: checks carry the configured weight and never said WHICH — the one fact the entry
#: withheld. This is that list, read from the place that decides it so the Guide cannot
#: drift from the computation (the architecture doc listed five of the six). The
#: remainder — "every other check shares the rest" — is JUDGED's members, one click away,
#: and is not repeated here.
POINT_WEIGHTS: Dict[str, str] = {
    "trailing_trust": "trust_dims",
}


def point_weights(point_key: str) -> Tuple[str, ...]:
    """The measures this mechanism weights, in the order the computation lists them."""
    src = POINT_WEIGHTS.get(point_key)
    if not src:
        return ()
    try:
        from trusted_data_agent.eval.trust_behavior import DEFAULTS as _tb
        return tuple(str(d) for d in (_tb.get(src) or ()))
    except Exception:  # noqa: BLE001 — the Guide degrades, it never errors
        return ()


def consulted_properties(point_key: str) -> tuple:
    """The properties this mechanism CONSULTS during the turn — the subset of its
    `POINT_PROPERTIES` that `INFLIGHT_CONSUMERS` assigns to it (the one declaration,
    asserted against the real call sites by `test_assurance_substrate`). A property can be
    one mechanism's and consulted by another: `answer_scope` belongs to Trusted scope too,
    but only the grounding gate consults it in flight. Read by the dial's mark
    (`active_modules`) and the Guide's fold (`consults`), so the two cannot disagree."""
    try:
        from trusted_data_agent.agent.assurance import INFLIGHT_CONSUMERS as _IC
    except Exception:  # noqa: BLE001 — the reading degrades, it never errors
        return ()
    return tuple(p for p in POINT_PROPERTIES.get(point_key, ()) if _IC.get(p) == point_key)


def active_modules(point_key: str, turn: Any) -> list:
    """The modules currently BOUND to this mechanism's properties, with what they found.

    Re-assessed from the persisted turn rather than read from a stamp, and that is safe
    precisely because every assessor is pure, deterministic and replayable — the same
    properties `conformance` requires for the post-hoc lane. It is also the point: the
    reader sees what the CURRENTLY BOUND module says, so after a remap the panel reflects
    the new module rather than a fossil of the old one's output.

    Fails soft to an empty list: a panel that loses its expansion is worse than one that
    never had it, but far better than a turn that fails to render.
    """
    props = POINT_PROPERTIES.get(point_key) or ()
    if not props or not isinstance(turn, dict):
        return []
    out = []
    try:
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
        from trusted_data_agent.agent.assurance import properties as _props
        from trusted_data_agent.eval.types import RunRecord

        _A.bootstrap()
        ctx = _rc.from_run(RunRecord(turn))
    except Exception:  # noqa: BLE001
        return []

    for prop in props:
        try:
            a = _reg.resolve(prop, _Lane.POST_HOC) or _reg.resolve(prop, _Lane.IN_FLIGHT)
            pdef = _props.get(prop)
            if a is None:
                # A property with no bound assessor is NOT silently omitted: "nothing is
                # assessing this" is the reading that must never be invisible.
                out.append({"property": prop,
                            "label": pdef.label if pdef else prop,
                            "assessor": None, "assessor_label": None,
                            "summary": "no module is bound to this property",
                            "metrics": {}, "abstained": True})
                continue
            f = a.run(ctx)
            decides = prop in consulted_properties(point_key)
            out.append({
                "property": prop,
                "label": pdef.label if pdef else prop,
                "assessor": a.key,
                "assessor_label": a.label or a.key,
                # The module's relation to the gate — a MARK on the row, never a sentence.
                "decides": decides,
                "summary": f.abstained or f.summary,
                # The VALUES — what the module actually found, not merely that it ran.
                "metrics": {k: round(float(v), 4) for k, v in (f.metrics or {}).items()},
                "triggered": bool(f.triggered),
                "abstained": bool(f.abstained),
            })
        except Exception:  # noqa: BLE001 — one bad module must not lose the others
            continue
    return out


def grading_state(key: str) -> dict:
    """How this mechanism is graded, DERIVED where possible.

    `graded` is derived from the registry — a mechanism with owned dimensions is graded by
    them, and no declaration can contradict that. Only the ABSENCE carries a declared
    reason, because "why is nothing grading this" is not derivable from anything.

    A mechanism that is ungraded and undeclared reads `gap` with no reason rather than
    inheriting a comforting default: an unexplained absence must look unexplained.
    """
    dims = POINT_DIMENSIONS.get(key) or ()
    if dims:
        return {"state": "graded", "dimensions": list(dims), "reason": ""}
    kind, why = UNGRADED_REASON.get(key, ("gap", ""))
    return {"state": kind, "dimensions": [], "reason": why}


def guide_entry(key: str) -> Optional[dict]:
    """A Guide-shaped entry for one mechanism: what it is, and what measures it.

    Returned for EVERY registered point, so nothing the instrument lists is a dead end.
    """
    note = MECHANISM_NOTE.get(key)
    if not note:
        return None
    dims = POINT_DIMENSIONS.get(key) or ()
    # The dimension NAMES are carried in `dims` and rendered as clickable chips, not
    # listed in the sentence: a reader looking at a mechanism should be able to step into
    # a dimension, read it, and step back. Naming them in prose makes them a dead end.
    if len(dims) > 1:
        measured = "It spans several scored dimensions rather than one, so no single score stands for it."
    elif len(dims) == 1:
        measured = "One scored dimension measures it."
    else:
        # The note already explains WHY nothing scores it, and restating that here just
        # says the same thing twice — the "no slot restates another" rule, one level down.
        measured = ""
    # The description is the NOTE alone. The measured sentence is the `measured` facet (a
    # tag on the entry) and the derivation is its own row (`derivation`); carrying both
    # inside the description made one slot restate two others, three sentences long.
    # (`measured` above is kept for the facet's own logic; it no longer enters the text.)
    return {"k": key, "label": None, "dims": list(dims),
            # WHY nothing grades this, when nothing does. The Guide shipped one sentence
            # for all six ungraded mechanisms, wrong for three of them and presenting every
            # absence as correct. `by_design` and `gap` are different answers and a reader
            # must be able to tell them apart.
            "grading": grading_state(key),
            # The subset this mechanism CONSULTS IN-PATH, so the Guide can separate what
            # DECIDES from what GRADES. Under one heading they read as one kind of thing
            # and the causal direction is lost.
            "judges": list(POINT_JUDGES.get(key) or ()),
            # The PROPERTIES this mechanism acts on, as their Guide keys — the one
            # declaration (`POINT_PROPERTIES`), served on the parent like every child list
            # (a station's `controls`, a mechanism's `dims`, JUDGED's `counts`). A
            # property's "assessed for" row is this list inverted on the client; no entry
            # stores its parents, so the two directions cannot disagree.
            "properties": [f"property_{p}" for p in POINT_PROPERTIES.get(key, ())],
            # The subset it CONSULTS during the turn (`consulted_properties`): the Guide folds
            # their in-flight modules under "modules during the turn".
            "consults": [f"property_{p}" for p in consulted_properties(key)],
            # The measures a window aggregate WEIGHTS (`POINT_WEIGHTS`) — a third relation,
            # because the other two say the wrong thing: `dims` would say these grade it
            # (they feed it), `properties` would say a module assesses it (none does).
            # Served as a child list like the rest, read backwards by the client.
            "weights": list(point_weights(key)),
            # What this mechanism COUNTS. Distinct from `dims` (what grades it) because
            # the direction is opposite: `evaluation` is not graded by these — it is the
            # layer that produces them ("Never — it grades, it never acts"). Labelling
            # them "graded by" would invert the causality the escalates-to/graded-by
            # split exists to keep.
            #
            # It counts EVERY scored dimension now, not the residue it held under the
            # retired partition — so this row names the seven MOMENTS rather than listing
            # 52 chips. That is not a summary standing in for the truth: each moment is a
            # real Guide entry carrying its own checks, so the full set is one click away
            # and organised the way the reader already sees it everywhere else. A 52-chip
            # wall would be complete and unreadable, which is the same as incomplete.
            # WHEN it acts and WHAT you can change — the two facts an operator choosing a
            # mode needs, and the two the Guide never carried.
            # Served STRUCTURED: `fires` {when, then, also, ignores, none}, `tunable` and
            # `fixed` as [setting, effect] pairs, `why` as the folded reasoning — the Guide
            # renders each as rows, never as a paragraph.
            **({"fires": _lim["fires"]} if (_lim := limits_of(key)).get("fires") else {}),
            **({"tunable": [list(p) for p in _lim["tunable"]]} if _lim.get("tunable") else {}),
            **({"fixed": [list(p) for p in _lim["fixed"]]} if _lim.get("fixed") else {}),
            **({"why": _lim["why"]} if _lim.get("why") else {}),
            # WHAT it can do to the answer, in the reader's words (MECHANISM_FATES). Served
            # even when empty: an empty list is the declaration "reports only", and a
            # missing key would read as "not known" — a different, weaker claim.
            "fates": list(fates_of(key)),
            # The note alone is the SHORT form (what the hover shows); the long form adds
            # what measures it and how it is derived.
            "desc": note,
            "long": note,
            "derivation": mechanism_derivation(key),
            "how": mechanism_how(key),
            "cls": POINT_CLASS.get(key, "control"),
            "measured": measured_as(key),
            # The station this mechanism sits at — the SAME registry fact the vocabulary
            # endpoint serves. Both payloads are projections of one registry, and this
            # one omitted it, so a mechanism the dial registered first (every mechanism
            # on the current turn) was known station-less and its dimensions fell to the
            # JUDGED residue. An incomplete projection of a single source makes the
            # answer depend on which projection a consumer happened to see first.
            "station": _BY_KEY[key].station if key in _BY_KEY else None,
            # `evaluation` is the one mechanism whose READING IS its dimensions — it is
            # the union over every applicable one. Clicking it should show them, not a
            # single row restating its own name. Every other mechanism HAS dimensions;
            # this one IS them, and that difference has to be declared rather than
            # guessed from the count.
            "aggregate": key == "evaluation"}


#: What each STATION guarantees. A station is neither a measure nor a control — it is
#: the GUARANTEE those mechanisms exist to uphold, which is why it needs its own class
#: rather than being filed as an oversized mechanism.
#:
#: Every card that reports a mechanism names its station, and the station is lookupable
#: like everything else, so a reader can move: card → mechanism → station → the other
#: mechanisms that uphold the same guarantee.
STATION_NOTE: Dict[str, str] = {
    "signed": "That this agent is the one that was signed off — its ontology, corpus, "
              "skills and posture unchanged since someone attested them.",
    "grounded": "That the answer stays inside what the agent actually has: its declared "
                "data scope and the knowledge it retrieved. Not that the answer is "
                "right — that it is not invented.",
    "guarded": "That nothing unsafe crossed either edge: no injected instruction reached "
               "the model, and no personal data or abuse reached you.",
    "bounded": "That the agent acted within limits set for it — whose authority it "
               "carried, which tools it could reach, which sources it could read.",
    "scored": (
        'That the answer was graded: every check ran and is on the record whether or not you '
        'look. Every scored check is counted here, and nothing else; nothing runs here.'
    ),
    "trusted": (
        "That the agent has a track record, and this answer sits inside it: every station's "
        "evidence composed into one standing over its recent turns, each check counted once."
    ),
    "reviewed": "That where a gate withheld something, a human owns the decision — and "
                "the original was kept, not discarded.",
    "proven": "That what happened can be verified afterwards by someone who does not "
              "trust this instance: the turn is sealed into a signed chain.",
}

#: How each station's own reading is produced. Stated because a station is a COMPOSITE,
#: and a composite without its rule invites the reader to guess the weighting.
#: How each station's reading is derived. NOT one shared sentence any more: the lane cut
#: made the seven guarantee stations and JUDGED derive their state from DIFFERENT things,
#: and a single string saying "worst-of its mechanisms AND the dimensions they name"
#: described neither — it was the fold, written once and inherited by all eight.
_GUARANTEE_DERIVATION = (
    "Its colour is the worst of its members: what upholds, bounds, attests or evidences it. "
    "The measures graded about it are counted at JUDGED and colour JUDGED, never this "
    "station, so this colour means one thing: how its own members went."
)
_JUDGED_DERIVATION = (
    "Nothing runs here. Its members are the measures: every scored measure is counted here "
    "once, taken after the answer, and can only report. Deterministic scoring "
    "runs on every turn for free; the judge panel runs only when a check already failed, "
    "the run looks anomalous, or sampling selects it. You can change the scoring switch, "
    "the judge sample rate and the green and red lines; the lines change what is reported, "
    "they cannot open or close a gate."
)
STATION_DERIVATION: Dict[str, str] = {
    k: (_JUDGED_DERIVATION if k == MEASUREMENT_STATION else _GUARANTEE_DERIVATION)
    for k in STATIONS
}


def station_entry(key: str) -> Optional[dict]:
    """A Guide-shaped entry for a STATION, so a card can link to what it guarantees."""
    note = STATION_NOTE.get(key)
    if not note:
        return None
    # EVERY member the registry declares, the excluded ones too. An excluded point is a
    # recorded decision, not an absence: `memory_firewall` declares GUARDED, and listing
    # only the on-path members left its own entry saying "upholds GUARDED" with nothing on
    # GUARDED saying it back — an entry with no parent. The off-path members are named
    # `excluded` in the vocabulary, so the Guide marks them and the dial, which reads a
    # turn, still lists only what is on one.
    mechs = [p.key for p in points(include_excluded=True) if p.station == key]
    # Display labels where they differ from the wire key. `scored` reads JUDGED:
    # TRUSTED's value is a conduct SCORE, so "Scored" carried two meanings on one
    # ring; this station renders verdicts. The wire key never changes — spans,
    # payloads and Guide keys are persisted.
    _row = station_row(key) or {}
    _label = station_label(key)
    _deriv = station_derivation(key)
    return {"k": f"station_{key}", "label": _label, "desc": note,
            # The registry row rides along (W5): the question a station answers and its
            # character line, so the Guide and the ring lead with the same words.
            "sub": _row.get("sub", ""), "q": _row.get("q", ""), "lane": _row.get("lane", ""),
            # The description is the note alone; the derivation is the folded `why`, and
            # HOW the station reads — on a turn, on the ring, where its checks count — is
            # three rows of its own (`reads`), the fact the description never carried.
            "long": note,
            "derivation": _deriv,
            "why": _deriv,
            "reads": station_reads(key),
            # A station holds TWO populations and the Guide must show both, exactly as
            # the dial's station panel does ("what ran" / "what it measures"):
            #   controls — the mechanisms that UPHOLD the guarantee (in-path, can act)
            #   dims     — the measurements that GRADE it (after the fact, report only)
            # Listing the mechanisms under "graded by" said a control grades a guarantee,
            # which inverts the one distinction this whole vocabulary exists to keep —
            # and left the station's real measurements off its own entry entirely.
            # `station_dimensions()` is THE partition, so the Guide counts a dimension at
            # the same station the ring and the scorecard do.
            "controls": mechs,
            "dims": list(station_dimensions().get(key, ())),
            # JUDGED counts every scored dimension, so it names the seven MOMENTS rather
            # than listing 52 chips: each moment is a real Guide entry carrying its own
            # checks, so the full set is one click away and organised the way the reader
            # sees it everywhere else. This row moved here from the retired `evaluation`
            # mechanism — the fact belongs to the station, not to a fiction standing in
            # front of it.
            **({"counts": _moment_keys()} if key == MEASUREMENT_STATION else {}),
            "cls": "station", "measured": "composite",
            "aggregate": True, "station": key}


#: The IFOC words for the internal class keys (CLAUDE.md: never show a raw type string).
#: `conversation_with_tools` is the Ideate class WITH tools — named so, or a row would say a
#: mechanism "runs for Ideate" to the very Ideate agent it was excluded from.
IFOC_LABEL: Dict[str, str] = {
    "llm_only": "Ideate", "conversation_with_tools": "Ideate + tools", "rag_focused": "Focus",
    "tool_enabled": "Optimize", "genie": "Coordinate",
}


@dataclass(frozen=True)
class AssurancePoint:
    key: str
    label: str
    #: Which of the eight stations this mechanism is evidence for.
    station: str
    #: What it can do to an answer — drives whether an omission is a safety matter.
    power: str                      # "alter" | "withhold" | "constrain" | "attest"
    reader: Callable[[dict, str], Reading]
    #: Empty = every profile class.
    applies_to: frozenset = frozenset()
    #: Where its stamp lands on the persisted turn (documentation + the P1 audit).
    turn_key: Optional[str] = None
    #: A point that exists but deliberately does NOT belong on this turn's path.
    excluded: bool = False
    exclusion_reason: str = ""

    def applies(self, profile_class: str) -> bool:
        return not self.applies_to or profile_class in self.applies_to


# ── helpers ───────────────────────────────────────────────────────────────────
def _d(turn: Any) -> dict:
    return turn if isinstance(turn, dict) else {}


def _stamp(turn: Any, key: str) -> dict:
    v = _d(turn).get(key)
    return v if isinstance(v, dict) else {}


def _mk(point_key: str, label: str, station: str, state: str, detail: str,
        acted: bool = False, word: str = "", dims: tuple = ()) -> Reading:
    return Reading(key=point_key, label=label, station=station, state=state,
                   detail=detail, acted=acted, dims=dims, word=word)


def _guard_reading(key: str, label: str, ev: dict) -> Tuple[str, str, bool]:
    """The guardrail state ladder, in ONE place for both directions.

    Reads the P1 stamp: `ran=False` distinguishes a switched-off guard from one that
    ran clean, which is precisely what the strip could not say before.
    """
    if not ev:
        # No stamp = the guard did not run this turn (the master switch is off, or the
        # per-agent path was never reached). That is structurally absent (na), NOT a
        # recording gap (unknown) — the guard's OWN error is stamped as `action="error"`
        # below and reads unknown from THERE. Same rule `_read_integrity` documents:
        # an absent stamp reads na, never unknown, or every gates-off turn (the default)
        # buries its whole station in "not recorded".
        return "na", "did not run on this turn", False
    if ev.get("ran") is False:
        return "na", ev.get("reason") or "not enabled for this agent", False
    action = ev.get("action")
    cats = ", ".join(ev.get("categories") or []) or None
    if action == "error":
        return "unknown", "the guard failed — not recorded as a pass", False
    if action == "block":
        return "danger", f"blocked ({cats or 'violation'})", True
    if action == "redact":
        return "warn", f"personal data masked ({cats or 'pii'})", True
    if action == "annotate":
        return "warn", f"caught {cats or 'findings'} — annotated", True
    if cats or ev.get("findings"):
        # Found something and left the answer as it was: an OBSERVE-mode catch. It used to
        # fall through to "screened — clean" (trust campaign 4, S3: an email and a phone
        # reached the answer, the stamp said "2 PII spans detected (observe)", the dial said
        # clean) while the answer capsule already read it as a catch. Same rule as preflight's
        # observe finding: warn, named, NOT acted (nothing was changed). Labels only (I9).
        return "warn", f"caught {cats or 'findings'} — recorded, not altered (observe mode)", False
    return "pass", "screened — clean", False


# ── readers ───────────────────────────────────────────────────────────────────
def _read_inbound(turn, _cls):
    st, detail, acted = _guard_reading("inbound", "Inbound guard",
                                       _stamp(turn, "guardrail_inbound"))
    return _mk("inbound_guard", "Inbound guard", "guarded", st,
               f"Inbound guard: {detail}", acted)


def _read_outbound(turn, _cls):
    st, detail, acted = _guard_reading("outbound", "Outbound guard",
                                       _stamp(turn, "guardrail"))
    return _mk("outbound_guard", "Outbound guard", "guarded", st,
               f"Outbound guard: {detail}", acted)


def _read_authority(turn, _cls):
    """BOUNDED — whose authority the turn ran under.

    Reads the SEALED `authority` stamp (`agent_authority.build_authority_stamp`, written
    at execution time) rather than re-deriving. The stamp's `applied` flag is the fact:
    it distinguishes "the boundary held" from "the boundary was never in force", which no
    re-derivation can recover.

    This used to read `turn["origin"]` — a key `execution_service` puts on the returned
    payload and on the user MESSAGE, but which no engine writes onto the turn. So every
    autonomous turn reported "acting as the user" (0 of 1540 production turns carried
    either key). `origin` survives as an any-era fallback for turns persisted before the
    stamp landed; absent both, the interactive reading is the honest one.
    """
    t = _d(turn)
    stamp = t.get("authority")
    if isinstance(stamp, dict) and "applied" in stamp:
        origin = str(stamp.get("origin") or "")
        if not stamp.get("applied"):
            # Limits the agent HAS but this turn ran without (no `enforce_interactive`) are
            # named, never left to read as though none were configured (trust campaign B3).
            inert = stamp.get("not_in_force")
            limits = describe_scope_safe(inert) if isinstance(inert, dict) else []
            if limits:
                return _mk("authority", "Authority", "bounded", "na",
                           "Authority: interactive turn — acting as the user. This agent's own "
                           "limits were NOT in force on this turn: " + " · ".join(limits)
                           + ". They apply to its autonomous runs; Agent Authority → "
                           "Apply to interactive turns too extends them to turns a person starts.",
                           word="agent limits not in force")
            return _mk("authority", "Authority", "bounded", "na",
                       "Authority: interactive turn — acting as the user",
                       word="you are the authority")
        scope = [c for c in describe_scope_safe(stamp)]
        from trusted_data_agent.core.agent_authority import AUTONOMOUS_ORIGINS
        if origin in AUTONOMOUS_ORIGINS:
            detail = (f"Authority: autonomous run ({origin}) — "
                      f"acting as the agent principal, a subset of the owner")
        else:
            # The principal applied to a turn a person started (`enforce_interactive`, or the
            # deployment's OBDA policy): the boundary held, but the run was not autonomous —
            # an empty origin once read "autonomous run (scheduler)" (trust campaign, B3).
            detail = ("Authority: interactive turn under the agent principal — "
                      "the agent's authority applies to turns a person starts too")
        if scope:
            detail += "\n" + " · ".join(scope)
        return _mk("authority", "Authority", "bounded", "pass", detail)

    origin = t.get("origin") or ""
    autonomous = origin in ("flow", "genie_parent", "genie_child") or t.get("source") == "scheduler"
    if not autonomous:
        return _mk("authority", "Authority", "bounded", "na",
                   "Authority: interactive turn — acting as the user", word="you are the authority")
    return _mk("authority", "Authority", "bounded", "pass",
               f"Authority: autonomous run ({origin or 'scheduler'}) — "
               f"acting as the agent principal, a subset of the owner")


def describe_scope_safe(stamp: dict) -> list:
    """The stamp's constraints in the ONE vocabulary `agent_authority.describe_scope` owns.

    Never raises and never invents wording of its own — a second description of a scope
    is a second definition of it.
    """
    try:
        from trusted_data_agent.core import agent_authority as _aid
        return list(_aid.describe_scope(_aid.scope_from_stamp(stamp)) or [])
    except Exception:  # noqa: BLE001 — a reading must never raise
        return []


def _read_tool_enforcement(turn, _cls):
    t = _d(turn)
    calls = []
    for k in ("execution_trace", "turn_action_history", "conversation_agent_events"):
        v = t.get(k)
        if isinstance(v, list) and v:
            calls = v
            break
    denied = False
    try:
        import json as _json
        denied = "agent_denied" in _json.dumps(calls, default=str)
    except Exception:  # noqa: BLE001 — a reading must never raise
        denied = False
    if denied:
        return _mk("tool_enforcement", "Tool enforcement", "bounded", "warn",
                   "Tool enforcement: at least one call was DENIED by Agent Authority", True)
    if not calls:
        return _mk("tool_enforcement", "Tool enforcement", "bounded", "na",
                   "Tool enforcement: no tool calls this turn", word="no tool calls")
    return _mk("tool_enforcement", "Tool enforcement", "bounded", "pass",
               f"Tool enforcement: {len(calls)} call(s) executed under the chokepoint")


def _read_preflight(turn, _cls):
    """Preflight SQL — blocks a query naming a database the session's catalog proves
    does not exist. Invisible on every surface until now."""
    ev = _stamp(turn, "preflight")
    if not ev:
        # Absent = the preflight guard did not run this turn (no SQL query needed a catalog
        # check, or the guard is not enabled) — structurally absent (na), never unknown.
        return _mk("preflight_sql", "Preflight SQL", "grounded", "na",
                   "Preflight SQL: did not run on this turn — no SQL query needed checking, "
                   "or the guard is not enabled", word="did not run")
    if ev.get("ran") is False:
        return _mk("preflight_sql", "Preflight SQL", "grounded", "na",
                   f"Preflight SQL: {ev.get('reason') or 'not enabled'}")
    # WHAT was wrong — an unknown database, or a column the table does not have (the
    # column check, Sep 2026); the reading names it rather than a generic "unknown"
    _dbs = ev.get("unknown_dbs") or []
    _cols = ev.get("missing_columns") or []
    _vals = ev.get("unverified_values") or []
    _decs = ev.get("decimal_divisions") or []
    # Every kind of finding names itself: a value or decimal finding once read "referenced an
    # unknown database or column", which is neither.
    _parts = ([f"unknown database(s) {', '.join(_dbs)}"] if _dbs else []) + \
             ([f"non-existent column(s) {', '.join(_cols)}"] if _cols else []) + \
             ([f"filter value(s) nothing established {', '.join(_vals[:3])}"] if _vals else []) + \
             ([f"a decimal division truncated to a literal's scale ({', '.join(_decs[:2])})"] if _decs else [])
    _what = "; ".join(_parts) or "an unknown database or column"
    blocked = int(ev.get("blocked_count") or 0)
    if blocked:
        return _mk("preflight_sql", "Preflight SQL", "grounded", "danger",
                   f"Preflight SQL: {blocked} call(s) blocked before execution — {_what}",
                   True)
    flagged = int(ev.get("flagged_count") or 0)
    if flagged:
        _noted = (ev.get("mode") == "enforce")
        return _mk("preflight_sql", "Preflight SQL", "grounded", "warn",
                   f"Preflight SQL: {flagged} call(s) — {_what} "
                   + ("(noted on the result — not blocked)" if _noted else "(observe mode — not blocked)"))
    return _mk("preflight_sql", "Preflight SQL", "grounded", "pass",
               "Preflight SQL: every query named a database the catalog knows")


def _read_scope(turn, _cls):
    gr = _stamp(turn, "grounding")
    dbs = gr.get("scope_databases") or []
    if not dbs and not _d(turn).get("kg_enrichment_event"):
        return _mk("trusted_scope", "Trusted scope", "grounded", "na",
                   "Trusted scope: no knowledge model bound to this turn", word="no model bound")
    shown = ", ".join(list(dbs)[:4]) + ("…" if len(dbs) > 4 else "")
    return _mk("trusted_scope", "Trusted scope", "grounded", "pass",
               f"Trusted scope stamped{': ' + shown if shown else ''}")


def _grounding_finding(ev: dict) -> str:
    """The finding the gate acted on, from its own stamp, in as few words as it takes: the
    family (unmatched statements · unmatched figures · out-of-scope data) and the count. It
    used to read "out-of-scope references" for EVERY annotate — on a Focus agent, whose
    finding is always a corpus one (prod 510f7011, 2026-10-30). Mirrored in trustJourney.js."""
    parts = []
    spans = ev.get("unsupported_claims") or []
    if spans:
        n, m = len(spans), ev.get("claims_checked")
        parts.append(f"{n} of {m} statements unmatched" if m else f"{n} unmatched statement{'s' if n != 1 else ''}")
    nums = ev.get("ungrounded_numbers") or []
    if nums:
        parts.append(f"{len(nums)} figure{'s' if len(nums) != 1 else ''} unmatched")
    srcs = sorted(set(ev.get("out_of_scope_dbs") or []) | set(ev.get("answer_oos_refs") or []))
    if srcs:
        parts.append("out of scope: " + ", ".join(srcs[:3]) + (" …" if len(srcs) > 3 else ""))
    return " · ".join(parts) or ("drift confirmed" if ev.get("decision") == "block" else "flagged")


def _read_grounding(turn, _cls):
    ev = _stamp(turn, "grounding")
    if not ev:
        # Absent = the grounding gate did not run this turn (the gate is not enabled —
        # GROUNDING_GATE_ENABLED defaults off). Structurally absent (na), never unknown; the
        # gate's own error is stamped as action="error" below and reads unknown from there.
        return _mk("grounding_gate", "Grounding gate", "grounded", "na",
                   "Grounding gate: did not run on this turn — the gate is not enabled",
                   word="did not run")
    if "decision" not in ev:
        # A P1 audit stamp: the gate did not produce a verdict.
        if ev.get("action") == "na":
            return _mk("grounding_gate", "Grounding gate", "grounded", "na",
                       "Grounding gate: nothing in this answer to ground")
        if ev.get("action") == "error":
            return _mk("grounding_gate", "Grounding gate", "grounded", "unknown",
                       "Grounding gate: failed — not recorded as a pass")
        return _mk("grounding_gate", "Grounding gate", "grounded", "na",
                   f"Grounding gate: {ev.get('reason') or 'not enabled'}")
    decision = ev.get("decision")
    oos = ev.get("out_of_scope_dbs") or []
    suffix = f"\nout of scope: {', '.join(list(oos)[:3])}" if oos else ""
    if decision == "block":
        return _mk("grounding_gate", "Grounding gate", "grounded", "danger",
                   f"Grounding gate: withheld — {_grounding_finding(ev)}", True)
    if decision == "annotate":
        return _mk("grounding_gate", "Grounding gate", "grounded", "warn",
                   f"Grounding gate: {_grounding_finding(ev)} — annotated", True)
    # A pass reads by the family that ran: a knowledge-only agent has no declared scope,
    # so "stays inside the declared scope" described a check that never happened. Spans
    # the trigger recorded but did not act on (below its threshold, or observe) are named
    # with that reason — never presented as though nothing was found.
    unsupported = ev.get("unsupported_claims") or []
    if ev.get("ground") == "corpus":
        if unsupported:
            why = ("observe mode" if str(ev.get("reason", "")).startswith("observe")
                   else "below the trigger threshold")
            return _mk("grounding_gate", "Grounding gate", "grounded", "pass",
                       f"Grounding gate: {len(unsupported)} unsupported claim{'s' if len(unsupported) != 1 else ''} "
                       f"recorded, not acted on ({why})")
        return _mk("grounding_gate", "Grounding gate", "grounded", "pass",
                   "Grounding gate: the answer is supported by the retrieved knowledge")
    return _mk("grounding_gate", "Grounding gate", "grounded", "pass",
               "Grounding gate: answer stays inside the declared scope" + suffix)


def _read_honesty(turn, _cls):
    """The honesty mechanism's reading — WORST-OF its branches, driver named.

    The gate stamps its populations under two keys (`honesty` for the SQL/RAG families,
    `honesty_citation` for cited-but-unread), because a branch IS a population and
    collapsing them would report a library turn as though the SQL branch had cleared it.
    A mechanism, though, is ONE row on the ring — so the row folds worst-of and says
    which branch drove it.

    Folding is not optional. Reading only the SQL stamp is exactly how this row came to
    say "no unsupported absence claim" with a green dot while its own module list said
    "1 of 3 found something" directly beneath — one mechanism, two answers, and a quiet
    station above both.
    """
    readings = [r for r in (_read_honesty_sql(turn), _read_honesty_citation(turn))
                if r is not None]
    if not readings:
        return _mk("honesty_gate", "Honesty", "grounded", "na",
                   "Honesty check: did not run on this turn", word="did not run")
    if len(readings) == 1:
        return readings[0]
    worst = worst_state(readings)
    driver = next(r for r in readings if r.state == worst)
    others = [r for r in readings if r is not driver and r.state != "na"]
    detail = driver.detail
    if others and worst in ("warn", "danger", "unknown"):
        # Name the quiet half too: a reader seeing only the finding cannot tell whether
        # the rest of the mechanism was checked or merely absent.
        detail += " · " + "; ".join(o.detail.split(": ", 1)[-1] for o in others)
    return _mk("honesty_gate", "Honesty", "grounded", worst, detail,
               acted=any(r.acted for r in readings), word=driver.word)


def _read_honesty_citation(turn, _cls=None):
    """The cited-but-unread branch (F-001). ``None`` when the branch never stamped —
    every agent without a progressive skill library, i.e. the baseline."""
    ev = _stamp(turn, "honesty_citation")
    if not ev:
        return None
    if ev.get("ran") is False:
        return _mk("honesty_gate", "Honesty", "grounded", "na",
                   f"Citation check: {ev.get('reason') or 'not enabled'}")
    if ev.get("action") == "error":
        return _mk("honesty_gate", "Honesty", "grounded", "unknown",
                   "Citation check: failed — not recorded as a pass")
    if not ev.get("acted"):
        return _mk("honesty_gate", "Honesty", "grounded", "pass",
                   "Citation check: every library section the answer cites was opened "
                   "this turn")
    if ev.get("kind") == "fabricated_identifier":
        bad = ev.get("fabricated") or []
        named = ", ".join(str(b) for b in bad[:3]) or "an identifier"
        one = len(bad) == 1
        verb = ("a disclosure was appended to the answer"
                if ev.get("disclosure_appended") else "flagged")
        return _mk("honesty_gate", "Honesty", "grounded", "warn",
                   f"Citation check: the answer presents {named} as established but "
                   f"{'it does' if one else 'they do'} not exist in the skill's "
                   f"library — {verb}", True)
    n_un = int(ev.get("unread_count") or 0)
    named = ", ".join(str(a) for a in (ev.get("unread") or [])[:3]) or "a cited section"
    verb = (f"a disclosure naming {'it' if n_un == 1 else 'them'} was appended to the "
            f"answer" if ev.get("disclosure_appended") else "flagged")
    return _mk("honesty_gate", "Honesty", "grounded", "warn",
               f"Citation check: the answer cites {named} as consulted but never opened "
               f"{'it' if n_un == 1 else 'them'} — {verb}", True)


def _read_honesty_sql(turn, _cls=None):
    ev = _stamp(turn, "honesty")
    if not ev:
        # Absent = this branch did not stamp (not a data-running engine, or not enabled).
        # The FOLD decides what that means: with NO branch at all the mechanism reads
        # "did not run"; with the other branch present this one simply contributes
        # nothing, rather than dragging a real finding down to `na`.
        return None
    if ev.get("ran") is False:
        return _mk("honesty_gate", "Honesty", "grounded", "na",
                   f"Honesty check: {ev.get('reason') or 'not enabled'}")
    if ev.get("action") == "error":
        return _mk("honesty_gate", "Honesty", "grounded", "unknown",
                   "Honesty check: failed — not recorded as a pass")
    decision = ev.get("decision")
    if ev.get("kind") == "grain_substitution":
        # The append act, in its own words — "false negative"/"rewritten" would
        # misdescribe a turn whose every query SUCCEEDED and whose figure stands.
        if decision == "disclosed":
            return _mk("honesty_gate", "Honesty", "grounded", "warn",
                       "Honesty check: the answer used a coarser grain than the "
                       "question asked — a disclosure naming the substitution was "
                       "appended to it", True)
        return _mk("honesty_gate", "Honesty", "grounded", "warn",
                   f"Honesty check: silent grain substitution flagged — "
                   f"{ev.get('reason') or 'the answer addresses a coarser question'}",
                   True)
    if ev.get("judge_cleared"):
        return _mk("honesty_gate", "Honesty", "grounded", "pass",
                   "Honesty check: the grain heuristic fired and the "
                   "grain_faithfulness judge ruled the answer faithful — "
                   "verdict withdrawn")
    if ev.get("kind") == "claimed_effect_after_denial":
        if decision == "block":
            return _mk("honesty_gate", "Honesty", "grounded", "danger",
                       "Honesty check: the answer reported a write the agent's authority had DENIED this turn — "
                       "replaced by an honest statement", True)
        return _mk("honesty_gate", "Honesty", "grounded", "warn",
                   "Honesty check: the answer reported a completed write that was DENIED this turn — "
                   + ("the disclosure was appended" if ev.get("disclosure_appended") else "flagged, answer unchanged"),
                   bool(ev.get("acted")))
    if ev.get("kind") == "unsourced_record":
        n = int(ev.get("unsupported_count") or 0)
        failed = int(ev.get("failed_query_count") or 0)
        # "no query behind it" covers both facts the gate reads: none ran, or every one failed
        behind = (f"with every data query failed ({failed})" if failed else "with no query behind it")
        if decision == "block":
            return _mk("honesty_gate", "Honesty", "grounded", "danger",
                       f"Honesty check: the answer presented a record {behind} "
                       f"({n} value(s) in nothing the agent was given) — replaced by an honest statement", True)
        return _mk("honesty_gate", "Honesty", "grounded", "warn",
                   f"Honesty check: a record presented {behind} — {n} value(s) appear in "
                   f"nothing the agent was given" + (" (disclosed in the answer)" if decision == "disclosed" else ""), True)
    if decision == "block":
        return _mk("honesty_gate", "Honesty", "grounded", "danger",
                   "Honesty check: a confident non-existence claim rested on failed "
                   "queries — the answer was rewritten to say so", True)
    if decision:
        return _mk("honesty_gate", "Honesty", "grounded", "warn",
                   f"Honesty check: flagged — {ev.get('reason') or 'assertion not supported'}",
                   True)
    return _mk("honesty_gate", "Honesty", "grounded", "pass",
               "Honesty check: no unsupported absence claim")


def _read_quarantine(turn, _cls):
    """Corpus quarantine — a document an owner quarantined is excluded from retrieval.
    Reversible, audited, and until now invisible on the answer's path."""
    ev = _stamp(turn, "retrieval_hygiene")
    if not ev:
        # No `retrieval_hygiene` stamp means the retrieval-screening path did not run this
        # turn — either guardrails are off or the turn did no knowledge retrieval. That is
        # structurally absent (`na`), NOT a recording gap (`unknown`). The stamp IS the
        # record of screening running, so its absence can never be "should have recorded and
        # didn't". `unknown` (rank 3) outranks `pass`, so emitting it here dragged the whole
        # GROUNDED station to "not recorded" on any SQL-only tool_enabled turn.
        return _mk("corpus_quarantine", "Corpus quarantine", "grounded", "na",
                   "Corpus quarantine: no knowledge was retrieved on this turn",
                   word="no knowledge retrieved")
    n = int(ev.get("quarantined_excluded") or 0)
    if n:
        return _mk("corpus_quarantine", "Corpus quarantine", "grounded", "warn",
                   f"Corpus quarantine: {n} quarantined document(s) held back from "
                   f"this answer", True)
    return _mk("corpus_quarantine", "Corpus quarantine", "grounded", "pass",
               "Corpus quarantine: nothing retrieved was under quarantine")


def _read_sensitivity(turn, _cls):
    """The agent's retrieval sensitivity ceiling — an access control, not a trust one."""
    ev = _stamp(turn, "retrieval_hygiene")
    if not ev:
        # See _read_quarantine: an absent stamp = screening did not run (na), never a gap.
        return _mk("sensitivity_ceiling", "Sensitivity ceiling", "bounded", "na",
                   "Sensitivity ceiling: no knowledge was retrieved on this turn",
                   word="no knowledge retrieved")
    ceiling = ev.get("max_sensitivity")
    if not ceiling:
        return _mk("sensitivity_ceiling", "Sensitivity ceiling", "bounded", "na",
                   "Sensitivity ceiling: no ceiling set for this agent", word="no ceiling set")
    n = int(ev.get("sensitivity_excluded") or 0)
    if n:
        return _mk("sensitivity_ceiling", "Sensitivity ceiling", "bounded", "warn",
                   f"Sensitivity ceiling ({ceiling}): {n} document(s) above the "
                   f"agent's ceiling were withheld from retrieval", True)
    return _mk("sensitivity_ceiling", "Sensitivity ceiling", "bounded", "pass",
               f"Sensitivity ceiling ({ceiling}): nothing retrieved exceeded it")


def _read_retrieval_firewall(turn, _cls):
    """Indirect injection lane — a retrieved chunk carrying an instruction is kept but
    labelled untrusted, never silently dropped."""
    ev = _stamp(turn, "retrieval_hygiene")
    if not ev:
        # See _read_quarantine: an absent stamp = screening did not run (na), never a gap.
        # This matches the two later na branches below (`firewall_off` / no retrieved text) —
        # every no-retrieval shape reads na; `unknown` is never legitimately produced here.
        return _mk("retrieval_firewall", "Retrieval firewall", "guarded", "na",
                   "Retrieval firewall: no knowledge was retrieved on this turn",
                   word="no knowledge retrieved")
    n = int(ev.get("defused_chunks") or 0)
    if n:
        return _mk("retrieval_firewall", "Retrieval firewall", "guarded", "warn",
                   f"Retrieval firewall: {n} retrieved chunk(s) carried an injection "
                   f"pattern — kept but labelled as untrusted data", True)
    if int(ev.get("screened_chunks") or 0):
        return _mk("retrieval_firewall", "Retrieval firewall", "guarded", "pass",
                   f"Retrieval firewall: {ev['screened_chunks']} retrieved chunk(s) "
                   f"screened — clean")
    if ev.get("firewall_off"):
        return _mk("retrieval_firewall", "Retrieval firewall", "guarded", "na",
                   "Retrieval firewall: guardrails are not switched on")
    # Retrieval hygiene ran (quarantine/ceiling) but no chunk reached the firewall.
    return _mk("retrieval_firewall", "Retrieval firewall", "guarded", "na",
               "Retrieval firewall: no retrieved text on this turn")


def _read_needs_input(turn, _cls):
    """PROP-011: a turn that stopped to ask is a clean stop (pass, acted — a reading, never an
    alarm); a turn that went past a code-detected ambiguity without asking is `warn` (the
    measure `guessed_past_ambiguity` grades it at JUDGED); a turn with neither is `na`."""
    q = _stamp(turn, "input_request")
    closed = (_d(turn).get("input_requests_closed") or []) if isinstance(_d(turn).get("input_requests_closed"), list) else []
    observed = _d(turn).get("ambiguities_observed") or []
    used = _d(turn).get("input_defaults_used") or []
    if q.get("id"):
        who = {"code": "the platform", "model": "the agent", "child": "an agent it asked", "data_source": "its data source"}.get(
            str(q.get("raised_by") or ""), "the platform")
        return _mk("needs_input", "Needs input", "reviewed", "pass",
                   f"Needs input: the turn stopped to ask the user ({who} raised it; {q.get('kind')}) — "
                   f"nothing ran past the question", acted=True, word="asked the user")
    if observed:
        return _mk("needs_input", "Needs input", "reviewed", "warn",
                   f"Needs input: {len(observed)} ambiguit{'y' if len(observed) == 1 else 'ies'} the platform saw "
                   f"({', '.join(sorted({str(o.get('kind')) for o in observed if isinstance(o, dict)}))}) — the turn went on "
                   f"without asking (raisers in observe, or the agent chose)", acted=False,
                   word=f"went past {len(observed)}")
    if closed:
        return _mk("needs_input", "Needs input", "reviewed", "pass",
                   "Needs input: a question was due and closed by the agent's policy (never) — the answer says so",
                   acted=True, word="closed by policy")
    if used:
        return _mk("needs_input", "Needs input", "reviewed", "pass",
                   f"Needs input: {len(used)} remembered answer{'s' if len(used) != 1 else ''} used instead of asking",
                   acted=True, word="remembered answer")
    return _mk("needs_input", "Needs input", "reviewed", "na",
               "Needs input: nothing on this turn needed the user", word="nothing to ask")


def _read_review_parking(turn, _cls):
    """HITL: in `review` mode a gate withholds AND parks the original for the owner.
    Its own point because the outcome differs — nothing is lost, and someone owes a
    decision."""
    for key in ("guardrail", "guardrail_inbound", "grounding", "honesty"):
        ev = _stamp(turn, key)
        rid = ev.get("review_id")
        if rid:
            return _mk("review_parking", "Human review", "reviewed", "warn",
                       f"Human review: the withheld original was parked for the owner "
                       f"(item {rid}) — nothing was lost", True)
    # If any gate ran in `review` mode, human review is ARMED and simply had nothing to
    # park — that is a pass, exactly like a guard that screened and found nothing.
    # Only when no gate is in review mode is the station genuinely not turned on.
    for key in ("guardrail", "guardrail_inbound", "grounding", "honesty"):
        if _stamp(turn, key).get("mode") == "review":
            return _mk("review_parking", "Human review", "reviewed", "pass",
                       "Human review: armed — nothing on this answer needed a human",
                       word="armed, nothing parked")
    return _mk("review_parking", "Human review", "reviewed", "na",
               "Human review: no gate is set to park answers for a human",
               word="not armed")


def _read_obda(turn, _cls):
    # Station is GROUNDED, matching this mechanism's AssurancePoint and its dimension
    # (`governed_data_ratio`). The Aug-2026 re-home moved the AssurancePoint + the dimension
    # partition + the ring's STATION_DIMS mirror to GROUNDED but left this reader stamping
    # `scored`, so the live mechanism rendered under JUDGED's "what ran" while its dimension
    # was counted at GROUNDED — the split an operator hit. `station_reader_matches_point`
    # now gates the two against drifting again.
    ev = _stamp(turn, "obda")
    if not ev:
        # No stamp has TWO causes and they must not share a sentence: the lane is OFF here
        # (the scan never ran — a turn that DID query would otherwise read "no data
        # operations", which is false), or it ran and the turn moved no data.
        try:
            from trusted_data_agent.agent.assurance.contract import master_switch_off
            off = master_switch_off("data_governance")
        except Exception:  # noqa: BLE001
            off = None
        if off:
            return _mk("governed_data", "Governed data", "grounded", "na",
                       f"Governed data: {off}", word="lane off here")
        return _mk("governed_data", "Governed data", "grounded", "na",
                   "Governed data: no data operations on this turn", word="no data operations")
    gov = int(ev.get("governed_count") or 0)
    free = int(ev.get("freeform_count") or 0)
    denied = int(ev.get("denied_count") or 0)
    if ev.get("lane") == "confined":
        # OBDA-READ Phase C: every raw-SQL attempt was DENIED by the data surface —
        # nothing executed outside it. The confinement holding is the mechanism
        # working, never a warn (a warn here punishes the agent for being confined).
        return _mk("governed_data", "Governed data", "grounded", "pass",
                   f"Governed data: {denied} raw-SQL attempt(s) denied by the data "
                   f"surface — nothing executed outside it")
    # Requests the CONTRACT refused (unknown member, grain conflict) — governance
    # working, not data moving. Counting them as "operations that ran" read
    # "3 governed data operations" over a turn where one moved no data at all
    # (operator finding). Named beside the lanes, never folded in.
    rejected = int(ev.get("rejected_count") or 0)
    _rej_note = (f" · {rejected} request(s) rejected by the contract (nothing ran)"
                 if rejected else "")
    if ev.get("lane") == "governed":
        return _mk("governed_data", "Governed data", "grounded", "pass",
                   f"Governed data: all {gov} data operation(s) ran through "
                   f"governed metrics{_rej_note}")
    return _mk("governed_data", "Governed data", "grounded", "warn",
               f"Governed data: {free} free-formed / {gov} governed "
               f"(the advisory lane, shown honestly){_rej_note}")



def _plural_checks(n: int) -> str:
    """"1 check" / "N checks" — no `(s)` ever reaches a rendered surface."""
    return f"{n} check" if n == 1 else f"{n} checks"


def measurement_status(turn: Any) -> Dict[str, str]:
    """JUDGED's own absence reading — the two facts no measurement can state.

    WHY THIS IS NOT A MECHANISM. It replaces the `evaluation` registry point, which was
    a fiction: JUDGED needed a row because stations have mechanisms, so a "mechanism" was
    invented for the one station where NOTHING RUNS. Under the lane cut that fiction
    actively misled — it sat in a list headed "what ran · acted during the turn" while
    running after the turn and acting never (its own declared limit: "it grades, it never
    acts"), and it computed a SUMMARY of the same measurements the station folds anyway.
    The two disagreed: on a turn a rule failed, the row read `warn` while the station
    read `danger`, putting a quieter sentence directly under a red segment — the exact
    defect `_driverPoint` exists to prevent, one station over.

    So the summary is gone (the measurements are their own reading) and only the ABSENCE
    survives, which is genuinely station-level:

      ``scored``   measurements exist — the station reads them; nothing to add here
      ``pending``  scoring runs AFTER the answer, so an absent score on a recent turn
                   means "not in yet". Rendering that as `na` would say the layer is
                   switched off, which is a different and false statement (Rule 9.1).
      ``absent``   nothing was scored. Rule 3.7 — no vacuous clean: an eval block that
                   recorded NOTHING must not read "every check passed", because absence
                   of evidence is not evidence of health.

    Pure and defensive: a malformed turn yields `absent`, never an exception.
    """
    ev = _d(turn).get("eval")
    ev = ev if isinstance(ev, dict) else {}
    dims = [d for d in (ev.get("dimensions") or [])
            if isinstance(d, dict) and d.get("k")]
    if dims or int(ev.get("checks") or 0):
        return {"state": "scored", "detail": ""}
    if not ev and not turn_predates_tracing(turn) and turn_within_scoring_window(turn):
        return {"state": "pending",
                "detail": "scoring runs after the answer — not in yet"}
    if not ev:
        return {"state": "absent", "detail": "no score recorded for this turn"}
    return {"state": "absent",
            "detail": "no check ran on this turn — nothing was scored here"}


def _read_compute_lineage(turn, _cls):
    """GROUNDED — Compute with Lineage (S3). Reads the turn's `compute_lineage` stamp.
    Absent ⇒ `na` (the switch is off here, the turn did no data ops, or it predates S3);
    every figure traced ⇒ `pass` naming how many by receipt and how many quoted; an
    unreceipted figure ⇒ `warn` naming it (and whether it was disclosed) — never a verdict
    that the figure is wrong."""
    ev = _stamp(turn, "compute_lineage")
    if not ev:
        return _mk("compute_lineage", "Number lineage", "grounded", "na",
                   "Number lineage: no reading on this turn — the switch is off here, the turn "
                   "read no data, or it predates the lineage", word="not armed")
    if ev.get("decision") == "na":
        return _mk("compute_lineage", "Number lineage", "grounded", "na",
                   "Number lineage: no result sets or receipts to trace against this turn",
                   word="nothing to trace")
    rec = len(ev.get("receipted") or []); quo = len(ev.get("quoted") or [])
    lit = len(ev.get("literals") or []); dec = len(ev.get("declared") or [])
    unr = list(ev.get("unreceipted") or []); figs = int(ev.get("figures") or 0)
    der = len(ev.get("derived") or []); near = [m for m in (ev.get("misderived") or []) if isinstance(m, dict)]
    extra = (f", {lit} written into a statement as a filter or bound" if lit else "") + \
            (f", {dec} a code the graph declares" if dec else "") + \
            (f", {der} derived from a result column (a sum or an average)" if der else "")
    if unr:
        shown = ", ".join(str(u) for u in unr[:4]) + (" …" if len(unr) > 4 else "")
        how = "disclosed in the answer" if ev.get("disclosure_appended") else "recorded only (observe)"
        close = ("; " + "; ".join(f"{m.get('figure')} is close to, but not, {m.get('lineage')}" for m in near[:3])
                 if near else "")
        return _mk("compute_lineage", "Number lineage", "grounded", "warn",
                   f"Number lineage: {len(unr)} of {figs} figure(s) match no compute receipt, no "
                   f"result row, no statement literal and no declared code — {shown} — the agent's own "
                   f"arithmetic, {how}; {rec} computed with a receipt, {quo} quoted from the data{extra}{close}")
    if not figs:
        return _mk("compute_lineage", "Number lineage", "grounded", "pass",
                   "Number lineage: the answer states no figures", word="no figures")
    return _mk("compute_lineage", "Number lineage", "grounded", "pass",
               f"Number lineage: every figure traces — {rec} computed with a receipt, {quo} quoted "
               f"from a result row{extra} ({int(ev.get('receipts') or 0)} receipt(s) sealed)")


def _read_llm_recovery(turn, _cls):
    """SIGNED — the Resilience Envelope (S4), read for the one fact SIGNED owns: WHICH model
    answered, and whether it is inside the signature. The signed deployment names two — the
    primary and `llmFallbackConfigurationId` (`core/trust/deployment.py`) — so either one
    serving is `pass`; a served model that is NOT the declared fallback is `warn`, the only
    SIGNED finding a recovery can produce.

    Retries and continuations keep the turn alive and say nothing about the signature: named
    in the text, never a colour. A provider refusal means NO model answered — `na` with its
    own word, the act recorded (`acted`), the honest answer named. Absent ⇒ `na`.

    It used to read `warn` for a fallback ("declared deployment drift") and for a refusal,
    turning SIGNED amber on turns where nothing about the signature had changed — the
    fallback is signed, and nothing on the signature side reacts to it."""
    ev = _stamp(turn, "resilience")
    # A turn no model answered (the provider failed, the attempts ran out): nothing bears on the
    # signature — `na`, the failure is read at JUDGED (clean_termination). Both branches below used
    # to claim "the configured model served" on such a turn (trust campaign C18).
    from trusted_data_agent.core import turn_states as _ts
    if _ts.is_failure((turn or {}).get("status")) and not ((ev or {}).get("served_by")):
        _c = (ev or {}).get("counts") or {}
        _tried = (f"{_c['retry']} retr{'y' if _c['retry'] == 1 else 'ies'}, then " if _c.get("retry") else "")
        return _mk("llm_recovery", "Recovery", "signed", "na",
                   f"Recovery: no model answered — {_tried}the call failed; nothing bears on the "
                   f"signature (the failed turn is read at JUDGED)", acted=bool(_c), word="no answer")
    if not ev or not ev.get("steps"):
        return _mk("llm_recovery", "Recovery", "signed", "na",
                   "Recovery: nothing to recover on this turn — the configured model served on the "
                   "first attempt, or the envelope is off here", word="nothing recovered")
    counts = ev.get("counts") or {}
    served = ev.get("served_by") or {}
    parts = []
    if counts.get("retry"):
        parts.append(f"{counts['retry']} retr{'y' if counts['retry'] == 1 else 'ies'}")
    if counts.get("continuation"):
        parts.append(f"{counts['continuation']} continuation{'s' if counts['continuation'] != 1 else ''}")
    joined = ", ".join(parts) if parts else "no retry"
    if ev.get("refusal"):
        cat = (ev["refusal"] or {}).get("category") or "refusal"
        return _mk("llm_recovery", "Recovery", "signed", "na",
                   f"Recovery: the provider refused the request ({cat}) — no model answered, so nothing "
                   f"bears on the signature; an honest answer naming the refusal was delivered; "
                   f"{joined} before it", acted=True, word="refused")
    if served:
        prim = ev.get("primary") or {}
        decl = ev.get("fallback_declared") or {}
        name = f"{served.get('provider')}/{served.get('model')}"
        # A turn recorded before the declared fallback was stamped carries no `decl`: the
        # envelope can only switch to the resolved fallback, so it reads inside.
        if decl and (decl.get("provider"), decl.get("model")) != (served.get("provider"), served.get("model")):
            return _mk("llm_recovery", "Recovery", "signed", "warn",
                       f"Recovery: answered by {name}, which is not the agent's signed fallback "
                       f"({decl.get('provider')}/{decl.get('model')}) — outside the signature",
                       acted=True, word="outside the signature")
        return _mk("llm_recovery", "Recovery", "signed", "pass",
                   f"Recovery: answered by the signed fallback {name} instead of "
                   f"{prim.get('provider') or '?'}/{prim.get('model') or '?'} — inside the signature "
                   f"({joined} first); cost billed at the model that served",
                   acted=True, word="signed fallback")
    return _mk("llm_recovery", "Recovery", "signed", "pass",
               f"Recovery: {joined} — the configured model served the answer")


def _read_attachment_sources(turn, _cls):
    """GROUNDED — Grounded Multimodal (S9). Reads the turn's `attachment_sources` stamp.
    Absent ⇒ `na` (nothing attached, or the switch is off here); every usable source verified ⇒
    `pass` naming the files and methods; an UNVERIFIED (model-transcribed) source the answer
    may rest on ⇒ `warn` naming it — the platform could not vouch for it; only unread sources
    ⇒ `na` saying the model saw them natively and nothing was vouched for."""
    ev = _stamp(turn, "attachment_sources")
    if not ev:
        return _mk("attachment_sources", "Attachment sources", "grounded", "na",
                   "Attachment sources: nothing attached to this turn, or the switch is off here",
                   word="none attached")
    srcs = [x for x in (ev.get("sources") or []) if isinstance(x, dict)]
    verified = [x for x in srcs if x.get("verified") and (x.get("tables") or x.get("text_chars"))]
    unverified = [x for x in srcs if not x.get("verified") and (x.get("tables") or x.get("text_chars"))]
    unread = [x for x in srcs if not (x.get("tables") or x.get("text_chars"))]

    def _name(x):
        m = str(x.get("method") or "none")
        eng = f"/{x['engine']}" if x.get("engine") and x.get("engine") != "model" else ""
        t = x.get("tables") or []
        rows = sum(int(tt.get("row_count") or 0) for tt in t if isinstance(tt, dict))
        what = f"{len(t)} table(s), {rows} row(s)" if t else f"{int(x.get('text_chars') or 0)} chars of text"
        return f"{x.get('filename')} ({m}{eng}: {what})"
    if unverified:
        return _mk("attachment_sources", "Attachment sources", "grounded", "warn",
                   f"Attachment sources: {len(unverified)} transcribed by the model, unverified — "
                   f"{', '.join(_name(x) for x in unverified[:3])} — the answer may rest on a reading the "
                   f"platform could not check" + (f"; {len(verified)} verified" if verified else ""))
    if verified:
        return _mk("attachment_sources", "Attachment sources", "grounded", "pass",
                   f"Attachment sources: {len(verified)} read by the platform, verified — "
                   f"{', '.join(_name(x) for x in verified[:3])}" +
                   (f"; {len(unread)} could not be read (the model saw it natively)" if unread else ""))
    return _mk("attachment_sources", "Attachment sources", "grounded", "na",
               f"Attachment sources: {len(unread)} attached, none the platform could read — "
               f"{', '.join(str(x.get('filename')) for x in unread[:3])} — the model saw them natively; "
               "nothing here is vouched for", word="not readable")


def _read_outcome_loop(turn, _cls):
    """GROUNDED — the Outcome Loop (S7). Reads the turn's `outcome` stamp. Absent ⇒ `na`
    (no revision happened — nothing revisable, or the loop is off); revised and cleared ⇒
    `pass` naming what was removed (the delivered answer passed the gate); revised and NOT
    cleared ⇒ `warn` (the honest annotated first draft was delivered — the finding stands)."""
    ev = _stamp(turn, "outcome")
    if not ev or not int(ev.get("revisions") or 0):
        return _mk("outcome_loop", "Outcome loop", "grounded", "na",
                   "Outcome loop: no revision on this turn — the draft passed the check, nothing revisable "
                   "was found, or the loop is off here", word="no revision")
    n = int(ev.get("revisions") or 0)
    kinds = sorted({str(f.get("kind", "")).replace("_", " ") for f in (ev.get("findings_before") or [])})
    what = ", ".join(kinds) or "finding"
    if ev.get("cleared"):
        return _mk("outcome_loop", "Outcome loop", "grounded", "pass",
                   f"Outcome loop: revised {'once' if n == 1 else f'{n} times'} — {what} removed; the revision "
                   f"passed the gate and was delivered (both drafts kept)", acted=True, word="revised, cleared")
    left = len(ev.get("findings_after") or [])
    return _mk("outcome_loop", "Outcome loop", "grounded", "warn",
               f"Outcome loop: revised {'once' if n == 1 else f'{n} times'} — {what} not cleared ({left} finding"
               f"{'s' if left != 1 else ''} remain); the honest annotated first draft was delivered, never a second "
               f"silent try", acted=True, word="revised, not cleared")


def _read_tool_surface(turn, _cls):
    """BOUNDED — the Governed Tool Surface (S6). Reads the turn's `tool_surface` stamp.
    Absent ⇒ `na` (surface mode `all`, or the switch is off); offered/held with nothing denied
    asked for ⇒ `pass` naming the split; a denied tool the model asked for ⇒ `warn` naming it
    (the boundary held — it was never offered nor found — but the model reached for it)."""
    ev = _stamp(turn, "tool_surface")
    if not ev or not ev.get("counts"):
        return _mk("tool_surface", "Tool surface", "bounded", "na",
                   "Tool surface: every enabled tool was offered — the surface is not governed on this agent, "
                   "or the switch is off here", word="not governed")
    c = ev.get("counts") or {}
    split = (f"offered {c.get('offered', 0)} of {c.get('total', 0)} tools"
             + (f" · {c.get('widened')} found by the model" if c.get("widened") else "")
             + (f" · {c.get('denied')} denied by authority" if c.get("denied") else ""))
    asked = ev.get("asked_denied") or []
    if asked:
        return _mk("tool_surface", "Tool surface", "bounded", "warn",
                   f"Tool surface: the model asked for {len(asked)} tool{'s' if len(asked) != 1 else ''} its authority denies "
                   f"({', '.join(asked[:4])}) — never offered, never found; {split}",
                   acted=True, word="asked for a denied tool")
    return _mk("tool_surface", "Tool surface", "bounded", "pass", f"Tool surface: {split}", word="governed")


def _read_actions(turn, _cls):
    """BOUNDED — signed actions (OBDA-ACT). Reads the turn's `actions` record folded with the
    confirmations a person gave afterwards (`action_confirmations`) through the ONE
    `action_perform.summarize`. None ⇒ `na`; a refusal ⇒ `warn`, named (the boundary held —
    nothing was written); a parked act ⇒ `warn` (the owner owes a decision); otherwise
    `pass` naming what was acted."""
    recs = turn.get("actions") if isinstance(turn, dict) else None
    conf = turn.get("action_confirmations") if isinstance(turn, dict) else None
    if not recs and not conf:
        return _mk("actions", "Actions", "bounded", "na",
                   "Actions: no signed action was invoked on this turn", word="none")
    try:
        from trusted_data_agent.core.action_perform import summarize
        sm = summarize(recs if isinstance(recs, list) else [], conf if isinstance(conf, dict) else {})
    except Exception:  # noqa: BLE001
        return _mk("actions", "Actions", "bounded", "unknown",
                   "Actions: the turn's action record could not be read")
    parts = []
    for k, w in (("performed", "acted"), ("prepared", "awaiting confirmation"), ("parked", "parked"),
                 ("observed", "observed"), ("refused", "refused")):
        if sm[k]:
            parts.append(f"{w} {sm[k]}")
    line = " · ".join(parts) or "nothing recorded"
    if sm["refused"]:
        return _mk("actions", "Actions", "bounded", "warn",
                   f"Actions: {line} — " + "; ".join(sm["refusals"][:3]) + ". Nothing was written by a refused act.",
                   acted=True, word=line)
    if sm["parked"]:
        return _mk("actions", "Actions", "bounded", "warn",
                   f"Actions: {line} — a parked write waits for the owner's decision in the Assurance Inbox",
                   acted=True, word=line)
    return _mk("actions", "Actions", "bounded", "pass", f"Actions: {line}", word=line)


def _read_turn_budget(turn, _cls):
    """BOUNDED — the Visible Budget (S5). Reads the turn's `budget` stamp. Absent ⇒ `na`
    (no budget declared, or the switch is off); within ⇒ `pass` naming the use; exhausted
    and graceful ⇒ `warn` naming the unit and the refusals (the answer says what is left);
    killed by a hard stop ⇒ `danger` (the boundary held only by the last line)."""
    ev = _stamp(turn, "budget")
    units = ev.get("units") if ev else None
    if not ev or not units:
        return _mk("turn_budget", "Turn budget", "bounded", "na",
                   "Turn budget: no budget declared on this agent, or the budget is off here",
                   word="no budget", dims=("budget_adherence",))
    use = " · ".join(f"{u.get('used')} of {u.get('total')} {u.get('unit')}" for u in units if isinstance(u, dict))
    if ev.get("killed"):
        return _mk("turn_budget", "Turn budget", "bounded", "danger",
                   f"Turn budget: the {ev.get('exhausted')} budget ran out and the turn was KILLED by a hard "
                   f"stop ({ev.get('killed')}) — the answer does not say what is left ({use})",
                   acted=True, word="killed", dims=("budget_adherence",))
    if ev.get("exhausted"):
        n = int(ev.get("refusals") or 0)
        return _mk("turn_budget", "Turn budget", "bounded", "warn",
                   f"Turn budget: the {ev.get('exhausted')} budget ran out — finished gracefully, "
                   f"{n} further tool call{'s' if n != 1 else ''} refused, the answer says what is left ({use})",
                   acted=True, word="exhausted, graceful", dims=("budget_adherence",))
    return _mk("turn_budget", "Turn budget", "bounded", "pass",
               f"Turn budget: within budget ({use})", word="within budget", dims=("budget_adherence",))


# The provider cache capabilities the `cache` stamp can carry (mirrors llm.cache_policy —
# pinned equal there; this module stays import-light). Only BREAKPOINTS attests a prefix:
# the platform set an explicit cache_control on bytes it hashed. The others are
# best-effort — the provider caches on its own and may report it; a same-prefix miss
# there says nothing about the prefix.
CACHE_CAP_BREAKPOINTS = "breakpoints"
CACHE_CAPS_BEST_EFFORT = frozenset({"implicit", "automatic"})


def _read_prompt_cache(turn, _cls):
    """SIGNED — the Attested Cache (S2). Reads the turn's `cache` stamp (facts accumulated
    per call by the handler and the LangChain lanes, sealed at persistence). Absent ⇒ `na`
    (caching not armed on this deployment, or an older turn); a provider with no cache
    ⇒ `na` naming it; an unchanged prefix that missed the cache inside its lifetime ⇒
    `warn` ONLY under an explicit breakpoint (something outside the hashed bytes moved —
    worth a look, never a verdict about the answer); under best-effort caching
    (implicit/automatic) the same miss is the provider's choice ⇒ informational, never a
    finding; a served prefix ⇒ `pass` with the share."""
    ev = _stamp(turn, "cache")
    if not ev:
        return _mk("prompt_cache", "Prompt cache", "signed", "na",
                   "Prompt cache: no reading on this turn — caching is not armed here, or the "
                   "turn predates it", word="not armed")
    cap = str(ev.get("capability") or "none")
    if cap == "none":
        return _mk("prompt_cache", "Prompt cache", "signed", "na",
                   "Prompt cache: this provider has no prompt cache", word="no cache")
    misses = int(ev.get("misses_found") or 0)
    cached = int(ev.get("cached_tokens") or 0)
    total = int(ev.get("input_tokens") or 0)
    calls = int(ev.get("calls") or 0)
    hits = int(ev.get("hits") or 0)
    if misses and cap == CACHE_CAP_BREAKPOINTS:
        # The platform ASKED for this prefix to be cached (an explicit breakpoint) and
        # hashed the bytes it asked about — the hash did not move, so what moved sits
        # OUTSIDE it: a tool schema, an enrichment placed before the breakpoint, or the
        # provider evicted the entry. Worth a look, never a verdict about the answer.
        return _mk("prompt_cache", "Prompt cache", "signed", "warn",
                   f"Prompt cache: an unchanged prefix missed the cache {misses} time(s) inside its "
                   f"lifetime — the bytes the platform hashed did not change, so something outside "
                   f"that hash moved under the agent (a tool schema, a rebuilt graph's enrichment, a "
                   f"changed prompt) or the provider dropped the entry; {cached:,} of {total:,} prompt "
                   f"tokens were served from cache")
    if misses:
        # Best-effort caching (Gemini implicit, OpenAI-shaped automatic, or a turn that
        # crossed providers): the provider decides what it serves, the platform placed no
        # breakpoint, and its own prefix hash was UNCHANGED — so a same-prefix miss is
        # the provider's choice (an entry not yet built, evicted, or never made), not a
        # prefix that moved. The old reading claimed drift the platform's own hash
        # refuted (prod session d9a60903). Informational; never a finding.
        _how = ("provider-decided" if cap in CACHE_CAPS_BEST_EFFORT else "mixed providers")
        if cached:
            return _mk("prompt_cache", "Prompt cache", "signed", "pass",
                       f"Prompt cache: {cached:,} of {total:,} prompt tokens served from cache "
                       f"({hits} of {calls} calls); {misses} call(s) on the same unchanged prefix were "
                       f"not served — caching here is {_how} (best-effort, no breakpoint set), so that "
                       f"is the provider's choice, not a prefix that moved")
        return _mk("prompt_cache", "Prompt cache", "signed", "na",
                   f"Prompt cache: the provider served nothing from cache on {misses} call(s) whose "
                   f"prefix was unchanged — caching here is {_how} (best-effort, no breakpoint set), "
                   f"so nothing was attested and nothing moved; not a finding", word="best-effort")
    if cached:
        return _mk("prompt_cache", "Prompt cache", "signed", "pass",
                   f"Prompt cache: {cached:,} of {total:,} prompt tokens served from cache "
                   f"({hits} of {calls} calls) — the prefix the provider served is the one it was "
                   f"asked to cache")
    if not ev.get("enabled"):
        return _mk("prompt_cache", "Prompt cache", "signed", "na",
                   "Prompt cache: not enabled for this agent", word="not enabled")
    return _mk("prompt_cache", "Prompt cache", "signed", "na",
               "Prompt cache: nothing to corroborate yet — the first call of a session, or a "
               "prefix below the provider's minimum", word="nothing yet")


def _read_integrity(turn, _cls):
    """SIGNED — the agent's integrity AT ANSWER TIME.

    The stamp is sealed onto the turn WHEN IT RUNS (executor.run() via the assurance
    tally; the genie coordinator at its own turn assembly) — the registry stays pure
    and I/O-free, and the reading replays unchanged forever. It must never be filled
    from a live resolve at read time: that substituted today's badge for yesterday's
    fact, so editing an agent flipped every historical turn's SIGNED station.

    An absent stamp has two honest causes — a turn older than the seal, or the
    (removable) trust suite not installed — and both read `na` "not recorded",
    deliberately NOT `unknown`: every pre-seal turn lacks this stamp, and putting all
    of history into attention would be the migration committing its own defect (the
    same reasoning as `turn_predates_tracing`, applied per-gate).
    """
    ev = _stamp(turn, "integrity")
    if not ev:
        return _mk("agent_integrity", "Agent integrity", "signed", "na",
                   "Agent integrity: not recorded when this turn ran — an older turn, "
                   "or the trust membrane is not installed here",
                   word="not recorded")
    state = str(ev.get("state") or "")
    if state == "trusted":
        return _mk("agent_integrity", "Agent integrity", "signed", "pass",
                   "Agent integrity: signed and intact when this answer was produced")
    if state == "changed":
        return _mk("agent_integrity", "Agent integrity", "signed", "danger",
                   "Agent integrity: the agent had CHANGED since it was signed — this "
                   "answer came from something other than what was attested", True)
    if state == "deployment_unattested":
        return _mk("agent_integrity", "Agent integrity", "signed", "warn",
                   "Agent integrity: publisher-signed, but this deployment has not been "
                   "attested here")
    if state == "unsigned":
        return _mk("agent_integrity", "Agent integrity", "signed", "na",
                   "Agent integrity: this agent has never been signed", word="never signed")
    return _mk("agent_integrity", "Agent integrity", "signed", "unknown",
               f"Agent integrity: unrecognised state '{state}'")


#: Behavioural trust states, worst-first. `provisional` is not a failing — it is a new
#: agent with too little evidence, which is a different fact from a poor record.
_TRUST_STATE = {
    "at_risk": ("danger", "at risk"),
    "watch": ("warn", "on watch"),
    # `na`, deliberately NOT `pending`: pending's contract is "has not reported yet"
    # (the live dial's in-flight state, with a moving-dash animation) — but a
    # provisional agent HAS reported; the reading is "too young a record to grade",
    # and nothing more is coming for this turn. Mapping it to pending left the
    # TRUSTED segment animating forever on settled turns, surviving page refreshes.
    # The registry NEVER emits `pending` — that state belongs exclusively to the
    # client's live model while a turn is actually running.
    "provisional": ("na", "still proving"),
    "proven": ("pass", "proven"),
    "trusted": ("pass", "trusted"),
}


def _read_trailing_trust(turn, _cls):
    """TRUSTED — the agent's trailing behavioural state.

    It is a WINDOW measurement, so it says nothing about this answer specifically — but
    that does not make it absent, and rendering the station blank claimed there was
    nothing to say. The scope is stated in the detail rather than hidden by a silent
    station: same subject, honestly labelled.
    """
    ev = _stamp(turn, "trailing_trust")
    state = str(ev.get("state") or "")
    turns = ev.get("turns")
    if state in _TRUST_STATE:
        band, word = _TRUST_STATE[state]
        over = f" over the last {turns} turns" if turns else " over a trailing window"
        return _mk("trailing_trust", "Trailing trust state", "trusted", band,
                   f"Trailing trust: this agent reads {word}{over} — a record, "
                   f"not a property of this one answer",
                   state in ("at_risk",), word=word)
    return _mk("trailing_trust", "Trailing trust state", "trusted", "na",
               "Trailing trust: behavioural trust scoring is not switched on",
               word="not scored")


def _read_seal(turn, _cls):
    pm = _stamp(turn, "provenance_meta")
    if not pm.get("sealed"):
        return _mk("provenance_seal", "Provenance seal", "proven", "na",
                   "Provenance: no sealed chain recorded on this turn", word="not sealed")
    tip = str(pm.get("chain_tip_hash") or "")[:12]
    return _mk("provenance_seal", "Provenance seal", "proven", "pass",
               f"Provenance: turn sealed into the signed chain\n"
               f"tip {tip}… · {pm.get('step_count') or '?'} step(s)")


def _read_memory_firewall(turn, _cls):
    """Never reached — recorded as an exclusion. See EXCLUSIONS below."""
    return _mk("memory_firewall", "Memory firewall", "guarded", "na",
               "Memory firewall: protects future turns, not this one")


# ── the registry ──────────────────────────────────────────────────────────────
# Order is the order an answer meets them, so a surface can render the path without
# knowing the pipeline.
REGISTRY: Tuple[AssurancePoint, ...] = (
    AssurancePoint("agent_integrity", "Agent integrity", "signed", "attest",
                   _read_integrity, turn_key="integrity"),
    # S2 — the Attested Cache: a per-turn CORROBORATION of the deployment surface (the
    # prefix the provider was asked to cache is the prefix it served). SIGNED, beside the
    # integrity stamp it corroborates — NOT TRUSTED, which is the ONE aggregate by contract
    # (Rule 3b.6) and takes no per-turn mechanism.
    AssurancePoint("prompt_cache", "Prompt cache", "signed", "attest",
                   _read_prompt_cache, turn_key="cache"),
    # S4 — the Resilience Envelope: retries, continuations, the fallback that served, a
    # refusal. At SIGNED it is EVIDENCE, not a control: the fallback is part of the signed
    # deployment, so the one fact it adds is which model answered and whether that model is
    # inside the signature. The recovery act itself upholds no guarantee.
    AssurancePoint("llm_recovery", "Recovery", "signed", "alter",
                   _read_llm_recovery, turn_key="resilience"),
    # S5 — the Visible Budget: a declared, model-visible boundary on what one turn may
    # spend; exhaustion is a graceful finish the turn RECORDS, a kill is the hard stop.
    # OBDA-ACT — signed actions: the agent writes only through a signed contract it is granted;
    # BOUNDED reads what it acted and what was refused.
    AssurancePoint("actions", "Actions", "bounded", "constrain",
                   _read_actions, turn_key="actions"),
    AssurancePoint("turn_budget", "Turn budget", "bounded", "constrain",
                   _read_turn_budget, turn_key="budget",
                   applies_to=frozenset({"tool_enabled", "conversation_with_tools", "genie"})),
    # S9 — Grounded Multimodal: what the platform read from each attachment, verified or
    # not. GROUNDED — it is evidence for the gate's measurements; a measure, never an act.
    AssurancePoint("attachment_sources", "Attachment sources", "grounded", "attest",
                   _read_attachment_sources, turn_key="attachment_sources"),
    # S7 — the Outcome Loop: one bounded revision when the gate's finding is fixable; the
    # revision delivered only if it cleared, both drafts sealed. GROUNDED — it is the gate's own
    # rubric acting one step earlier.
    AssurancePoint("outcome_loop", "Outcome loop", "grounded", "alter",
                   _read_outcome_loop, turn_key="outcome"),
    # S6 — the Governed Tool Surface: what the model was shown, what it could find, what it
    # could never reach; a denied tool it asked for is the named finding.
    AssurancePoint("tool_surface", "Tool surface", "bounded", "constrain",
                   _read_tool_surface, turn_key="tool_surface",
                   applies_to=frozenset({"tool_enabled", "conversation_with_tools", "genie"})),
    AssurancePoint("inbound_guard", "Inbound guard", "guarded", "withhold",
                   _read_inbound, turn_key="guardrail_inbound"),
    AssurancePoint("authority", "Authority", "bounded", "constrain",
                   _read_authority),
    AssurancePoint("trusted_scope", "Trusted scope", "grounded", "constrain",
                   _read_scope),
    AssurancePoint("preflight_sql", "Preflight SQL", "grounded", "withhold",
                   _read_preflight, turn_key="preflight",
                   applies_to=frozenset({"tool_enabled", "conversation_with_tools"})),
    AssurancePoint("tool_enforcement", "Tool enforcement", "bounded", "constrain",
                   _read_tool_enforcement,
                   applies_to=frozenset({"tool_enabled", "conversation_with_tools", "genie"})),
    AssurancePoint("corpus_quarantine", "Corpus quarantine", "grounded", "constrain",
                   _read_quarantine, turn_key="retrieval_hygiene",
                   applies_to=frozenset({"rag_focused", "tool_enabled",
                                         "llm_only", "conversation_with_tools"})),
    AssurancePoint("sensitivity_ceiling", "Sensitivity ceiling", "bounded", "constrain",
                   _read_sensitivity, turn_key="retrieval_hygiene",
                   applies_to=frozenset({"rag_focused", "tool_enabled",
                                         "llm_only", "conversation_with_tools"})),
    AssurancePoint("retrieval_firewall", "Retrieval firewall", "guarded", "alter",
                   _read_retrieval_firewall, turn_key="retrieval_hygiene",
                   applies_to=frozenset({"rag_focused", "tool_enabled",
                                         "llm_only", "conversation_with_tools"})),
    # `withhold`, not `alter`: its own verdict set declares BLOCK (enforce + an injection
    # or toxicity hit → the content is withheld). `alter` under-stated it, and the fates
    # table (which must agree with the power) would have had to lie one way or the other.
    AssurancePoint("outbound_guard", "Outbound guard", "guarded", "withhold",
                   _read_outbound, turn_key="guardrail"),
    AssurancePoint("grounding_gate", "Grounding gate", "grounded", "withhold",
                   _read_grounding, turn_key="grounding"),
    AssurancePoint("honesty_gate", "Honesty", "grounded", "alter",
                   _read_honesty, turn_key="honesty"),
    # S3 — Compute with Lineage: where every figure in the answer came from. GROUNDED,
    # beside the honesty gate whose disclosure idiom it shares; a control (it appends a
    # disclosure), never a rewrite.
    AssurancePoint("compute_lineage", "Number lineage", "grounded", "alter",
                   _read_compute_lineage, turn_key="compute_lineage",
                   applies_to=frozenset({"tool_enabled", "conversation_with_tools"})),
    # GROUNDED. The earlier note recorded only what this is NOT ("SCORED, not TRUSTED"
    # — TRUSTED is a trailing-window measurement with no honest reading for one answer,
    # and hanging a per-turn mechanism on it made it read `warn` for a single turn).
    # `scored` was the leftover, and while JUDGED was the UNION that cost nothing: a
    # mechanism's station did not decide where its dimension was counted, because every
    # dimension was counted there. The partition made the station the decision, and
    # `governed_data_ratio` then sat at JUDGED — contradicting JUDGED's own definition
    # ("the checks no other station owns") with a dimension that IS owned.
    #
    # The positive reason: this measures whether the turn's data came through the
    # governed semantic layer or was free-formed SQL — whether the answer rests on
    # declared, traceable data operations. That is what GROUNDED guarantees.
    AssurancePoint("governed_data", "Governed data", "grounded", "attest",
                   _read_obda, turn_key="obda",
                   applies_to=frozenset({"tool_enabled", "conversation_with_tools"})),
    AssurancePoint("review_parking", "Human review", "reviewed", "withhold",
                   _read_review_parking),
    # PROP-011: a turn that stops to ask the user — a person is needed before the work goes on
    AssurancePoint("needs_input", "Needs input", "reviewed", "withhold",
                   _read_needs_input, turn_key="input_request"),
    # `evaluation` was RETIRED here — see `measurement_status`. JUDGED is the one
    # station where nothing runs: it reports what the measurements found after the
    # turn, and a "mechanism" invented to give it a row was a fiction that eventually
    # contradicted its own column header and disagreed with the station above it.
    # ⚠ KNOWN LABELLING DEFECT (recorded 2026-08-05, deliberately not fixed here).
    #
    # This is a `measure` that does NOT run during the turn — it is a trailing-window
    # aggregate over the record — yet it renders in the dial's mechanism list, whose
    # header reads "what ran · acted during the turn". Same symptom as the retired
    # `evaluation` point, and it was evaluated for the same treatment.
    #
    # It is NOT the same defect, which is why it survives. `evaluation` was REDUNDANT:
    # it restated a summary its station computed anyway, and the two disagreed (warn
    # against danger). Redundancy requires something to be redundant WITH, and JUDGED
    # has 52 measurements. TRUSTED has NONE — `allocate_measurements` sends every scored
    # dimension to MEASUREMENT_STATION — so this point is not an intermediate layer, it
    # is the station's only child. Retiring it would blank TRUSTED entirely.
    #
    # So the fix is not removal but an honest HEADER: a point needs a declared lane
    # (in-flight / post-flight) so a panel can say "what ran" for the ones that did and
    # something accurate for the rest. That is step 0 of
    # docs/roadmap/TRUST_RUNTIME_COMPREHENSIBILITY.md, and it settles `governed_data` in
    # the same stroke — which passes both tests today (it runs in-flight at
    # executor.py:4011 AND genuinely summarises its own declared dimension).
    AssurancePoint("trailing_trust", "Trailing trust state", "trusted", "attest",
                   _read_trailing_trust),
    AssurancePoint("provenance_seal", "Provenance seal", "proven", "attest",
                   _read_seal, turn_key="provenance_meta"),

    # ── deliberate exclusions ─────────────────────────────────────────────────
    # Present so the omission is a recorded decision rather than a gap. An excluded
    # point never appears in a turn reading.
    AssurancePoint("memory_firewall", "Memory firewall", "guarded", "constrain",
                   _read_memory_firewall, excluded=True,
                   exclusion_reason=(
                       "It screens what a turn WRITES to memory, so it protects future "
                       "turns rather than this one. Putting it on this answer's path "
                       "would claim it guarded an answer it never touched. It belongs "
                       "on the agent's posture, not on a turn.")),
)

_BY_KEY: Dict[str, AssurancePoint] = {p.key: p for p in REGISTRY}


# ── public API ────────────────────────────────────────────────────────────────
def unclassified_points() -> List[str]:
    """Registered points with no class. A point the Guide cannot file is a point the
    reader cannot place — the gate fails on a non-empty result."""
    return [p.key for p in REGISTRY if p.key not in POINT_CLASS]


def points(include_excluded: bool = False) -> Tuple[AssurancePoint, ...]:
    if include_excluded:
        return REGISTRY
    return tuple(p for p in REGISTRY if not p.excluded)


def get(key: str) -> Optional[AssurancePoint]:
    return _BY_KEY.get(key)


def stations_covered() -> frozenset:
    """Which stations have at least one live mechanism behind them.

    A station with none is not necessarily wrong — SIGNED and PROVEN are acts and
    attestations, not in-path gates — but it is worth knowing which is which.
    """
    return frozenset(p.station for p in points())


# ── the station partition (mutual exclusivity; TRUSTED is the sole aggregate) ──
#: REFINEMENT PAIRS — a coarse pre-signal and the judge that refines it.
#:
#: Some dimensions measure the same property twice, at different resolutions:
#: `grounded_overlap` counts literal token overlap; `groundedness` reads meaning. The
#: coarse one runs on every turn for free, the judge on sampled turns for tokens, and
#: the coarse one's own description has always said the judge *refines* it.
#:
#: When two measures of ONE property disagree, the disagreement is more informative
#: than either reading alone — so it is reported rather than resolved. Weighting the
#: judge would create a composite with invisible arithmetic (Rule 4.3 permits one only
#: because the drill-down names its driver) AND make the reading depend on whether a
#: sampled judge happened to run. Demoting the pre-signal would silence a check that
#: runs on every turn in favour of one that runs on some. Both answer "which measure is
#: right?", which is the wrong question.
#:
#: Declared, never inferred: one entry, beside the partition it belongs to.
#: Written `refined_by = {coarse: judge}`.
REFINEMENT_PAIRS: Dict[str, str] = {
    "grounded_overlap": "groundedness",
}

#: The four readings a refinement pair can produce (Rule 3.0c). No new state, no
#: weighting — the existing four-word vocabulary already carries the distinction.
CORROBORATED, DISPUTED, JUDGE_ONLY, AGREED_CLEAN = (
    "corroborated", "disputed", "judge_only", "agreed_clean")


def refinement_reading(coarse_band: Optional[str], judge_band: Optional[str]) -> Optional[str]:
    """How a coarse check and its refining judge stand relative to each other.

    ``None`` when the pair cannot be evaluated — the judge did not run (sampling), or
    either side is absent. The caller then treats the coarse check as standing alone at
    full weight, which is correct: with nothing to refine it, it is the only measure
    there is.
    """
    bad = {"danger", "review"}
    if not coarse_band or not judge_band:
        return None
    c_fail, j_fail = coarse_band in bad, judge_band in bad
    if c_fail and j_fail:
        return CORROBORATED          # two independent methods agree → danger
    if j_fail:
        return JUDGE_ONLY            # the judge caught what tokens cannot → danger
    if c_fail:
        return DISPUTED              # flagged by the coarse check, cleared by the judge
    return AGREED_CLEAN


#: What each reading contributes to a station's attention. `disputed` is the whole
#: point: heavy paraphrase (benign) or a lenient judge — either way a human should
#: glance, which is exactly what "worth a look" means. Nothing is silenced: amber still
#: colours the station and still appears in the routing summary.
REFINEMENT_STATE: Dict[str, str] = {
    CORROBORATED: "danger",
    JUDGE_ONLY: "danger",
    DISPUTED: "warn",
    AGREED_CLEAN: "pass",
}

#: The sentence each reading puts in front of an operator. A disagreement that is not
#: NAMED is a composite with invisible arithmetic wearing a reading's clothes.
REFINEMENT_NOTE: Dict[str, str] = {
    CORROBORATED: "both the coarse lexical check and the judge that refines it found the "
                  "answer unsupported — a corroborated failure",
    JUDGE_ONLY: "the judge that reads meaning found the answer unsupported, where literal "
                "token overlap did not — the judge is the better measure of what it measures",
    DISPUTED: "the coarse lexical check flagged this and the judge that refines it cleared "
              "it — either the answer is heavily paraphrased, or the judge was lenient",
    AGREED_CLEAN: "the coarse check and the judge that refines it agree the answer is "
                  "supported",
}


def station_dimensions() -> Dict[str, Tuple[str, ...]]:
    """Which guarantee each measurement is ABOUT — the LABEL map.

    ⚠ THIS IS NO LONGER A COUNTING MAP. It once decided where a dimension was counted,
    and the fold it drove is what made a station's colour blend two different kinds of
    fact (see `MEASUREMENT_STATION`). Counting now belongs to JUDGED alone; what survives
    here is the far more useful half — *what a measurement is about* — which drives every
    READING surface: the scorecard's groups, the roster's dimension bars and band strip,
    the Assurance Guide's station homes, and the admin Trust page's slider grouping.

    Nothing about the derivation changed: still `POINT_DIMENSIONS` × the mechanism's
    station, never hand-listed, so it cannot drift from the registry. `evaluation` is
    excluded because it declares nothing of its own — it is the layer that produces
    measurements, not a mechanism that owns a subset of them.
    """
    out: Dict[str, set] = {}
    for p in points():
        if p.key == "evaluation":
            continue
        for d in POINT_DIMENSIONS.get(p.key) or ():
            out.setdefault(p.station, set()).add(d)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def foreign_dimensions(station: str = MEASUREMENT_STATION) -> frozenset:
    """Every dimension LABELLED to a guarantee other than `station`.

    Formerly the exclusion set that kept JUDGED from double-counting. JUDGED now counts
    everything, so nothing excludes these — the set survives because the reverse question
    is the one that matters under the new model: a station that no longer folds its
    measurements must be able to NAME them and say where they are counted (the reach-loss
    mitigation, plan §D3).
    """
    return frozenset(d for st, dims in station_dimensions().items()
                     if st != station for d in dims)


def partition_conflicts() -> List[str]:
    """Dimensions LABELLED to two different guarantees — a registry error.

    Counting no longer depends on this (everything counts at `MEASUREMENT_STATION`), but
    the label still must be unambiguous: a dimension claimed by mechanisms on two
    stations would appear under two headings on every reading surface, and no rule would
    say which is right. The gate still fails on a non-empty result. Mechanisms of ONE
    station sharing a dimension (the three guarded screens all reading `safety`) is fine.
    """
    homes: Dict[str, set] = {}
    for p in points():
        for d in POINT_DIMENSIONS.get(p.key) or ():
            homes.setdefault(d, set()).add(p.station)
    return sorted(d for d, s in homes.items() if len(s) > 1)


# ── the two vocabularies, and the ONE bridge between them ─────────────────────
#: A quality BAND is not a turn STATE. They are different vocabularies that overlap on
#: exactly one word, and that coincidence hid a real defect for a whole release.
#:
#:     band  (what a measurement scored)   healthy · review · danger · na
#:     state (attention priority)          pass · warn · danger · na · pending · unknown
#:
#: `danger` means the same thing in both; nothing else does. So a renderer that "converts"
#: a band by special-casing `healthy` appears to work — until it meets `review`, the only
#: value where the two genuinely differ, which then falls through as an unrecognised word
#: and ranks BELOW `na`. That is precisely what happened: an amber measurement could never
#: raise its station, so GROUNDED read "quiet" beside its own `context_utilization 0.56`.
#:
#: Hence: exactly one mapping, declared here, mirrored in `trustGrammar.js:BAND_STATE` and
#: pinned equal by the gate. Nothing else may convert a band — not a renderer, not a route.
#: Only the three bands that CARRY a measurement appear. `na` is not a band — it is the
#: absence of one (an abstained dimension), and both resolvers already default to `na`,
#: so listing it would add a second way to say the same thing and put the two language
#: declarations out of step for no gain.
BAND_STATE: Dict[str, str] = {
    "healthy": "pass",
    "review": "warn",
    "danger": "danger",
}


def dimension_state(dim: dict) -> str:
    """What ONE measurement contributes to JUDGED, in the state vocabulary.

    The single resolver, so no surface converts a band itself. Precedence:

      1. ``sc`` — the CONTRIBUTION, present only where a refinement pair decided it
         (Rule 3.0c). It is already a state (`REFINEMENT_STATE`), never a band, and it
         deliberately differs from what the chip shows: a coarse check flagged and cleared
         by the judge that refines it is "worth a look", not "needs you now".
      2. **A judge's danger is capped at ``warn``** — see below.
      3. ``b`` — the band the dimension actually measured, bridged through `BAND_STATE`.

    WHY A JUDGE CANNOT DRIVE THE STATION TO DANGER
    ----------------------------------------------
    `analysis.convicting_dimensions` has held since Aug 2026 that *a judge alone in
    danger does not convict a turn* — the platform's judges are advisory until their
    calibration clears. This resolver did not know that, so one fact had two answers: the
    turn verdict read a lone judge failure as no conviction while the station beside it
    went red on the same finding. Capping it here makes the two agree, and the
    disagreement was real — not a hypothetical the rule guards against.

    It is capped, never silenced: `warn` still colours JUDGED amber, still appears in the
    routing summary, and the chip still shows the measured `danger` via `band_state`. A
    judge that CORROBORATES a rule changes nothing, because the rule convicts on its own.

    This matters far more now than it did: JUDGED counts every scored dimension
    (`MEASUREMENT_STATION`), so without the cap a single sampled judge would put the one
    station that carries all measurement into permanent danger — and a station that can
    never go green gets ignored, which is this platform's own recorded finding about
    GROUNDED.

    An unrecognised band yields ``na`` (counted, never colouring) rather than leaking a
    foreign word into the state vocabulary — the failure mode this function exists to end.
    ``b`` itself is never rewritten: lying about a measurement to calm a station is the
    defect the whole grammar is built against.
    """
    if not isinstance(dim, dict):
        return "na"
    sc = dim.get("sc")
    if sc in STATES:
        return sc
    st = BAND_STATE.get(dim.get("b"), "na")
    if st == "danger" and dim.get("k") in judge_dimensions():
        return "warn"
    return st


def band_state(dim: dict) -> str:
    """What ONE measurement MEASURED, in the state vocabulary — for its own chip.

    The sibling of `dimension_state`, and the reason both exist: a station's colour and a
    chip's colour answer different questions, and Rule 3.0c makes them legitimately
    differ. A coarse check its refining judge cleared measured `danger` and contributes
    `warn`; the station goes amber, the chip stays red, and neither is rewritten to agree
    with the other.

    Resolved here rather than in the renderer for the same reason `st` is: a band is not a
    state, and every surface that converted one by hand eventually dropped `review`.
    """
    if not isinstance(dim, dict):
        return "na"
    return BAND_STATE.get(dim.get("b"), "na")


def allocate_measurements(scored: Dict[str, dict]) -> Dict[str, list]:
    """Allocate this turn's scored dimensions — ALL of them to `MEASUREMENT_STATION`.

    Measurement is a lane, not a guarantee-attribute. Every scored dimension is counted
    at JUDGED and nowhere else, so a station's colour carries exactly one kind of fact:
    the seven guarantee stations report what their MECHANISMS did during the turn, JUDGED
    reports what the MEASUREMENTS found afterwards. The reader never has to ask which of
    the two a colour means.

    Still exclusive — every dimension counted exactly once, so the routing summary cannot
    say "2 need you now" for one root cause. That double count is what the pre-Aug-2026
    union model produced and it does not return here; only the axis of the cut changed,
    from guarantee to lane (`MEASUREMENT_STATION`).

    **The guarantee a dimension is about is not lost** — `station_dimensions()` keeps it
    as a LABEL, and every reading surface (scorecard groups, roster bars, Guide homes)
    still groups by it. What changed is that the label no longer moves a colour.

    Pure. `scored` maps dimension key → its record; each returned record carries `st`
    (its contribution to JUDGED) and `bst` (the band its own chip shows).

    The former `applicable` parameter is REMOVED rather than kept as an ignored no-op. It
    restricted which stations could receive a set, which has no meaning once there is one
    counting station — and a parameter that is accepted and silently ignored is worse
    than none: the next caller passes it, gets no effect, and the only symptom is a
    dimension that quietly fails to appear where it is counted.
    """
    out: Dict[str, list] = {s: [] for s in STATIONS}
    for key in sorted(scored):
        rec = scored[key]
        if not isinstance(rec, dict):
            continue
        out.setdefault(MEASUREMENT_STATION, []).append(
            dict(rec, st=dimension_state(rec), bst=band_state(rec)))
    return out


def scored_dimensions(turn: dict) -> Dict[str, dict]:
    """This turn's scored dimensions, keyed. One reader, so the join seam and the
    allocation cannot disagree about what was scored."""
    out: Dict[str, dict] = {}
    for d in ((_d(turn).get("eval") or {}).get("dimensions") or []):
        if isinstance(d, dict) and d.get("k"):
            out[d["k"]] = d
    return out


def apply_refinements(scored: Dict[str, dict]) -> Tuple[Dict[str, dict], list]:
    """Rule 3.0c, applied — the ONE place a refinement pair is resolved.

    Where a coarse pre-signal and the judge that refines it BOTH decided this turn, the
    pair's standing decides what the coarse dimension contributes to its STATION — never
    what the dimension itself reports. The measurement stays exactly as measured (`b`,
    what the chip shows); a separate `sc` carries the contribution and `disp` marks the
    disagreement so the panel can name it. Rewriting `b` would be lying about a real
    measurement to make a station calmer — the defect this grammar exists to prevent.

    Extracted from `read_turn` because the allocation needs the SAME resolved records:
    computing `sc` on one path and not the other would give the mechanism row and the
    station segment two different answers about one pair, which is the class of
    disagreement the partition was introduced to end.

    Returns `(scored, disputes)` — a new mapping (the input is never mutated) plus the
    pairs that disagreed, for the note the owning mechanism prints.
    """
    scored = dict(scored)
    disputes: list = []
    for coarse, judge in REFINEMENT_PAIRS.items():
        reading = refinement_reading((scored.get(coarse) or {}).get("b"),
                                     (scored.get(judge) or {}).get("b"))
        if reading is None:
            continue                      # the judge did not run — the coarse stands alone
        contrib = REFINEMENT_STATE.get(reading)
        if contrib and contrib != (scored.get(coarse) or {}).get("b"):
            scored[coarse] = dict(scored[coarse], sc=contrib)
        if reading == DISPUTED:
            scored[coarse] = dict(scored[coarse], disp=True)
            disputes.append((coarse, judge, reading))
    return scored, disputes


def read_turn_measures(turn: dict, profile_class: str = "tool_enabled"
                       ) -> Dict[str, list]:
    """The station → measurements allocation for one turn, contributions stamped.

    Shaped as a per-station map, but under `MEASUREMENT_STATION` every measurement lands
    at JUDGED and the other seven keys are empty. The MAP SHAPE IS KEPT DELIBERATELY, and
    that is the mechanism by which two of the six counting sites needed no code change at
    all: the dial's station-state fold and its `_failingDims` both iterate `d.dims`, so an
    empty list makes them data-driven no-ops. A flatter return type would have forced
    branching into two renderers to express what an empty list already says.

    So a station's attention is now worst-of its MECHANISMS alone — what ran during the
    turn — while JUDGED reports what the measurements found afterwards. One kind of fact
    per colour; see `MEASUREMENT_STATION` for why the two were separated.

    `profile_class` is accepted for symmetry with `read_turn`; it does not filter, since
    what a turn scored is already class-determined by the scorer registry.
    """
    scored, _disputes = apply_refinements(scored_dimensions(turn))
    return allocate_measurements(scored)


#: Every turn key an assurance point can stamp. A turn carrying NONE of these was
#: written before the trace existed — the only pre-tracing signal available that does
#: not date-gate the UI (which the migration plan forbids, §5).
_STAMP_KEYS = ("guardrail", "guardrail_inbound", "grounding", "honesty",
               "preflight", "retrieval_hygiene", "obda")


#: How long an unscored turn may honestly read "not in yet". The online eval is
#: dispatched right after the turn persists and its judge layer is bounded at 25 s,
#: so a turn still unscored minutes later is not "pending" — nothing more is coming.
#: Past the window `_read_evaluation` reads the truthful absence ("no score recorded")
#: instead of a pending that never resolves (the forever-pending dial of Sep 2026).
EVAL_PENDING_WINDOW_SECONDS = 180


def turn_within_scoring_window(turn: dict, now: Optional[float] = None) -> bool:
    """Whether the turn is young enough for an absent score to mean "not in yet".

    Reads the turn's own ``timestamp`` (ISO 8601, written by every engine). A turn with
    no readable time keeps the pre-window behaviour (pending) — a legacy shape must not
    be pushed into "absent" on the strength of a field it never carried.
    """
    import time as _time
    from datetime import datetime, timezone
    raw = _d(turn).get("timestamp")
    if not isinstance(raw, str) or not raw:
        return True
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now if now is not None else _time.time()) - ts.timestamp()
    except (ValueError, TypeError, OverflowError):
        return True
    return age < EVAL_PENDING_WINDOW_SECONDS


def turn_predates_tracing(turn: dict) -> bool:
    """Whether this turn was written before the gates left a trace.

    Silence has two meanings and they must not be conflated in EITHER direction:

      · a turn carrying some stamps but not this gate's — the gate should have
        recorded and did not. A real gap, and it deserves attention.
      · a turn carrying no stamps at all — it predates tracing. Nothing is owed, and
        rendering it as a gap would put every historical turn into alarm: the
        migration committing its own defect, in reverse, forever.

    Derived from the turn's own content rather than a date, because date-gating the UI
    hides the distinction instead of stating it (plan §5 / invariant V4).
    """
    t = _d(turn)
    return not any(isinstance(t.get(k), dict) and t.get(k) for k in _STAMP_KEYS)


def turn_refused_inbound(turn: dict) -> bool:
    """The request was refused AT THE DOOR — the inbound guard blocked it.

    No model ever saw the text, so nothing downstream could run: there was no retrieval
    to screen, no answer to ground, nothing to seal. Their silence is STRUCTURAL, not a
    stamping gap, and the distinction matters more here than almost anywhere else: this
    is the single best outcome the guard can produce, and reading six mechanisms as
    `unknown` ("should have recorded and did not") renders a perfectly defended turn as
    a surface full of attention — the false alarm that teaches operators to ignore
    alarms. Same reasoning as `turn_predates_tracing`, different cause.
    """
    ev = _stamp(turn, "guardrail_inbound")
    return bool(ev) and ev.get("action") == "block"


#: Points that CANNOT have run once the request was refused at the door. The inbound
#: guard itself is excluded (it is the thing that acted), as are the readings that remain
#: legitimately available on a refused turn — the review disposition, the agent's
#: trailing record, and the authority the turn would have carried.
_POST_DOOR_POINTS = frozenset({
    "trusted_scope", "preflight_sql", "tool_enforcement", "corpus_quarantine",
    "sensitivity_ceiling", "retrieval_firewall", "outbound_guard", "grounding_gate",
    "honesty_gate", "governed_data", "evaluation", "provenance_seal", "agent_integrity",
    "prompt_cache", "compute_lineage", "llm_recovery", "turn_budget", "tool_surface", "outcome_loop",
    "attachment_sources", "actions",
})

#: What each of them honestly reads on a refused turn. One sentence, naming the cause —
#: never the generic "not turned on", which would claim the protection is off.
_REFUSED_WORD = "refused at the door"


import dataclasses as _dc


def read_turn(turn: dict, profile_class: str = "tool_enabled") -> List[Reading]:
    """Every applicable point's state for one turn, in path order.

    Defensive by construction: a point whose reader raises yields `unknown` rather
    than removing itself from the path. A silently missing gate is the exact defect
    this registry exists to prevent, so a broken reader must still occupy its place.
    """
    out: List[Reading] = []
    for p in points():
        if not p.applies(profile_class):
            # Shown, never dropped: the row says it is not part of this class and where
            # it runs, in IFOC words. `na` keeps it out of the station's worst-of.
            classes = tuple(dict.fromkeys(IFOC_LABEL.get(c, c) for c in sorted(p.applies_to)))
            out.append(Reading(key=p.key, label=p.label, station=p.station, state="na",
                               detail=f"{p.label}: not part of this class — runs for "
                                      f"{', '.join(classes)} agents",
                               word="not part of this class", applies=False, classes=classes))
            continue
        try:
            r = p.reader(turn, profile_class)
            if not isinstance(r, Reading) or r.state not in STATES:
                raise TypeError(f"reader for {p.key} returned {r!r}")
        except Exception as e:  # noqa: BLE001 — never break the surface
            r = _mk(p.key, p.label, p.station, "unknown",
                    f"{p.label}: could not be read from this turn ({type(e).__name__})")
        out.append(r)

    # Attach each reading's dimension context: the dimensions that measure it, carrying
    # the band they actually scored at on this turn. One join, at one seam, so no reader
    # has to know about scoring and no two readers can disagree about a band.
    scored = scored_dimensions(turn)
    if scored:
        scored, disputes = apply_refinements(scored)

        joined = []
        for r in out:
            if r.dims:                       # `evaluation` already carries the full set
                joined.append(r); continue
            keys = POINT_DIMENSIONS.get(r.key) or ()
            # `st` — the contribution, resolved ONCE here so no renderer converts a band.
            picked = tuple(dict(scored[k], st=dimension_state(scored[k]),
                                bst=band_state(scored[k]))
                           for k in keys if k in scored)
            if not picked:
                joined.append(r); continue
            detail = r.detail
            # Name the disagreement on the mechanism that owns the pair — a disputed
            # reading the operator cannot see the reason for is a composite with
            # invisible arithmetic wearing a reading's clothes.
            for coarse, _judge, reading in disputes:
                if coarse in keys:
                    detail = f"{detail}\n{REFINEMENT_NOTE[reading]}"
            # `replace`, not a fresh Reading: every field this reader does not touch
            # (applies, classes, modules, guide) survives — a rebuild once silently reset
            # `applies` to True, which is how a dropped mechanism came back as applicable.
            joined.append(_dc.replace(r, detail=detail, dims=picked))
        out = joined

    # Every listed mechanism carries its own Guide entry — the rule is that any item the
    # instrument shows must be lookupable, including the ones no single dimension scores.
    out = [_dc.replace(r, guide=guide_entry(r.key)) for r in out]

    # V4 — absent evidence is NAMED, never assumed. On a pre-tracing turn an unknown is
    # not a gap, so it reads `na` WITH ITS REASON rather than "not turned on"; the
    # honesty lives in the words (Rule 8.3), which is why this needs no fifth state.
    if turn_predates_tracing(turn):
        out = [Reading(r.key, r.label, r.station, "na",
                       f"{r.label}: not recorded on this turn", r.acted)
               if r.state == "unknown" else r
               for r in out]

    # Refused at the door: the same principle, a different cause. A gate that never had
    # an answer to act on did not fail to record — it had nothing to record, and saying
    # so is what keeps the best possible outcome from rendering as six unknowns.
    if turn_refused_inbound(turn):
        out = [Reading(r.key, r.label, r.station, "na",
                       f"{r.label}: the request was refused before the agent ran — "
                       f"this layer never had an answer to act on",
                       r.acted, dims=(), guide=r.guide, word=_REFUSED_WORD)
               if (r.key in _POST_DOOR_POINTS and r.state in ("unknown", "pending", "na"))
               else r
               for r in out]
    return out


def read_turn_by_station(turn: dict, profile_class: str = "tool_enabled"
                         ) -> Dict[str, List[Reading]]:
    """The same readings, grouped for the ring — a station is a VIEW over its
    mechanisms (contract invariant V1), never a replacement for them."""
    import dataclasses as _dc
    grouped: Dict[str, List[Reading]] = {s: [] for s in STATIONS}
    for r in read_turn(turn, profile_class):
        mods = active_modules(r.key, turn)
        if mods:
            r = _dc.replace(r, modules=tuple(mods))
        grouped.setdefault(r.station, []).append(r)
    return grouped


def worst_state(readings: List[Reading]) -> str:
    """Attention priority for a group — worst-of, matching the ring's aggregation.

    `unknown` outranks `pass`: a gate whose outcome the turn does not record must
    never be summarised as though it had passed.
    """
    order = ("danger", "warn", "unknown", "pending", "pass", "na")
    seen = {r.state for r in readings}
    for s in order:
        if s in seen:
            return s
    return "na"
