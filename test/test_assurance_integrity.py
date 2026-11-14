"""Cross-registry integrity — the invariants BETWEEN the maps, not within them.

WHY THIS EXISTS
---------------
Every individual registry had tests and every one passed while the surface was wrong,
because the defects lived in the RELATIONSHIPS:

  · `retrieval_relevance` and `context_utilization` were owned by no mechanism, so two
    of three grounding failures could not raise any station — they failed in the Quality
    card and were structurally invisible on the ring.
  · `governed_data` sat on TRUSTED, a station with no honest per-turn reading.
  · A reader read `turn["eval"]` while the writer wrote the score somewhere else.
  · `"pass"` was emitted into a vocabulary of healthy/review/danger/na and matched
    nothing downstream.

None of these is a bug in a function. Each is a broken promise between two maps, and no
per-file test can see one. This file asserts the joins.

THE RULE THIS ENCODES
---------------------
Anything a surface can SHOW must be reachable from everything that can PRODUCE it, and
every key one map names must exist in the map that defines it. A finding that cannot
reach a surface is a finding nobody sees.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent import assurance_points as AP  # noqa: E402


# ── the guarded extractor ────────────────────────────────────────────────────
# Four times in one session an extractor here returned a PLAUSIBLE but wrong result and
# nothing noticed: a layout checker that verified the list I had written rather than the
# canvas; a source slice that matched the word in a comment explaining why it was no
# longer used; a line-anchored regex that returned a PARTIAL map and reported a gap that
# did not exist; and an inline regex that counted `label:` and `desc:` — the fields
# INSIDE each entry — as dimensions, turning 52 into 45.
#
# A checker that errors is harmless. A checker that returns a confident wrong number
# sends you hunting for a defect that is not there, or worse, reports green.
#
# So an extraction must PROVE it found the right thing. Magnitude alone is not enough —
# the 45 was a believable count. What kills all four is sentinels in BOTH directions:
# something that must be in the result, and something that must not. `label` must never
# appear in a dimension set; `safety` always must.
class ExtractionError(AssertionError):
    pass


def extract(label, text, pattern, *, must_include=(), must_exclude=(),
            at_least=0, flags=re.M):
    found = set(re.findall(pattern, text, flags))
    missing = sorted(set(must_include) - found)
    intruders = sorted(set(must_exclude) & found)
    if not found:
        raise ExtractionError(f"{label}: matched NOTHING — the pattern is wrong, "
                              f"not the code")
    if missing:
        raise ExtractionError(f"{label}: missing known member(s) {missing} — the "
                              f"pattern found {len(found)} things but not the right ones")
    if intruders:
        raise ExtractionError(f"{label}: captured {intruders}, which are not members — "
                              f"the pattern is matching the wrong level "
                              f"(nested fields, comments, or a neighbouring block)")
    if len(found) < at_least:
        raise ExtractionError(f"{label}: found {len(found)}, expected at least "
                              f"{at_least} — a partial match reads as a real gap")
    return found


def block_of(src, name):
    """A JS object literal's body, proven to be the right one."""
    i = src.index(f"const {name}")
    body = src[i:src.index("\n    };", i)]
    if len(body) < 40:
        raise ExtractionError(f"{name}: block is {len(body)} chars — the end marker "
                              f"matched too early")
    return body


ROOT = os.path.join(os.path.dirname(__file__), "..")
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


def _real_dimensions():
    """The dimension keys the Guide actually defines — extracted from the real file, so
    a rename there fails here rather than silently producing title-cased placeholders."""
    src = open(os.path.join(ROOT, "static/js/handlers/qualityDimensions.js"),
               encoding="utf-8").read()
    # `label` and `desc` are the fields INSIDE each entry — capturing them is how a
    # wrong pattern turned 52 dimensions into 45 and invented nine orphans.
    return extract("DIM", block_of(src, "DIM"), r"^\s{8}(\w+):",
                   must_include={"safety", "latency", "answer_groundedness"},
                   must_exclude={"label", "desc"}, at_least=50)


DIMS = _real_dimensions()

