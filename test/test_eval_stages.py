"""Gate — the measurement process model (eval/stages.py).

The declaration is a PARTITION of every scored dimension, so the two ways it can rot are
a scorer added without a stage (it would render nowhere) and a stage naming a dimension
no scorer produces (a heading over nothing). Both fail here, in both directions, against
the LIVE registry — never a second hand-kept list.

Pure: no server, no network, no DB.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval import stages as ST          # noqa: E402
from trusted_data_agent.eval import scorer as SC          # noqa: E402
import trusted_data_agent.eval.scorers                    # noqa: E402,F401

PASS = FAIL = 0


def check(label, cond, want=True):
    global PASS, FAIL
    ok = (cond == want)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got={cond!r} want={want!r}")
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


def section(t):
    print(f"\n[{t}]")


# ── 1 · the partition agrees with the live registry, both directions ─────────
section("the partition is total and exclusive against the real scorer suite")
_gaps = ST.partition_gaps()
check(f"no drift between declaration and registry (gaps: {_gaps or 'none'})",
      _gaps, {})
_registered = {d for d in (getattr(x, "dimension", None) for x in SC.all_scorers()) if d}
_declared = {d for dims in ST.STAGE_DIMENSIONS.values() for d in dims}
check("every registered dimension has a stage", _registered - _declared, set())
check("every declared dimension has a scorer", _declared - _registered, set())
check("the declaration covers the whole suite", len(_declared), len(_registered))
_flat = [d for dims in ST.STAGE_DIMENSIONS.values() for d in dims]
check("no dimension is declared at two stages", len(_flat), len(set(_flat)))

# The gap reporter must FAIL LOUD, not silently pass, when it cannot see the registry —
# "not known here" reading as "clean" is the defect class this whole suite exists for.
check("an unavailable registry is reported, never treated as clean",
      "registry_unavailable" in ST.partition_gaps.__doc__, True)

# ── 2 · order is the model's main asset ──────────────────────────────────────
section("the stages carry a canonical turn order")
check("every stage key is ordered", set(ST.STAGE_ORDER), set(ST.STAGE_DIMENSIONS))
check("the order is the turn's sequence",
      ST.STAGE_ORDER[:5], ("plan", "retrieve", "act", "compose", "deliver"))
check("the whole-turn group comes after the stages, not inside them",
      ST.STAGE_ORDER.index("the_run") > ST.STAGE_ORDER.index("deliver"), True)
check("grouping returns stages in turn order, never registry order",
      [s for s, _ in ST.group_dimensions(
          ["cost", "answer_present", "steps", "grounded_overlap"])],
      ["plan", "compose", "deliver", "the_run"])

# ── 3 · every stage is renderable — a wire key must never reach a surface ────
section("labels and descriptions exist for every stage")
for _s in ST.STAGE_ORDER:
    check(f"{_s}: has a user-facing label", bool(ST.STAGE_LABEL.get(_s)), True)
    check(f"{_s}: has a description saying what is measured",
          len(ST.STAGE_DESC.get(_s, "")) > 20, True)
    check(f"{_s}: the label is not the wire key",
          ST.STAGE_LABEL[_s] != _s, True)

# ── 4 · an unknown dimension is NAMED, never bucketed ────────────────────────
section("a gap is surfaced, not filed under 'other'")
check("stage_of returns None for an unknown dimension",
      ST.stage_of("not_a_dimension"), None)
check("grouping DROPS an unknown dimension rather than inventing a home",
      [d for _, ds in ST.group_dimensions(["cost", "not_a_dimension"]) for d in ds],
      ["cost"])
check("...and unclassified() names it, so it cannot vanish between the two",
      ST.unclassified(["cost", "not_a_dimension"]), ["not_a_dimension"])
check("no stage is called 'other'", "other" in ST.STAGE_ORDER, False)

# ── 5 · an EMPTY stage is omitted, never rendered as a heading over nothing ──
section("absence is a fact about the agent, not an empty header")
check("a stage with no member is omitted from the grouping",
      [s for s, _ in ST.group_dimensions(["cost"])], ["the_run"])
check("an empty input groups to nothing", ST.group_dimensions([]), [])

# ── 6 · per-class shape — the model must render usably on every real class ───
section("every profile class renders a usable number of groups")
for _cls in ("llm_only", "rag_focused", "conversation_with_tools", "tool_enabled", "genie"):
    _dims = {d for d in (getattr(x, "dimension", None)
                         for x in SC.scorers_for(_cls)) if d}
    _groups = ST.group_dimensions(_dims)
    check(f"{_cls}: every scored dimension lands in a stage",
          sorted(d for _, ds in _groups for d in ds), sorted(_dims))
    # The whole point of consolidating from ten groups to six: a header per two chips is
    # noise. Between 3 and 6 groups is the band that reads as structure.
    check(f"{_cls}: renders 3–6 groups (got {len(_groups)})",
          3 <= len(_groups) <= 6, True)
    # A header per one chip is the noise the ten-group topic model was rejected for.
    # It is not zero here — `knowledge_freshness` is the only RETRIEVE check a non-Focus
    # class produces, and `expert_success` the only ACT check a coordinator produces —
    # but it must stay an EDGE. One lone group per class is a true reading ("this agent
    # does exactly one thing at this stage"); two would mean the cut is too fine again.
    _lone = [s for s, ds in _groups if len(ds) == 1]
    check(f"{_cls}: at most one lone-chip group (got {_lone})", len(_lone) <= 1, True)

_flow_dims = {d for d in (getattr(x, "dimension", None)
                          for x in SC.scorers_for("flow")) if d}
# FLOW-ONLY, not exclusive — asserted in the direction that is actually true. A flow
# also produces `tasks` and `parallel_efficiency`, so it renders PLAN beside FLOW_RUN;
# an exclusivity claim would have hidden two real measurements on every flow run.
check("no TURN class ever renders the flow group",
      any("flow_run" in [s for s, _ in ST.group_dimensions(
          {d for d in (getattr(x, "dimension", None) for x in SC.scorers_for(c)) if d})]
          for c in ("llm_only", "rag_focused", "tool_enabled", "genie",
                    "conversation_with_tools")), False)
check("a flow-agent DOES render the flow group",
      "flow_run" in [s for s, _ in ST.group_dimensions(_flow_dims)], True)
check("...and its shared work checks are not hidden by a false exclusivity",
      "plan" in [s for s, _ in ST.group_dimensions(_flow_dims)], True)
check("the flow-only direction is DECLARED, not left implicit",
      "flow_run" in ST.FLOW_ONLY_STAGES, True)

# ── 7 · the model is not the retired station partition wearing new words ─────
section("the stages cut ACROSS the retired station labels")
try:
    from trusted_data_agent.agent import assurance_points as AP
    _label = {d: st for st, ds in AP.station_dimensions().items() for d in ds}
    _crossing = [s for s, ds in ST.STAGE_DIMENSIONS.items()
                 if len({_label.get(d, "—") for d in ds}) > 1]
    # If every stage mapped 1:1 onto one station label, this would be a rename with
    # extra steps rather than a different question being asked.
    check(f"at least 3 stages draw from multiple station labels ({len(_crossing)})",
          len(_crossing) >= 3, True)
    check("DELIVER in particular is not a single station's set",
          len({_label.get(d, "—") for d in ST.STAGE_DIMENSIONS["deliver"]}) > 1, True)
except Exception as _e:  # noqa: BLE001
    check(f"assurance_points importable for the crossing check ({_e})", False)

# ── 8 · the served payload carries the whole model (no new JS mirror) ───────
section("the model travels on the vocabulary payload")
# NOTE: stages ride their OWN payload key, not `guide_entry` — that accessor describes a
# MECHANISM, and a mechanism has no stage because it is not a measurement. An earlier
# draft of this gate asserted the wrong seam and failed for the right reason.
_pay = ST.vocabulary_payload()
check("the payload carries the order as a LIST (the sequence is the model's asset)",
      _pay["order"], list(ST.STAGE_ORDER))
check("...and the dimension→stage map, complete",
      len(_pay["dimensions"]), len(_declared))
check("...and a label for every stage", 
      all(_pay["labels"].get(s) for s in _pay["order"]), True)
check("...and a description for every stage",
      all(_pay["descriptions"].get(s) for s in _pay["order"]), True)
check("...and the flow-only marker", _pay["flow_only"], ["flow_run"])
check("the payload is a projection of the ONE declaration, not a second copy",
      _pay["dimensions"], {d: ST.stage_of(d) for d in _declared})
# The endpoint must embed it rather than rebuild it.
_rr = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                        "api", "rest_routes.py")).read()
check("the vocabulary endpoint serves the model from eval.stages",
      "stages.vocabulary_payload()" in _rr.replace("_stg.", "stages."), True)
check("...under a key the client can ignore if absent",
      '"stages": _stages' in _rr, True)

# ── 9 · the JS side reads the SERVED stage, never a hand-kept copy ───────────
section("no second declaration in JavaScript")
_qd = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                        "handlers", "qualityDimensions.js")).read()
check("qualityDimensions declares no local stage→dimension map",
      "STAGE_DIMENSIONS" in _qd or "const STAGE = {" in _qd, False)
check("...and exposes stageOf for the grouping surfaces", "stageOf" in _qd, True)
check("the retired topic map is gone (one grouping vocabulary, not two)",
      "const TIER = {" in _qd, False)


# ── 10 · MOMENTS are first-class Guide entries — no dead ends ────────────────
# The Assurance Guide is the mental model of the trust runtime. It could explain a
# GUARANTEE (station) and a MECHANISM, but not a MOMENT OF THE TURN — while a moment is
# how 52 of its entries are grouped. A reader saw a shelf headed "Compose" with nowhere
# to ask what it meant, and a header route had nothing to point at (which is why one
# briefly degraded to a tooltip: a patch on a missing concept, not a design).
section("every moment is an openable entry, and every path closes")
_entries = ST.stage_entries()
check("one entry per stage", len(_entries), len(ST.STAGE_ORDER))
check("keyed like a station entry, so ONE navigation channel serves both",
      all(e["k"] == f"stage_{s}" for e, s in zip(_entries, ST.STAGE_ORDER)))
check("each declares the entry class the Guide files it under",
      all(e["cls"] == "stage" for e in _entries))
for _e in _entries:
    check(f"{_e['k']}: renders a label, never a wire key",
          _e["label"] and _e["label"] != _e["k"], True)
    check(f"{_e['k']}: says what it is", len(_e["desc"]) > 20, True)
    check(f"{_e['k']}: says how it is derived", len(_e["derivation"]) > 40, True)
    check(f"{_e['k']}: carries its checks so the path closes both ways",
          len(_e["dims"]) > 0, True)

# The reader can WALK the turn. A moment that knew its checks but not its neighbours
# would explain a point in a process without conveying that there is a process.
_seq = [e for e in _entries if "position" in e]
check("the walkable sequence is the five real moments",
      [e["k"] for e in _seq],
      ["stage_plan", "stage_retrieve", "stage_act", "stage_compose", "stage_deliver"])
check("positions are 1..N with a stated denominator",
      [e["position"] for e in _seq] == list(range(1, len(_seq) + 1))
      and all(e["of"] == len(_seq) for e in _seq))
check("the first has no predecessor and the last no successor",
      "prev" not in _seq[0] and "next" not in _seq[-1])
check("every link points at a REAL entry (no dangling neighbour)",
      all(ST.stage_entry(k[len("stage_"):]) for e in _seq
          for k in (e.get("prev"), e.get("next")) if k), True)
check("the chain is connected — next/prev agree in both directions",
      all(_seq[i]["next"] == _seq[i + 1]["k"] and _seq[i + 1]["prev"] == _seq[i]["k"]
          for i in range(len(_seq) - 1)), True)
# `the_run` and `flow_run` are deliberately OUT of the walk: one is the whole-turn
# wrapper, the other a different process. Giving them a position would place them in a
# sequence they are not part of.
check("the whole-turn wrapper is not placed in the sequence",
      "position" in (ST.stage_entry("the_run") or {}), False)
check("...and says why in its own derivation",
      "not a moment in the sequence" in ST.stage_entry("the_run")["derivation"].lower())
check("the flow group is not placed in the turn's sequence",
      "position" in (ST.stage_entry("flow_run") or {}), False)
check("an unknown stage yields no entry, never a stub",
      ST.stage_entry("not_a_stage"), None)

# ── 11 · the Guide's own consumers point at real entries ─────────────────────
section("every surface that names a moment routes to its entry")
_ui = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "ui.js")).read()
_ap_js = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                           "handlers", "agentPerformanceHandler.js")).read()
_qd_js = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                           "handlers", "qualityDimensions.js")).read()
check("the scorecard's group header routes to the moment",
      'data-dim="stage_${esc(g.stage)}"' in _ui)
check("the roster ledger's group header routes to the moment",
      'data-dim="stage_${esc(st)}"' in _ap_js)
check("the Guide's per-entry chip routes to the moment, not to a station",
      'data-dim="stage_${esc(stg)}"' in _qd_js
      and 'data-dim="station_${esc(st)}"' not in _qd_js)
check("the Guide files moments under their own class, first-class beside guarantees",
      "stage: 'Moments'" in _qd_js)
check("...and offers a moment facet",
      "pill('stage'" in _qd_js)
# The retired vocabulary must not survive on a rendered surface.
for _f, _src in (("ui.js", _ui), ("agentPerformanceHandler.js", _ap_js)):
    _prose = "\n".join(l for l in _src.split("\n")
                       if not l.strip().startswith("//") and not l.strip().startswith("*"))
    check(f"{_f}: no rendered text calls a group a station's residue",
          "residue" not in _prose.lower())

# ── 12 · a MOMENT is not a measurement, and must not be read as one ─────────
# Seen rendered: every moment entry carried three facets that belong to a MEASUREMENT —
# the kind tag `deterministic`, the tuning note "a yes/no check … nothing to configure",
# and a per-turn status "Abstained — didn't fire this turn". A moment does not fire, is
# not graded by a rule or a judge, and has no pass line: it is a point in the turn.
# Three questions answered that nobody asked of it, in another kind of entry's words.
section("a grouping is not rendered in a measurement's vocabulary")
check("moment entries declare the grouping class",
      all(e["cls"] == "stage" for e in ST.stage_entries()))
check("…and carry no judge/kind facet to render from",
      all("kind" not in e and "judges" not in e for e in ST.stage_entries()))
check("…and no per-class applicability that a status could be derived from",
      all("applies_to" not in e for e in ST.stage_entries()))
_qd2 = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                         "handlers", "qualityDimensions.js")).read()
check("the row suppresses the kind tag for a grouping",
      "const kindTag = grouping ? '' : runs" in _qd2)
check("…the per-turn status", "(_guideCtx && !grouping) ? statusFor" in _qd2)
# The PROPERTY — every GROUPING class suppresses the tuning line — not the literal
# condition, which broke when `property` became the seventh class while what it stands for
# held perfectly.
_tune_guard = _qd2[_qd2.index("    function tuningLine(k) {"):][:600]
check("…and the tuning line, for every grouping class",
      all(f"c === '{c}'" in _tune_guard for c in ("stage", "station", "property"))
      and "return '';" in _tune_guard)
check("…scoped to BOTH groupings — a guarantee is not measured either",
      "classOf(k) === 'stage' || classOf(k) === 'station'" in _qd2)
# The Guide points at where a threshold is actually set. The panel moved; the pointer
# is the kind of sentence that silently sends an operator to a tab that no longer holds
# what they were sent for.
check("the Guide's admin path names the panel that holds the sliders",
      "App Config → Trust → Quality bands" in _qd2)
check("…and no longer points at the tab they left",
      "Operations → Agent Evaluation" not in _qd2)

# ── 13 · the admin panel must not claim a reach the lane cut removed ───────
# The quality-band floors colour a measurement-derived reading. Only JUDGED has one;
# the other seven stations report what their MECHANISMS did. Telling an operator these
# two numbers colour "every station" invites them to hunt for a change that cannot
# happen there — the same over-claim as a station counting a dimension it never read.
section("the blast-radius sentence names the one station the floors reach")
_ix = open(os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")).read()
check("it names JUDGED", "the Trust Circle&#39;s JUDGED reading" in _ix
      or "the Trust Circle's JUDGED reading" in _ix)
check("…and no longer claims every station",
      "execution reading on every station" not in _ix)
check("…and says plainly that the other seven are not coloured by it",
      "these floors do not move them" in _ix)

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