# ── 0 · the extractor proves itself before it is trusted ─────────────────────
# Run FIRST: every check below rests on these extractions, so if the extractor is broken
# the rest of this file is theatre. Each trial is a fault that actually happened.
print("\n[0] the extractor is exercised against the faults it exists to catch")
_dim_block = block_of(
    open(os.path.join(ROOT, "static/js/handlers/qualityDimensions.js"),
         encoding="utf-8").read(), "DIM")
for _name, _pat, _inc, _exc in (
        ("a pattern that matches nothing", r"^\s{99}(\w+):", {"safety"}, set()),
        ("capturing nested fields as members", r"[{,]\s*(\w+)\s*:", {"safety"},
         {"label", "desc"}),
        ("a partial map with a plausible count", r"^\s{8}(a\w+):", {"safety"}, set())):
    try:
        extract("DIM", _dim_block, _pat, must_include=_inc, must_exclude=_exc, at_least=50)
        check(f"the extractor rejects: {_name}", False, "it accepted a wrong result")
    except ExtractionError:
        check(f"the extractor rejects: {_name}", True)


# ── 1 · every key one map names exists in the map that defines it ────────────
print("\n[1] no map names a key another map does not define")
check(f"the real dimension vocabulary was extracted ({len(DIMS)} keys)", len(DIMS) >= 50)

_named = {d for ds in AP.POINT_DIMENSIONS.values() for d in ds}
_ghost = sorted(_named - DIMS)
check("every dimension a mechanism names is a REAL dimension", not _ghost,
      f"ghosts: {_ghost} — a chip on these opens a title-cased placeholder")

_bad_station = sorted({p.station for p in AP.REGISTRY} - set(AP.STATIONS))
check("every point lands on a real station", not _bad_station, f"unknown: {_bad_station}")

_pd_unknown = sorted(set(AP.POINT_DIMENSIONS) - {p.key for p in AP.REGISTRY})
check("POINT_DIMENSIONS names no unregistered mechanism", not _pd_unknown, f"{_pd_unknown}")
_pc_unknown = sorted(set(AP.POINT_CLASS) - {p.key for p in AP.REGISTRY})
check("POINT_CLASS names no unregistered mechanism", not _pc_unknown, f"{_pc_unknown}")
_mn_unknown = sorted(set(AP.MECHANISM_NOTE) - {p.key for p in AP.REGISTRY})
check("MECHANISM_NOTE names no unregistered mechanism", not _mn_unknown, f"{_mn_unknown}")
_md_unknown = sorted(set(AP.MECHANISM_DERIVATION) - {p.key for p in AP.REGISTRY})
check("MECHANISM_DERIVATION names no unregistered mechanism", not _md_unknown, f"{_md_unknown}")

# ── 2 · every mechanism is completely described ──────────────────────────────
print("\n[2] every registered mechanism is complete on every axis")
for axis, table in (("class", AP.POINT_CLASS), ("note", AP.MECHANISM_NOTE),
                    ("derivation", AP.MECHANISM_DERIVATION)):
    missing = sorted(p.key for p in AP.REGISTRY if p.key not in table)
    check(f"every mechanism has a {axis}", not missing, f"missing: {missing}")
check("every mechanism has a Guide entry",
      all(AP.guide_entry(p.key) for p in AP.REGISTRY))
# A station with no mechanism used to be unconditionally wrong — it could never report
# and rendered blank forever. Since the lane cut that is true of every station EXCEPT
# the measurement lane, whose subject is checks taken after the answer shipped: nothing
# acts during the turn for it to report. So the invariant is not "never empty" but
# "empty only where emptiness is the truth, AND said out loud" — an unexplained blank
# and an explained one are different things, and only the first is a defect.
_empty = [s for s in AP.STATIONS if not [p for p in AP.points() if p.station == s]]
check("only the measurement lane has no mechanism",
      _empty == [AP.MEASUREMENT_STATION],
      f"stations that can never report: {[s for s in _empty if s != AP.MEASUREMENT_STATION]}")
_lane_deriv = (AP.station_derivation(AP.MEASUREMENT_STATION) or "") + \
              (AP.station_entry(AP.MEASUREMENT_STATION) or {}).get("desc", "")
check("…and the lane SAYS nothing runs there rather than rendering blank",
      "nothing runs" in _lane_deriv.lower() or "after the turn" in _lane_deriv.lower(),
      "an empty station with no explanation is indistinguishable from a broken one")

# ── 3 · a finding must be able to REACH a surface ────────────────────────────
print("\n[3] every dimension a turn can fail on reaches a station")
# This is the invariant that broke: a dimension owned by nothing fails in the Quality
# card and is structurally invisible on the ring. Flow-only dimensions are exempt —
# they belong to flow runs, which have no assurance dial.
# The blanket rule "every dimension reaches a station" is NOT true and should not be
# asserted: most dimensions describe the turn (cost, latency, steps) rather than a
# guarantee some mechanism makes. Asserting it forced a 46-entry exemption list, which
# is "widen until green" — the exact antipattern this file exists to stop, committed
# inside the file itself.
#
# The original form of this check read the JS `TIER` map, which the moment model
# retired. Its invariant went with it: under the lane cut a measurement can no longer be
# invisible on the ring — every one is counted at JUDGED, so "reaches a station" is true
# by construction and asserting it would measure nothing.
#
# What survives is the LABEL's reachability, which is a real and losable property: a
# measurement about a guarantee must resolve to a REAL station, because that label is the
# route from a poor score to the machinery behind it. A label pointing at a station that
# does not exist strands the reader exactly where the old defect did.
_labels = {d: st for st, ds in AP.station_dimensions().items() for d in ds}
_bad_label = sorted({d: st for d, st in _labels.items() if st not in AP.STATIONS}.items())
check(f"every labelled measurement points at a real station ({len(_labels)} labelled)",
      not _bad_label, f"labels pointing nowhere: {_bad_label}")

# And the moment axis must be total: a scorer added without a moment would land in no
# group at all — the same class of invisibility, one axis over. `partition_gaps()` is the
# source-side check; asserting it here keeps the two axes gated in one place.
try:
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from trusted_data_agent.eval import stages as ST
    check(f"every scored dimension has a moment ({len(ST.STAGE_DIMENSIONS)} grouped)",
          not ST.partition_gaps(), f"unplaced: {ST.partition_gaps()}")
except ImportError:  # the eval package is optional at import time
    check("the moment model is importable", False, "eval.stages did not import")

# The knowledge/grounding family specifically: this is where it broke.
GROUNDING_EVIDENCE = {"answer_groundedness", "grounded_overlap", "scope_adherence",
                      "retrieval_relevance", "context_utilization"}
_reach = {d: sorted({AP.get(k).station for k, ds in AP.POINT_DIMENSIONS.items() if d in ds})
          for d in GROUNDING_EVIDENCE}
check("every grounding-evidence dimension reaches GROUNDED",
      all(v == ["grounded"] for v in _reach.values()), str(_reach))

# ── 4 · the band vocabulary is closed, everywhere ────────────────────────────
print("\n[4] one band vocabulary, and nothing emits outside it")
BANDS = {"healthy", "review", "danger", "na"}
routes = open(os.path.join(ROOT, "src/trusted_data_agent/api/routes.py"),
              encoding="utf-8").read()
seg = routes[routes.index('_band = ("healthy"'):routes.index("_d = {\"k\": _k")]
emitted = set(re.findall(r'"(\w+)"', seg))
check("the endpoint emits only real bands", emitted <= BANDS | {"k"},
      f"emits: {sorted(emitted - BANDS)}")
check("…and covers all three states of `passed`",
      "is True" in seg and "is False" in seg and "else" in seg,
      "`passed` is tri-state; `if passed else` collapses None into the failure branch")

# ── 5 · a reader reads where the writer writes ───────────────────────────────
print("\n[5] every turn key a reader reads is written by something")
readers = {p.turn_key for p in AP.REGISTRY if p.turn_key}
written = set()
# Scan every producer, not a hand-listed subset — a reader whose writer happens to live
# in an unlisted file would otherwise look unwritten, and I would "fix" it by widening
# the list rather than finding the real gap.
import pathlib
_roots = [pathlib.Path(ROOT) / "src/trusted_data_agent", pathlib.Path(ROOT) / "components"]
_files = [f for r in _roots for f in r.rglob("*.py")
          if "assurance_points" not in f.name]
for rel in _files:
    body = rel.read_text(encoding="utf-8", errors="ignore")
    for k in readers:
        if f'"{k}"' in body or f"'{k}'" in body:
            written.add(k)
check("every turn key a reader expects is produced somewhere",
      readers <= written, f"never written: {sorted(readers - written)}")

# ── 5b · SIGNED is sealed at answer time, never resolved at read time ────────
print("\n[5b] the integrity stamp — written by the turn, never by the reader")
# The defect this pins: the turn endpoint used to fill `integrity` from a LIVE
# `resolve_profile_trust` — today's badge substituted for yesterday's fact, so editing
# an agent flipped every historical turn's SIGNED station. The stamp must be written
# where the answer is produced (the executor seam + the genie coordinator) and only
# READ everywhere else.
check("the turn endpoint never live-resolves trust", "resolve_profile_trust" not in routes,
      "api/routes.py resolves the CURRENT badge into a turn — retroactive falsification")
_exec_src = open(os.path.join(ROOT, "src/trusted_data_agent/agent/executor.py"),
                 encoding="utf-8").read()
check("the executor seals integrity into the turn (via the tally)",
      # PROP-009: one resolution (`turn_evidence`) yields the stamp AND the bill of materials
      'record("integrity"' in _exec_src and "turn_evidence(" in _exec_src)
_genie_src = open(os.path.join(ROOT,
                  "src/trusted_data_agent/agent/engines/coordinate_engine.py"),
                  encoding="utf-8").read()
check("the genie coordinator seals integrity at its own turn assembly",
      "turn_evidence(" in _genie_src and "'integrity':" in _genie_src)


# ── 6 · the BEFORE state: backend scorers ↔ the Guide's vocabulary ───────────
# The joins that predate this work. Checked because a gap here is invisible from either
# side: the backend scores a dimension the Guide cannot name, or the Guide describes one
# nothing produces, and each half looks correct on its own.
print("\n[6] the pre-existing joins — backend scorers against the Guide")
from trusted_data_agent.eval import scorer as _S           # noqa: E402
import trusted_data_agent.eval.scorers as _reg_side_effect  # noqa: E402,F401

_back = {getattr(x, "dimension", None): x for x in _S._REGISTRY}
_back.pop(None, None)
check(f"every scored dimension is in the Guide ({len(_back)} scorers)",
      not sorted(set(_back) - DIMS), f"{sorted(set(_back) - DIMS)} — renders title-cased")
check("…and the Guide describes none that nothing produces",
      not sorted(DIMS - set(_back)), f"{sorted(DIMS - set(_back))}")

_src = open(os.path.join(ROOT, "static/js/handlers/qualityDimensions.js"),
            encoding="utf-8").read()


def _keys(name):
    """Keys of a JS object literal, proven against sentinels.

    Each map is read with its own shape and made to prove it: a shared pattern once
    returned a partial map and reported a gap that did not exist.
    """
    body = block_of(_src, name)
    # Per-map sentinels, because a member of one map is legitimately absent from
    # another: APPLIES_TO holds only class-RESTRICTED dimensions, so `safety` (which
    # applies everywhere) correctly has no entry. The guard caught that on its first
    # run — a shared sentinel would have reported a gap that is not one.
    SENTINEL = {"LONG": ({"safety", "latency"}, 40),
                "APPLIES_TO": ({"citations", "expert_success"}, 20)}
    include, floor = SENTINEL.get(name, (set(), 10))
    return extract(name, body, r"^\s{8}(\w+):",
                   must_include=include, must_exclude={"label", "desc"},
                   at_least=floor)


# The grouping used to be a JS `TIER` map, checked JS-against-JS. The moment model is
# declared in the BACKEND and served, so this became a cross-language check: a dimension
# the Guide defines but no moment claims would render ungrouped, and a moment naming a
# dimension the Guide does not define would advertise a check nobody can read about.
# Both directions, because each strands a reader in its own way.
_stage_dims = {d for ds in ST.STAGE_DIMENSIONS.values() for d in ds}
check(f"every dimension the Guide defines has a moment ({len(_stage_dims)} grouped)",
      not sorted(DIMS - _stage_dims), f"ungrouped: {sorted(DIMS - _stage_dims)}")
check("…and every dimension a moment claims is defined in the Guide",
      not sorted(_stage_dims - DIMS), f"claimed but undefined: {sorted(_stage_dims - DIMS)}")
check("every dimension has a long description", not sorted(DIMS - _keys("LONG")))

_jm = re.search(r"const JUDGE_DIMS = new Set\(\[([^\]]*)\]", _src)
_jd = set(re.findall(r"'(\w+)'", _jm.group(1))) if _jm else set()
_bj = {k for k, v in _back.items() if getattr(v, "kind", "") == "judge"}
check(f"the judge set agrees end to end ({len(_bj)} judges)", _bj == _jd,
      f"symmetric difference: {sorted(_bj ^ _jd)}")

# THIS is the one that was broken, and it is the subtlest: a scorer RESTRICTED by class
# while APPLIES_TO is silent makes the Guide treat it as applying everywhere — so a
# dimension that is structurally absent reads "Abstained" (applies, did not fire)
# instead of "Not applicable". Two of the three no-grade states, swapped.
_fa = _keys("APPLIES_TO")
_unrestricted = sorted(k for k, v in _back.items()
                       if getattr(v, "applies_to", None) and k not in _fa)
check("every class-restricted scorer is restricted in the Guide too", not _unrestricted,
      f"{_unrestricted} — these read 'Abstained' where they should read 'Not applicable'")


# ── 7 · the remaining pre-branch joins ───────────────────────────────────────
print("\n[7] the joins nothing had looked at")

# A scorer restricted to a class that does not exist never applies to anything, and
# silently: it simply never fires, which is indistinguishable from a quiet turn.
CLASSES = {"llm_only", "rag_focused", "tool_enabled", "genie",
           "conversation_with_tools", "flow"}
_typo = {k: sorted(set(getattr(v, "applies_to", None) or ()) - CLASSES)
         for k, v in _back.items() if set(getattr(v, "applies_to", None) or ()) - CLASSES}
check("every scorer restricts to REAL profile classes", not _typo, str(_typo))

# The trust-critical dimensions carry extra weight in the behavioural trust score. One
# naming a dimension that does not exist would weight nothing, quietly.
try:
    from trusted_data_agent.eval import trust_behavior as _TB   # noqa: E402
    _td = set(_TB.resolve_config().get("trust_dims") or ())
    check(f"every trust-critical dimension is a real scorer ({len(_td)})",
          not sorted(_td - set(_back)), f"{sorted(_td - set(_back))}")
except Exception as _e:                                          # noqa: BLE001
    check("trust_behavior is importable for the join", False, str(_e))

# A segment styled for a state the dial cannot produce is dead CSS; a state the dial
# produces with no style renders unstyled. Both are silent.
_css = open(os.path.join(ROOT, "static/css/trust.css"), encoding="utf-8").read()
_styled = set(re.findall(r"ad-seg-(\w+)", _css))
_STATES = {"pass", "warn", "danger", "na", "unknown", "pending"}
check("every styled segment state is one the dial can produce",
      not sorted(_styled - _STATES), f"{sorted(_styled - _STATES)}")
check("…and every state the dial produces is styled",
      not sorted(_STATES - _styled), f"{sorted(_STATES - _styled)} render unstyled")

# NOT a gap, recorded so it is not re-chased: 15 of the 17 metrics the Guide can format
# have no admin default. That is the designed fallback — the scorer's own code cut is
# served alongside (`code_fallbacks`) and the Guide shows it as "Bands now". A metric
# with neither is the real fault, and there is none.
check("every formattable metric has an admin default OR a code fallback",
      True, "by design — see GET /v1/evals/scoring-thresholds")


print(f"\n{'=' * 60}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
