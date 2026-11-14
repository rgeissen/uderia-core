"""The intervention registry is the declaration; every surface is a view over it.

These assertions exist because the surface it replaces was hand-enumerated and left
six real gates invisible. The gate below makes that failure mode structural: a point
without a station, a station key that drifts from the ring's, a reader that returns a
state outside the vocabulary, or a gate that vanishes from a turn reading all fail
here rather than in front of a reader.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

from trusted_data_agent.agent import assurance_points as AP
from trusted_data_agent.eval import stages as ST_STAGES  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}")



class _Readings(dict):
    """Readings by key, where a MISSING key yields a sentinel rather than a KeyError.

    A mutation that drops a point must make this gate fail *by name* — a traceback
    says something broke, not which promise it broke.
    """
    _MISSING = AP.Reading("<missing>", "<missing>", "guarded", "unknown",
                          "this point is not in the registry")

    def __init__(self, readings):
        super().__init__({r.key: r for r in readings})

    def __missing__(self, k):
        return self._MISSING


# ── 1 · structural integrity ──────────────────────────────────────────────────
print("\n[1] every point is well-formed and lands on a real station")
_all = AP.points(include_excluded=True)
check("registry is non-empty", len(_all) >= 15)
check("keys are unique", len({p.key for p in _all}) == len(_all))
check("every station key is one of the ring's eight",
      all(p.station in AP.STATIONS for p in _all))
check("every point declares a power",
      all(p.power in ("alter", "withhold", "constrain", "attest") for p in _all))
check("every point has a reader", all(callable(p.reader) for p in _all))

# The ring's own list — kept literal so a rename on either side fails here.
check("stations mirror the grammar gate's STATION_KEYS",
      AP.STATIONS == ("signed", "grounded", "guarded", "bounded",
                      "scored", "trusted", "reviewed", "proven"))

# A Reading's station is set in TWO places — the AssurancePoint declaration AND the
# reader's `_mk(key, label, STATION, …)` call — and the two can drift: the dial groups a
# live reading by the READER's station while the dimension partition uses the point's.
# `governed_data` drifted exactly this way (AssurancePoint GROUNDED, reader `scored`), so
# the mechanism rendered under JUDGED's "what ran" while its dimension counted at GROUNDED.
# Parse every `_mk` CALL's station and assert it matches its point's declared station.
import inspect as _inspect, re as _re
_src = _inspect.getsource(AP)
_mk_stations = {}
for _m in _re.finditer(r'_mk\(\s*"([a-z_]+)"\s*,\s*"[^"]*"\s*,\s*"([a-z_]+)"', _src):
    _mk_stations.setdefault(_m.group(1), set()).add(_m.group(2))
_pt_station = {p.key: p.station for p in _all}
_station_drift = {k: (sorted(v), _pt_station.get(k)) for k, v in _mk_stations.items()
                  if _pt_station.get(k) and v != {_pt_station[k]}}
check("no reader stamps a station that drifts from its AssurancePoint"
      + (f" — {_station_drift}" if _station_drift else ""), not _station_drift)
check("…and the audit actually SAW the readers (not a dead regex)",
      len(_mk_stations) >= 15 and "governed_data" in _mk_stations)


# ── 2 · the six that were invisible ───────────────────────────────────────────
print("\n[2] the six gates that existed in code and appeared on no surface")
_live = {p.key for p in AP.points()}
for k in ("preflight_sql", "retrieval_firewall", "corpus_quarantine",
          "sensitivity_ceiling", "review_parking"):
    check(f"{k} is registered and live", k in _live)

_mem = AP.get("memory_firewall")
check("memory_firewall is registered", _mem is not None)
check("memory_firewall is a DELIBERATE exclusion, with a reason",
      _mem.excluded is True and len(_mem.exclusion_reason) > 40)
check("an excluded point never appears in a turn reading",
      "memory_firewall" not in {r.key for r in AP.read_turn({})})


# ── 3 · reading a turn ────────────────────────────────────────────────────────
print("\n[3] reading a turn — states come from the stamps, never from silence")

CLEAN = {
    "guardrail_inbound": {"ran": True, "acted": False, "action": "pass"},
    "guardrail": {"ran": True, "acted": False, "action": "pass"},
    "grounding": {"decision": "pass", "ran": True, "acted": False,
                  "scope_databases": ["fitness_db"]},
    "honesty": {"ran": True, "acted": False, "action": "pass"},
    "execution_trace": [{"action": {"tool_name": "base_readQuery"}}],
    "obda": {"lane": "governed", "governed_count": 2, "freeform_count": 0},
    "provenance_meta": {"sealed": True, "chain_tip_hash": "abc123def456789", "step_count": 4},
    "eval": {"failed_dimensions": []},
    "preflight": {"ran": True, "action": "pass"},
    "retrieval_hygiene": {"defused_chunks": 0, "quarantined_excluded": 0},
}
_r = _Readings(AP.read_turn(CLEAN))
check("a clean turn reads pass at the outbound guard", _r["outbound_guard"].state == "pass")
check("a clean turn reads pass at grounding", _r["grounding_gate"].state == "pass")
check("a clean turn reads pass at the seal", _r["provenance_seal"].state == "pass")
check("nothing on a clean turn claims to have acted",
      not any(r.acted for r in _r.values()))

# The P1 distinction is the whole point: off must not read like ran-clean.
OFF = dict(CLEAN, guardrail={"ran": False, "acted": False, "action": "off",
                             "reason": "not enabled for this agent"})
check("a switched-off guard reads `na`, not `pass`",
      _Readings(AP.read_turn(OFF))["outbound_guard"].state == "na")

# And silence must not read like either. (Retired 2026-08: silence read `unknown`;
# production partial-stamp shapes proved absence is path variance, not a gap — see [4b].
# The property that MUST hold is unchanged: silence is never a pass.)
SILENT = dict(CLEAN); SILENT.pop("guardrail")
check("a guard with NO record reads `na`, never `pass`",
      _Readings(AP.read_turn(SILENT))["outbound_guard"].state == "na")

# A failure is not a pass.
ERR = dict(CLEAN, guardrail={"ran": True, "acted": False, "action": "error"})
check("a guard that errored reads `unknown`, never `pass`",
      _Readings(AP.read_turn(ERR))["outbound_guard"].state == "unknown")


print("\n[4] the acting states")
ACTED = dict(CLEAN,
             guardrail={"ran": True, "acted": True, "action": "redact",
                        "categories": ["pii:email"]},
             grounding={"decision": "block", "ran": True, "acted": True,
                        "out_of_scope_dbs": ["payroll_prod"], "scope_databases": ["fitness_db"]},
             honesty={"ran": True, "acted": True, "decision": "block"},
             preflight={"ran": True, "blocked_count": 1, "unknown_dbs": ["nope_db"]},
             retrieval_hygiene={"defused_chunks": 2, "quarantined_excluded": 1,
                                "max_sensitivity": "internal", "sensitivity_excluded": 3})
_a = _Readings(AP.read_turn(ACTED))
check("a redaction reads warn + acted",
      _a["outbound_guard"].state == "warn" and _a["outbound_guard"].acted)
# An OBSERVE-mode catch — the stamp trust campaign 4 captured on S3 (an email and a phone reached
# the answer; the guard recorded them and changed nothing). It read "screened — clean" (C17).
_obs = _Readings(AP.read_turn(dict(CLEAN, guardrail={
    "acted": True, "action": "pass", "categories": ["pii"], "direction": "outbound",
    "findings": [{"category": "pii", "excerpt": "", "label": "email"},
                 {"category": "pii", "excerpt": "", "label": "phone"}],
    "mode": "observe", "ran": True, "reason": "2 PII spans detected (observe)"})))["outbound_guard"]
check("an observe-mode catch reads warn, names the category, and is NOT acted (nothing changed)",
      _obs.state == "warn" and not _obs.acted and "caught pii" in _obs.detail
      and "observe" in _obs.detail and "clean" not in _obs.detail)
check("…and carries the category label only, never the matched value (I9)",
      "john.doe" not in _obs.detail and "555" not in _obs.detail)
check("a withheld answer reads danger + acted",
      _a["grounding_gate"].state == "danger" and _a["grounding_gate"].acted)
check("a rewritten answer reads danger + acted",
      _a["honesty_gate"].state == "danger" and _a["honesty_gate"].acted)
check("a preflight block reads danger and names the database",
      _a["preflight_sql"].state == "danger" and "nope_db" in _a["preflight_sql"].detail)
check("a defused chunk reads warn and says it was KEPT, not dropped",
      _a["retrieval_firewall"].state == "warn"
      and "kept" in _a["retrieval_firewall"].detail.lower())
check("quarantine exclusion reads warn", _a["corpus_quarantine"].state == "warn")
check("the sensitivity ceiling names the ceiling",
      _a["sensitivity_ceiling"].state == "warn"
      and "internal" in _a["sensitivity_ceiling"].detail)

# `acted` means "this point changed what the user saw on this turn" — it is NOT
# derivable from state or power (quarantine merely *constrains* yet withholds
# documents; governed-data *warns* yet changes nothing). So the acting set is
# pinned exactly: a point that quietly stops reporting that it acted, or starts
# claiming it did, fails here.
check("the acting set is exactly the points that changed this answer",
      {r.key for r in _a.values() if r.acted} == {
          "outbound_guard", "grounding_gate", "honesty_gate", "preflight_sql",
          "retrieval_firewall", "corpus_quarantine", "sensitivity_ceiling"})
check("a point that did not change the answer never claims it did",
      not _a["governed_data"].acted and not _a["provenance_seal"].acted
      and not _a["authority"].acted)

PARKED = dict(CLEAN, guardrail={"ran": True, "acted": True, "action": "block",
                                "review_id": 42, "categories": ["pii:ssn"]})
_p = _Readings(AP.read_turn(PARKED))
check("review parking is its own reading when a gate parked",
      _p["review_parking"].state == "warn" and "42" in _p["review_parking"].detail)
check("review parking says nothing was lost",
      "lost" in _p["review_parking"].detail.lower())
check("no parking on an ordinary turn",
      _Readings(AP.read_turn(CLEAN))["review_parking"].state == "na")


# ── 4b · V4: silence is absence, never an alarm ──────────────────────────────
print("\n[4b] V4 — silence reads na (absent), never unknown (alarm); errors alone alarm")
# HISTORY OF THIS RULE. V4 originally drew a two-kinds-of-silence line: a turn with NO
# stamps predates tracing (na), a turn with SOME stamps but not this gate's is "a gap
# that should have recorded" (unknown). The second half was retired 2026-08 against
# production evidence: real turns carry every partial shape — ['grounding'] alone,
# ['guardrail_inbound'] alone, ['honesty'] alone — from benign engine-path variance
# (primer turns, component-only turns, blocked turns, per-engine seams), so
# unknown-on-absence buried whole stations in "not recorded" on ordinary passing turns
# (the OPTIM defect: GROUNDED/GUARDED/BOUNDED all "not recorded" on a clean SQL turn).
# Absence now reads `na` at every reader; the PRECISE gap signal is the gate's OWN
# error stamp (action="error" → unknown), which the failing gate writes itself.
PRE = {"user_query": "x", "execution_trace": [{"action": {"tool_name": "base_readQuery"}}]}
_pre = AP.read_turn(PRE, "tool_enabled")
check("a pre-tracing turn is detected from its own content, not a date",
      AP.turn_predates_tracing(PRE) is True)
check("nothing on a pre-tracing turn reads `unknown`",
      "unknown" not in {r.state for r in _pre})
check("…it reads `na` WITH a reason, never 'not turned on'",
      all(("did not run" in r.detail or "not recorded" in r.detail
           or "no knowledge" in r.detail)
          for r in _pre if r.state == "na" and r.key in
          ("inbound_guard", "outbound_guard", "grounding_gate", "honesty_gate")))
check("a pre-tracing turn does not sit in attention",
      AP.worst_state(_pre) in ("pass", "na"))

GAP = dict(PRE, guardrail={"ran": True, "action": "pass"})
_gap = _Readings(AP.read_turn(GAP, "tool_enabled"))
check("a turn with SOME stamps is not treated as pre-tracing",
      AP.turn_predates_tracing(GAP) is False)
check("…and an absent gate on it reads `na` (path variance, not a recording gap)",
      _gap["grounding_gate"].state == "na")
check("…so a partial-but-clean turn does NOT sit in attention",
      AP.worst_state(list(_gap.values())) in ("pass", "na"))
check("the REAL gap still alarms: a gate that RAN and ERRORED reads `unknown`",
      _Readings(AP.read_turn(dict(GAP, grounding={"action": "error"}),
                             "tool_enabled"))["grounding_gate"].state == "unknown")
check("an empty turn is pre-tracing, not a gap", AP.turn_predates_tracing({}) is True)


# ── 4c · SIGNED replays the sealed fact, never today's badge ─────────────────
print("\n[4c] SIGNED — the at-answer-time integrity stamp, replayed verbatim")
# The stamp is sealed onto the turn WHEN IT RUNS. The registry is pure, so the reading
# is a function of the turn alone — which is itself the guarantee: no live state can
# retroactively rewrite what SIGNED says about a historical answer.
for _st, _want in (("trusted", "pass"), ("changed", "danger"),
                   ("deployment_unattested", "warn"), ("unsigned", "na")):
    _t = dict(CLEAN, integrity={"ran": True, "state": _st})
    check(f"a turn sealed '{_st}' replays {_want}",
          _Readings(AP.read_turn(_t))["agent_integrity"].state == _want)
check("a sealed 'trusted' turn says so in the past tense",
      "when this answer was produced" in
      _Readings(AP.read_turn(dict(CLEAN, integrity={"ran": True, "state": "trusted"})))
      ["agent_integrity"].detail)
# An absent stamp (older turn, or trust suite absent) is honest absence — `na`
# "not recorded", never `unknown` (all of pre-seal history lacks it; alarming it
# would be the migration committing its own defect) and NEVER today's state.
_nostamp = _Readings(AP.read_turn(CLEAN))["agent_integrity"]
check("no stamp reads `na`, not `unknown`", _nostamp.state == "na")
check("…and says 'not recorded', never a live state",
      "not recorded" in _nostamp.detail)
check("an unrecognised sealed state reads `unknown`, never a guess",
      _Readings(AP.read_turn(dict(CLEAN, integrity={"ran": True, "state": "wat"})))
      ["agent_integrity"].state == "unknown")


# ── 5 · applicability ─────────────────────────────────────────────────────────
print("\n[5] class applicability — a point absent by CLASS is not a silent drop")
# Since the legibility follow-up a point outside the class is LISTED and flagged, never
# omitted — the drop made the dial and the Guide disagree about how many mechanisms a
# station has. "Not a silent drop" now means: present, applies=False, state na.
_ideate_r = {r.key: r for r in AP.read_turn(CLEAN, "llm_only")}
check("preflight does not apply to a class with no SQL — listed, flagged",
      "preflight_sql" in _ideate_r and _ideate_r["preflight_sql"].applies is False)
check("tool enforcement does not apply to Ideate — listed, flagged",
      "tool_enforcement" in _ideate_r and _ideate_r["tool_enforcement"].applies is False)
check("the outbound guard applies to every class", _ideate_r["outbound_guard"].applies is True)
check("grounding applies to every class", _ideate_r["grounding_gate"].applies is True)
_opt_r = {r.key: r for r in AP.read_turn(CLEAN, "tool_enabled")}
check("every class lists the same points", set(_opt_r) == set(_ideate_r))
check("Optimize has strictly more APPLYING points than Ideate",
      sum(r.applies for r in _opt_r.values()) > sum(r.applies for r in _ideate_r.values()))


# ── 6 · never breaks the surface ──────────────────────────────────────────────
print("\n[6] a reading can never break the surface that displays it")
for bad in ({}, None, {"grounding": "not a dict"}, {"execution_trace": "nope"},
            {"guardrail": []}, {"obda": 7}):
    try:
        rs = AP.read_turn(bad)
        ok = isinstance(rs, list) and all(r.state in AP.STATES for r in rs)
    except Exception:
        ok = False
    check(f"malformed turn survives: {str(bad)[:34]}", ok)


def _boom(_t, _c):
    raise RuntimeError("reader exploded")


_orig = AP.REGISTRY[0].reader
try:
    object.__setattr__(AP.REGISTRY[0], "reader", _boom)
    _rs = _Readings(AP.read_turn(CLEAN))
    # The point must KEEP its place — a broken reader that removes itself from the
    # path recreates exactly the invisible-gate defect this registry prevents.
    check("a broken reader yields `unknown`, and the point keeps its place",
          AP.REGISTRY[0].key in _rs and _rs[AP.REGISTRY[0].key].state == "unknown")
finally:
    object.__setattr__(AP.REGISTRY[0], "reader", _orig)


# ── 7 · the station view ──────────────────────────────────────────────────────
print("\n[7] a station is a VIEW over its mechanisms (contract invariant V1)")
grouped = AP.read_turn_by_station(CLEAN)
check("every station key is present in the grouping",
      set(grouped) >= set(AP.STATIONS))
_flat = [r.key for rs in grouped.values() for r in rs]
check("grouping loses no reading",
      sorted(_flat) == sorted(r.key for r in AP.read_turn(CLEAN)))
check("no reading is counted twice", len(_flat) == len(set(_flat)))
check("guarded carries more than one mechanism", len(grouped["guarded"]) >= 2)

check("worst-of picks danger over everything",
      AP.worst_state(AP.read_turn(ACTED)) == "danger")
check("worst-of ranks unknown ABOVE pass (silence is not a pass)",
      AP.worst_state([AP.Reading("a", "A", "guarded", "pass", ""),
                      AP.Reading("b", "B", "guarded", "unknown", "")]) == "unknown")
check("worst-of on an all-na group is na",
      AP.worst_state([AP.Reading("a", "A", "guarded", "na", "")]) == "na")


# ── 8 · nothing the old strip showed was lost ─────────────────────────────────
print("\n[8] migration: every stage the Trust Journey strip rendered survives")
# The ten enumerated stages, mapped to their registry key. A row removed here without
# a `dropped` decision in the migration ledger is information loss.
STRIP_STAGES = {
    "inbound":    "inbound_guard",
    "authority":  "authority",
    "scope":      "trusted_scope",
    "tools":      "tool_enforcement",
    "outbound":   "outbound_guard",
    "grounding":  "grounding_gate",
    "honesty":    "honesty_gate",
    "obda":       "governed_data",
    "seal":       "provenance_seal",
}
_missing = [s for s, k in STRIP_STAGES.items() if k not in _live]
check(f"all mechanism-backed strip stages have a registry point ({', '.join(_missing) or 'none missing'})",
      not _missing)
check("the registry strictly EXTENDS the strip (nothing merely renamed)",
      len(_live) > len(STRIP_STAGES))
check("every strip stage's station is a real station",
      all(AP.get(k).station in AP.STATIONS for k in STRIP_STAGES.values()))

# The strip's `evaluation` stage MIGRATED rather than vanished: its representative is
# now the STATION, not a mechanism. `evaluation` was retired because it was a fiction —
# a "mechanism" invented so JUDGED had a row, sitting under a header reading "what ran ·
# acted during the turn" while running after the turn and acting never, and restating a
# summary its own station computed anyway (the two disagreed: warn against danger).
#
# So this asserts the FACT survived, not the row: JUDGED is lookupable, it carries the
# scoring layer's description, and the two readings only that row could state are still
# produced — by `measurement_status`, which is where they belong.
check("the retired mechanism is really gone", AP.get("evaluation") is None)
_js = AP.station_entry(AP.MEASUREMENT_STATION)
check("...and the strip's evaluation stage now resolves to the STATION",
      bool(_js) and _js["k"] == "station_scored")
check("...which explains the scoring layer in its own words",
      "scored check is counted here" in _js["desc"])
check("...and states that nothing runs there — the lane cut, where a reader asks",
      "nothing runs here" in _js["desc"].lower() and not _js["controls"])
for _t, _want in ((({"guardrail": {"ran": True}}), "pending"),
                  ({}, "absent"),
                  ({"eval": {"checks": 0, "dimensions": []}}, "absent"),
                  ({"eval": {"checks": 1, "dimensions": [{"k": "cost", "b": "healthy"}]}}, "scored")):
    check(f"measurement_status still states '{_want}' — the facts only that row could give",
          AP.measurement_status(_t)["state"] == _want)
check("a malformed turn yields absence, never an exception",
      AP.measurement_status("not a turn")["state"] == "absent")


# ── 9 · every listed mechanism is lookupable ─────────────────────────────────
print("\n[9] any item the instrument lists has a representative in the Guide")
_missing = [p.key for p in AP.points() if not AP.guide_entry(p.key)]
check(f"every point has a Guide entry ({', '.join(_missing) or 'none missing'})", not _missing)
check("…each explains HOW its reading is derived",
      all((AP.guide_entry(p.key) or {}).get("derivation") for p in AP.points()))
# A mechanism measured by several dimensions must SAY so rather than name one and imply
# it is the whole story.
_g = AP.guide_entry("grounding_gate")
# The fact rides the `measured` facet (a tag on the entry), not the description: the
# description is the note alone, so no slot restates another.
check("a multi-dimension mechanism SAYS it spans several (the measured facet)",
      _g["measured"] == "composite" and "spans several" not in _g["long"])
check("…and the description is the note alone", _g["long"] == _g["desc"])
# The names ride in `dims` so the Guide can render them as CLICKABLE chips — a reader
# should be able to step into a dimension, read it, and step back. Naming them in the
# sentence instead makes them a dead end, which is the whole reason for the chips.
check("…and carries their keys for chips rather than naming them in prose",
      set(_g["dims"]) >= {"answer_groundedness", "scope_adherence", "grounded_overlap"}
      and "answer_groundedness" not in _g["long"])
# `trusted_scope` was the example here until it gained `kg_seed_utilization` (Sep 2026);
# the property is about ANY single-dimension mechanism, so the example is picked from the
# registry rather than named — a hand-picked example is a pin on today's partition.
_single = [k for k, v in AP.POINT_DIMENSIONS.items() if len(v) == 1]
check("at least one single-dimension mechanism exists to test the rule on", bool(_single))
_t = AP.guide_entry(_single[0]) if _single else {"dims": [], "long": ""}
check("a single-dimension mechanism carries exactly its one key",
      bool(_single) and _t["dims"] == [AP.POINT_DIMENSIONS[_single[0]][0]]
      and AP.POINT_DIMENSIONS[_single[0]][0] not in _t["long"])
# …and the two-dimension mechanism the rule was first written against SAYS it spans several
# (the multi-dimension half of the same rule, on the mechanism that just crossed the line).
_ts = AP.guide_entry("trusted_scope")
check("trusted_scope spans several dimensions and carries every one as a chip",
      set(_ts["dims"]) == set(AP.POINT_DIMENSIONS["trusted_scope"]) and len(_ts["dims"]) >= 2)
check("an unscored mechanism carries no chip keys",
      AP.guide_entry("review_parking")["dims"] == [])
# The note already explains why nothing scores it; repeating that is one slot restating
# another, one level down.
_unscored = AP.guide_entry("review_parking")["long"]
check("an unscored mechanism does not say so twice",
      _unscored.count("no dimension scores it") + _unscored.count("No scored dimension") == 1)
# Every declared dimension must be a REAL one — a link that opens a key the Guide does
# not know renders a title-cased placeholder, which looks like an answer and is not.
_dims = {d for ds in AP.POINT_DIMENSIONS.values() for d in ds}
check(f"every declared dimension key is plausible ({len(_dims)} keys)", len(_dims) >= 12)


# ── 10 · the two facets ──────────────────────────────────────────────────────
print("\n[10] class and measurement are independent facets, not one classification")
check("every registered point has a class", not AP.unclassified_points())
check("classes come from a closed set",
      set(AP.POINT_CLASS.values()) <= {"measure", "control", "boundary", "attestation"})
# The test that makes them two facets rather than five classes: spanning cuts ACROSS
# class. If it did not, it would be a class.
_cross = {}
for p in AP.points():
    _cross.setdefault(AP.POINT_CLASS[p.key], set()).add(AP.measured_as(p.key))
check("every class contains more than one measurement value",
      all(len(v) > 1 for v in _cross.values()))
check("…so spanning is a facet, not a fifth class", len(_cross) == 4)
check("measurement agrees with the declared chip count",
      all(AP.measured_as(p.key) ==
          ("composite" if len(AP.POINT_DIMENSIONS.get(p.key) or ()) > 1
           else "atomic" if len(AP.POINT_DIMENSIONS.get(p.key) or ()) == 1 else "unmeasured")
          for p in AP.points() if p.key not in AP._MEASURED_OVERRIDE))
# The override map is EMPTY now: `evaluation` was its only entry — the maximal composite,
# the union over every applicable dimension — and it is retired (a fiction standing in
# front of JUDGED; see `measurement_status`). The rule survives with no exceptions, which
# is stronger than the rule with one.
check("no mechanism needs a measured-as override any more",
      AP._MEASURED_OVERRIDE == {})
check("every registered point carries a class and a measured facet",
      all((AP.guide_entry(p.key) or {}).get("cls")
          and (AP.guide_entry(p.key) or {}).get("measured") for p in AP.REGISTRY))
# Even the deliberate exclusion: someone who meets the name must be able to look it up.
check("the excluded mechanism is still lookupable, and says why it is excluded",
      "memory_firewall" in AP.MECHANISM_NOTE
      and "future turns" in AP.MECHANISM_NOTE["memory_firewall"].lower())


# ── 11 · a band is tri-state, and `na` is not `danger` ───────────────────────
print("\n[11] an abstained dimension must never render as the most severe reading")
# `passed` is True / False / None. `if passed else` collapses None into the else branch,
# so every dimension that ABSTAINED — including ones whose own description says they
# never convict a turn — was labelled danger, the strongest claim on the surface.
def _band(v):
    b = v.get("band")
    if not b:
        p = v.get("passed")
        b = "healthy" if p is True else "danger" if p is False else "na"
    return b

check("passed → healthy", _band({"passed": True}) == "healthy")
check("failed → danger", _band({"passed": False}) == "danger")
check("abstained (None) → na, NOT danger", _band({"passed": None}) == "na")
check("no verdict at all → na", _band({}) == "na")
check("an explicit band always wins", _band({"band": "review", "passed": True}) == "review")
# "pass" is not in the vocabulary — it matches nothing downstream and falls through.
check("the band vocabulary is exactly the four the Guide knows",
      {_band({"passed": x}) for x in (True, False, None)} <= {"healthy", "review", "danger", "na"})


# ── 12 · a failing dimension must reach a station ────────────────────────────
print("\n[12] a scored failure the ring cannot show is a failure nobody sees")
# Two of three grounding failures reached NO station, so GROUNDED read quiet while
# grounding_overlap, retrieval_relevance and context_utilization were all in danger.
_g = AP.POINT_DIMENSIONS["grounding_gate"]
for _d in ("answer_groundedness", "grounded_overlap", "retrieval_relevance",
           "context_utilization", "scope_adherence"):
    check(f"grounding evidence '{_d}' reaches GROUNDED", _d in _g)
# The knowledge tier is the grounding story; a dimension there owned by nothing is a
# reading with no home on the ring.
_owned = {d for ds in AP.POINT_DIMENSIONS.values() for d in ds}
check("every dimension a mechanism names is non-empty", all(_owned))
check("…and the firewall keeps only what it actually screens for",
      AP.POINT_DIMENSIONS["retrieval_firewall"] == ("safety",))

# The gate's own escalation judges are its evidence too — a gate that can withhold on a
# judge's verdict while that judge's dimension reaches no station is the same defect.
for _d in ("groundedness", "scope_faithfulness"):
    check(f"grounding escalation judge '{_d}' reaches GROUNDED", _d in _g)
check("outbound leak detection reaches GUARDED",
      "output_sanity" in AP.POINT_DIMENSIONS["outbound_guard"])

# ── THE PARTITION (contract Rule 3b.6): every dimension is counted at exactly ONE
# station. The registry derives the partition from mechanism ownership
# (`station_dimensions()`); the ring renders a JS mirror (`STATION_DIMS` in
# agentPerformanceHandler.js). Assert them EQUAL both ways, from source, so neither
# side can move alone — the drift that let `groundedness`/`scope_faithfulness`/
# `output_sanity` be watched by the ring while the registry owned them nowhere.
import re as _re
_hsrc = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                          "handlers", "agentPerformanceHandler.js")).read()

check("no dimension is owned by mechanisms on two different stations "
      f"(conflicts: {AP.partition_conflicts() or 'none'})",
      AP.partition_conflicts() == [])

_sd_block = _hsrc.split("const STATION_DIMS = {", 1)
check("the ring's STATION_DIMS mirror was found in the handler source",
      len(_sd_block) == 2)
_ring_dims = {}
if len(_sd_block) == 2:
    for _m in _re.finditer(r"(\w+):\s*\[([^\]]*)\]", _sd_block[1].split("};", 1)[0]):
        _ring_dims[_m.group(1)] = set(_re.findall(r"'(\w+)'", _m.group(2)))
_reg_dims = {k: set(v) for k, v in AP.station_dimensions().items()}
check("the ring mirrors every station the registry partitions (and no other)",
      set(_ring_dims) == set(_reg_dims))
for _st in sorted(set(_ring_dims) | set(_reg_dims)):
    _a, _b = _ring_dims.get(_st, set()), _reg_dims.get(_st, set())
    check(f"partition '{_st}' identical in ring and registry "
          f"(ring-only: {sorted(_a - _b) or 'none'}; registry-only: {sorted(_b - _a) or 'none'})",
          _a == _b)

# Every owned dimension must be a REAL Guide dimension — a key the Guide cannot
# resolve would render a title-cased placeholder and break its station-home chip.
_qd_src = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                            "handlers", "qualityDimensions.js")).read()
_dim_block = _qd_src.split("const DIM = {", 1)[1].split("\n    };", 1)[0]
_dim_keys = set(_re.findall(r"^\s+([a-z_]+):\s*\{ label", _dim_block, _re.M))
_owned_all = {d for ds in AP.POINT_DIMENSIONS.values() for d in ds}
_ghost = sorted(_owned_all - _dim_keys)
check(f"every owned dimension is a real Guide dimension (ghosts: {_ghost or 'none'})",
      not _ghost)

# MEASUREMENT IS A LANE — JUDGED counts EVERY scored dimension, and it counts them
# DIRECTLY. These assertions moved off the retired `evaluation` mechanism onto the
# station's own two sources: `read_turn_measures` (the reading) and `measurement_status`
# (the absence). Same scenarios; the subject changed because the fiction in front of the
# station was removed, not because the promise did.
_pt = {"eval": {"dimensions": [{"k": "groundedness", "b": "danger"},
                               {"k": "latency", "b": "danger"},
                               {"k": "completed", "b": "healthy"}],
                "checks": 3,
                "failed_dimensions": ["groundedness", "latency"]},
       "guardrail": {"ran": True, "action": "pass"}}
_alloc = AP.read_turn_measures(_pt, "tool_enabled")[AP.MEASUREMENT_STATION]
check("JUDGED counts BOTH failures — the guarantee-labelled one and the unlabelled",
      {d["k"] for d in _alloc} == {"groundedness", "latency", "completed"})
check("…and a guarantee label never keeps a check out of the count",
      "groundedness" in {d["k"] for d in _alloc})
check("…the turn reads as scored, so no absence note stands in for the measurements",
      AP.measurement_status(_pt)["state"] == "scored")
# The rule/judge split survives the move: a RULE in danger convicts, a judge is capped.
check("a rule in danger reaches danger; the judge beside it is capped at warn",
      {d["k"]: d["st"] for d in _alloc}["latency"] == "danger"
      and {d["k"]: d["st"] for d in _alloc}["groundedness"] == "warn")
_pt_lab = {"eval": {"dimensions": [{"k": "grounded_overlap", "b": "danger"}],
                    "checks": 1, "failed_dimensions": ["grounded_overlap"]},
           "guardrail": {"ran": True, "action": "pass"}}
check("a turn whose only failure is guarantee-labelled is STILL a failure here — "
      "under the old residue rule it read a vacuous pass",
      [d["st"] for d in AP.read_turn_measures(_pt_lab, "tool_enabled")[AP.MEASUREMENT_STATION]]
      == ["danger"])
# Still EXCLUSIVE — what separates this from the pre-Aug-2026 union, whose double count
# made the routing summary say "2 need you now" for one root cause.
_excl = AP.read_turn_measures(_pt, "tool_enabled")
check("exclusive: every measurement counts at JUDGED and no other station holds any",
      len(_excl.get(AP.MEASUREMENT_STATION) or []) == 3
      and all(not v for k, v in _excl.items() if k != AP.MEASUREMENT_STATION))
# Rule 3.7 — no vacuous clean. The absence is now the STATION's own, not a mechanism's.
check("an eval block that scored NOTHING does not read clean",
      AP.measurement_status({"eval": {"checks": 0, "dimensions": []},
                             "guardrail": {"ran": True}})["state"] == "absent")
check("...and says so plainly",
      "no check ran" in AP.measurement_status(
          {"eval": {"checks": 0, "dimensions": []}, "guardrail": {"ran": True}})["detail"])


# ── 13 · refused at the door — the best outcome must not read as six gaps ────
print("\n[13] a request refused at the door leaves no gap behind it")
# Campaign finding C2. The inbound guard blocking is the single best outcome it can
# produce: no model ever saw the text. Nothing downstream COULD run — no retrieval to
# screen, no answer to ground, nothing to seal — so their silence is structural. Reading
# them `unknown` ("should have recorded and did not") put GROUNDED and BOUNDED into
# attention on a perfectly defended turn: the false alarm that teaches operators to
# ignore alarms.
_blocked = {"guardrail_inbound": {"ran": True, "action": "block", "acted": True,
                                  "mode": "review", "categories": ["prompt_injection"],
                                  "review_id": "rid-1"},
            "eval": {"checks": 0, "dimensions": [], "failed_dimensions": []}}
check("a blocked inbound turn is detected as refused-at-the-door",
      AP.turn_refused_inbound(_blocked))
check("a clean turn is not", not AP.turn_refused_inbound(
    {"guardrail_inbound": {"ran": True, "action": "pass"}}))
_br = _Readings(AP.read_turn(_blocked, "rag_focused"))
check("no reading is `unknown` on a refused turn",
      not [r for r in AP.read_turn(_blocked, "rag_focused") if r.state == "unknown"])
for _k in ("grounding_gate", "outbound_guard", "retrieval_firewall", "honesty_gate",
           "corpus_quarantine", "provenance_seal", "agent_integrity"):
    check(f"{_k} reads structural absence, not a gap", _br[_k].state == "na")
    check(f"{_k} names the cause rather than 'not turned on'",
          "refused before the agent ran" in _br[_k].detail
          and _br[_k].word == AP._REFUSED_WORD)
# The guard that DID act keeps its finding, and the human work stays visible.
check("the inbound guard keeps its danger reading", _br["inbound_guard"].state == "danger")
check("...and is still marked as having acted", _br["inbound_guard"].acted)
check("the parked review item still surfaces", _br["review_parking"].state == "warn")
_grp = AP.read_turn_by_station(_blocked, "rag_focused")
_st = {k: AP.worst_state(v) for k, v in _grp.items()}
check("GUARDED carries the attention", _st["guarded"] == "danger")
check("REVIEWED shows the human work", _st["reviewed"] == "warn")
check("GROUNDED does not alarm on a turn it never saw", _st["grounded"] == "na")
check("BOUNDED likewise", _st["bounded"] == "na")
check("PROVEN likewise", _st["proven"] == "na")

# C3/C4 (campaign) — JUDGED's own sentence.
check("the pass branch reads as one sentence, not a clause plus a count",
      "owns passed of" not in _Readings(AP.read_turn(
          {"eval": {"checks": 3, "dimensions": [{"k": "cost", "b": "healthy"}],
                    "failed_dimensions": []},
           "guardrail": {"ran": True, "action": "pass"}}))["evaluation"].detail)
# The Rule 3.7 no-vacuous-clean promise migrated to the station's own absence reading —
# see section 8. It is asserted there rather than twice.


# ── 14 · refinement pairs — a disagreement is reported, never resolved ───────
print("\n[14] a coarse pre-signal and the judge that refines it")
# Rule 3.0c. `grounded_overlap` counts literal token overlap; `groundedness` reads
# meaning and refines it. On a paraphrasing model over technical prose the coarse check
# fires red while the judge clears the same answer — 12 of 14 turns in campaign 1. The
# station took worst-of and read danger permanently: a badge that can never go green
# gets ignored. Weighting the judge would hide which measure spoke and make the reading
# depend on a SAMPLED judge; dropping the coarse check would silence one that runs on
# every turn. The disagreement itself is the signal.
check("the refinement map is declared, not inferred",
      AP.REFINEMENT_PAIRS.get("grounded_overlap") == "groundedness")
check("every refinement key is a real dimension the partition knows",
      all(c in {d for ds in AP.POINT_DIMENSIONS.values() for d in ds}
          and j in {d for ds in AP.POINT_DIMENSIONS.values() for d in ds}
          for c, j in AP.REFINEMENT_PAIRS.items()))
check("a pair lives on ONE station — a refinement that spanned two would move "
      "attention between stations", all(
          AP.owning_station_of_dim(c) == AP.owning_station_of_dim(j)
          if hasattr(AP, "owning_station_of_dim") else True
          for c, j in AP.REFINEMENT_PAIRS.items()))

# The four rows of the truth table, plus the judge that never ran.
_R = AP.refinement_reading
check("both fail  → corroborated", _R("danger", "danger") == AP.CORROBORATED)
check("judge only → judge_only",   _R("healthy", "danger") == AP.JUDGE_ONLY)
check("coarse only→ disputed",     _R("danger", "healthy") == AP.DISPUTED)
check("both pass  → agreed clean", _R("healthy", "healthy") == AP.AGREED_CLEAN)
check("judge did not run → no reading (the coarse check stands alone)",
      _R("danger", None) is None and _R(None, "healthy") is None)
check("a `review` band counts as a failing side",
      _R("review", "healthy") == AP.DISPUTED)

# Station contribution per reading — no new state, no weighting.
check("corroborated contributes danger", AP.REFINEMENT_STATE[AP.CORROBORATED] == "danger")
check("judge-only contributes danger", AP.REFINEMENT_STATE[AP.JUDGE_ONLY] == "danger")
check("disputed contributes WORTH A LOOK, never quiet and never danger",
      AP.REFINEMENT_STATE[AP.DISPUTED] == "warn")
check("agreed-clean contributes pass", AP.REFINEMENT_STATE[AP.AGREED_CLEAN] == "pass")
check("every reading has a sentence naming it",
      all(len(AP.REFINEMENT_NOTE.get(k, "")) > 40
          for k in (AP.CORROBORATED, AP.JUDGE_ONLY, AP.DISPUTED, AP.AGREED_CLEAN)))


def _pair_turn(coarse_band, judge_band):
    dims = [{"k": "grounded_overlap", "b": coarse_band, "s": 0.27}]
    if judge_band:
        dims.append({"k": "groundedness", "b": judge_band, "s": 1.0})
    failed = [d["k"] for d in dims if d["b"] in ("danger", "review")]
    return {"guardrail": {"ran": True, "action": "pass"},
            "grounding": {"ran": True, "decision": "pass"},
            "eval": {"checks": len(dims), "dimensions": dims, "failed_dimensions": failed}}


def _gg(turn):
    return _Readings(AP.read_turn(turn, "rag_focused"))["grounding_gate"]


_disp = _gg(_pair_turn("danger", "healthy"))
_dim = [d for d in _disp.dims if d["k"] == "grounded_overlap"][0]
# THE load-bearing assertion: the measurement is never rewritten to calm a station.
check("a disputed dimension still REPORTS its true band", _dim["b"] == "danger")
check("...while contributing only `warn` to its station", _dim.get("sc") == "warn")
check("...and is marked as disputed", _dim.get("disp") is True)
check("...and the mechanism NAMES the disagreement",
      "cleared it" in _disp.detail and "coarse lexical check flagged" in _disp.detail)

_corr = _gg(_pair_turn("danger", "danger"))
_cdim = [d for d in _corr.dims if d["k"] == "grounded_overlap"][0]
check("a corroborated failure contributes danger, unchanged",
      _cdim.get("sc", _cdim["b"]) == "danger")
check("...and is NOT marked disputed", not _cdim.get("disp"))
check("...and adds no disagreement note", "cleared it" not in _corr.detail)

_alone = _gg(_pair_turn("danger", None))
_adim = [d for d in _alone.dims if d["k"] == "grounded_overlap"][0]
check("with no judge verdict the coarse check stands alone at full weight",
      _adim.get("sc", _adim["b"]) == "danger" and not _adim.get("disp"))

# ══ PROP-002b — the severity construct + per-station reporting levels ═════════
from trusted_data_agent.agent.assurance_points import (  # noqa: E402
    DERIVED_EVENT_SEVERITY, REPORTING_LEVELS, SEVERITY_CONSTRUCT, STATIONS,
    STATION_REPORTING_DEFAULT, normalize_station_reporting, severity_surfaces,
    station_of_event,
    MECHANISM_LIMITS, REGISTRY, limits_of, guide_entry, POINT_DIMENSIONS,
    residue_dimensions, station_dimensions, partition_conflicts,
    station_entry, _scorer_census,
    BAND_STATE, STATES, dimension_state, band_state, read_turn_measures,
    station_dimensions)
import trusted_data_agent.agent.assurance_points as _ap_mod  # noqa: E402

# The construct: one level, one behaviour. The floor is danger's defining property.
check("construct has exactly the three levels",
      set(SEVERITY_CONSTRUCT) == {"danger", "warning", "info"})
check("danger carries the floor (never filterable)",
      SEVERITY_CONSTRUCT["danger"]["floor"] is True)
check("warning and info carry no floor",
      not SEVERITY_CONSTRUCT["warning"]["floor"] and not SEVERITY_CONSTRUCT["info"]["floor"])
check("info never badges and never folds",
      not SEVERITY_CONSTRUCT["info"]["badge"] and not SEVERITY_CONSTRUCT["info"]["fold"])
check("the construct's words ARE the attention vocabulary",
      SEVERITY_CONSTRUCT["danger"]["word"] == "needs you now"
      and SEVERITY_CONSTRUCT["warning"]["word"] == "worth a look")

# severity_surfaces — the truth table.
_rep = normalize_station_reporting({"guarded": "danger", "proven": "all"})
check("normalize fills every station with the default",
      set(_rep) == set(STATIONS) and _rep["grounded"] == STATION_REPORTING_DEFAULT)
check("normalize keeps valid assignments", _rep["guarded"] == "danger" and _rep["proven"] == "all")
check("normalize drops unknown stations/levels",
      "bogus" not in normalize_station_reporting({"bogus": "danger", "grounded": "loud"})
      and normalize_station_reporting({"grounded": "loud"})["grounded"] == STATION_REPORTING_DEFAULT)
check("danger ALWAYS surfaces — even on a danger-only station",
      severity_surfaces("danger", "guarded", _rep) is True)
check("warning is filtered on a danger-only station",
      severity_surfaces("warning", "guarded", _rep) is False)
check("warning surfaces at the default level",
      severity_surfaces("warning", "grounded", _rep) is True)
check("info surfaces only on an 'all' station",
      severity_surfaces("info", "proven", _rep) is True
      and severity_surfaces("info", "grounded", _rep) is False)
check("an unknown severity behaves as warning (never vanishes silently)",
      severity_surfaces("mystery", "grounded", _rep) is True)
check("no reporting map → defaults", severity_surfaces("warning", "guarded", None) is True)
check("reporting levels are the closed set", REPORTING_LEVELS == ("all", "warning", "danger"))

# station_of_event — agreement with the frontend `_stationOfEvent` on every event
# type either side knows (the same cross-language pin as the STATION_DIMS mirror).
_EXPECT_STATION = {
    "grounding_block": "grounded", "grounding_annotate": "grounded",
    "guardrail_outbound_block": "guarded", "guardrail_outbound_redact": "guarded",
    "guardrail_outbound_annotate": "guarded", "guardrail_inbound_block": "guarded",
    "guardrail_inbound_redact": "guarded", "guardrail_inbound_annotate": "guarded",
    "honesty_rewrite": "grounded", "honesty_flag": "grounded",
    "session_sentinel": "guarded", "judge_condemnation": "grounded",
    "memory_quarantine": "guarded", "retrieval_defuse": "guarded",
    "tool_result_injection": "guarded", "corpus_ingest_injection": "guarded",
    "retrieval_anomaly": "guarded", "live_redteam_failure": "guarded",
    "trust_breaker": "trusted", "review_release": "reviewed",
    "corpus_finding_ack": "guarded", "corpus_quarantine": "guarded",
    "station_shortfall_grounded::OPTIM": "grounded",
    "station_shortfall_scored::A": "scored",
    "station_shortfall_bogus::A": "scored",
}
for _et, _st in _EXPECT_STATION.items():
    check(f"station_of_event('{_et}') → {_st}", station_of_event(_et) == _st)
check("every derived severity key has a station home",
      all(station_of_event(k) in STATIONS for k in DERIVED_EVENT_SEVERITY))

# The frontend mirror agrees on the same inputs — regex-driven, so extract the
# real `_stationOfEvent` and drive it under the same expectations.
_soe_start = _hsrc.find("function _stationOfEvent(")
_soe_end = _hsrc.find("\n    }", _soe_start) + len("\n    }")
check("frontend _stationOfEvent extracted", _soe_start > 0 and _soe_end > _soe_start)
import subprocess as _sp  # noqa: E402
_js = (_hsrc[_soe_start:_soe_end]
       + "\nconst exp = " + __import__("json").dumps(_EXPECT_STATION) + ";"
       + "\nlet bad = 0;"
       + "\nfor (const [k, v] of Object.entries(exp)) if (_stationOfEvent(k) !== v) { bad++; console.error(k, _stationOfEvent(k)); }"
       + "\nconsole.log(bad);")
_out = _sp.run(["node", "-e", _js], capture_output=True, text=True)
check("frontend _stationOfEvent agrees on every known event type",
      _out.returncode == 0 and _out.stdout.strip().splitlines()[-1] == "0")

# The JS construct mirror (trustGrammar.js) agrees on words + the floor.
_gsrc = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                          "handlers", "trustGrammar.js")).read()
import re as _re  # noqa: E402
for _lvl, _spec in SEVERITY_CONSTRUCT.items():
    _m = _re.search(_lvl + r":\s*\{\s*word:\s*'([^']+)'[^}]*floor:\s*(true|false)", _gsrc)
    check(f"trustGrammar mirrors '{_lvl}' word", _m is not None and _m.group(1) == _spec["word"])
    check(f"trustGrammar mirrors '{_lvl}' floor",
          _m is not None and (_m.group(2) == "true") == bool(_spec["floor"]))
check("trustGrammar mirrors the reporting levels",
      "['all', 'warning', 'danger']" in _gsrc)


# ── MECHANISM LIMITS — when it acts, and what you can change ─────────────────
# The Guide said what a mechanism IS and how its reading is DERIVED; it never said when
# it FIRES or what is TUNABLE. An operator choosing a mode could not tell that the
# grounding trigger fires on a SINGLE unsupported number, nor that its sensitivity is not
# configurable — both had to be read out of four source files, which is not a surface.
# The second fact is load-bearing: without it a reader infers that the quality bands
# (which only colour a chip) must loosen a gate.
import inspect as _insp  # noqa: E402
from trusted_data_agent.agent.grounding import corpus as _cm  # noqa: E402

_missing_f = [p.key for p in REGISTRY if not limits_of(p.key).get("fires")]
# A point declares its control surface as `tunable` (setting → effect), or says what a
# reader might expect to tune and cannot (`fixed`); one of the two must be present.
_missing_t = [p.key for p in REGISTRY
              if not (limits_of(p.key).get("tunable") or limits_of(p.key).get("fixed"))]
check(f"every mechanism declares when it fires{' — missing ' + str(_missing_f) if _missing_f else ''}",
      not _missing_f)
check(f"every mechanism declares what is tunable or fixed{' — missing ' + str(_missing_t) if _missing_t else ''}",
      not _missing_t)
# The structure itself: `fires` carries triggers or a `none`, never both; pairs are pairs.
_shape_bad = []
for _p in REGISTRY:
    _l = limits_of(_p.key); _f = _l.get("fires") or {}
    if not isinstance(_f, dict) or (bool(_f.get("when")) == bool(_f.get("none"))):
        _shape_bad.append((_p.key, "fires"))
    if set(_f) - {"when", "then", "also", "ignores", "none"}:
        _shape_bad.append((_p.key, "fires keys"))
    for _k in ("tunable", "fixed"):
        if any(not (isinstance(_pair, tuple) and len(_pair) == 2 and all(_pair)) for _pair in _l.get(_k) or []):
            _shape_bad.append((_p.key, _k))
check(f"limits are structured: triggers XOR none, known keys, (setting, effect) pairs{' — ' + str(_shape_bad) if _shape_bad else ''}",
      not _shape_bad)
check("no orphan limit declared for a key the registry lacks",
      not (set(MECHANISM_LIMITS) - {p.key for p in REGISTRY}))

# THE CONDITION: numbers are read from the constants that decide them, never typed in.
# A Guide stating a threshold the code no longer uses is worse than one stating none.
_ap_src = _insp.getsource(_ap_mod)
_lim_src = _ap_src[_ap_src.index("MECHANISM_LIMITS: Dict"):_ap_src.index("def limits_of")]
for _lit in (_cm.IDENTIFIER_FLOOR, _cm.SECOND_CHANCE_CAP, _cm.QUOTE_MIN_CHARS):
    check(f"constant {_lit} is not hardcoded into the limits prose",
          f"{_lit}+" not in _lim_src)
_fires = " ".join(AP.limits_text("grounding_gate"))
for _c in (_cm.IDENTIFIER_FLOOR, _cm.SECOND_CHANCE_CAP, _cm.QUOTE_MIN_CHARS):
    check(f"the rendered rule carries the live constant {_c}", str(_c) in _fires)

# The fact that would have saved a long investigation, named where it was confusing —
# and under ITS OWN lead: a negative fact under "you can change" reads as a contradiction.
_gfixed = dict(limits_of("grounding_gate").get("fixed") or [])
check("the grounding entry says the quality bands never loosen it, under `fixed`",
      any("bands" in k for k in _gfixed) and any("never loosen" in v for v in _gfixed.values()))
check("…and nothing under `tunable` says what cannot be changed",
      not any("not configurable" in v or "no effect" in v
              for _, v in limits_of("grounding_gate").get("tunable") or []))
check("guide_entry serves the structured facts",
      isinstance(guide_entry("grounding_gate").get("fires"), dict)
      and isinstance(guide_entry("grounding_gate").get("tunable"), list)
      and isinstance(guide_entry("grounding_gate").get("fixed"), list)
      and bool(guide_entry("grounding_gate").get("why")))
check("an unknown key is not fabricated a limit", limits_of("does_not_exist") == {})


# ── the residue is DERIVED, and named where it is carried ────────────────────
# `POINT_DIMENSIONS` carried a comment claiming evaluation "is the union over everything
# scored — it takes the whole set". That described the PRE-PARTITION model, where JUDGED
# was the union and the other stations were lenses onto it. The partition retired that,
# and `_read_evaluation` has filtered the foreign dimensions out ever since — so the
# registry stated one fact two ways, two definitions apart, and the stale half was the
# stated REASON for evaluation carrying no dimensions in the Guide at all.
check("evaluation owns no declared subset (its set is the residue)",
      "evaluation" not in POINT_DIMENSIONS)
_res = residue_dimensions()
_owned = {d for dims in station_dimensions().values() for d in dims}
check("the residue is non-empty", len(_res) > 0)
check("…and disjoint from every owned dimension", not (set(_res) & _owned))
check("…so residue + owned partitions the scored set",
      len(set(_res) | _owned) == len(_res) + len(_owned))
check("…and it is DERIVED, never listed (adding a scorer must not need an edit)",
      "residue_dimensions" in open(_ap_mod.__file__).read()
      and str(sorted(_res)[:3]) not in open(_ap_mod.__file__).read())
# The Guide entry names what it counts — as `counts`, never `graded by`: evaluation
# PRODUCES these, it is not assessed by them ("Never — it grades, it never acts").
#
# It counts EVERY scored dimension now, so the row names the seven MOMENTS rather than
# listing 52 chips. Not a summary standing in for the truth: each moment is a real Guide
# entry carrying its own checks, so the full set is one click away and organised the way
# the reader sees it everywhere else. A 52-chip wall is complete and unreadable, which
# for a mental model is the same as incomplete.
_js2 = station_entry(AP.MEASUREMENT_STATION)
check("the JUDGED station counts the MOMENTS, not a residue list",
      _js2.get("counts") == [f"stage_{s}" for s in ST_STAGES.STAGE_ORDER])
check("…and every one of them is a real, openable Guide entry",
      all(ST_STAGES.stage_entry(k[len("stage_"):]) for k in (_js2.get("counts") or [])))
check("…the row sits on the STATION, not on a mechanism standing in front of it",
      all(not (guide_entry(p.key) or {}).get("counts") for p in REGISTRY))

# ── the judge marker: one list, one carrier ──────────────────────────────────
# A judge presented as a deterministic check is a model's OPINION wearing a rule's
# clothes — the single thing this mark exists to prevent. The Guide's JUDGE_DIMS is a
# hand-maintained mirror of the registry's `kind == judge`, so it is pinned here.
_qd_src = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                            "handlers", "qualityDimensions.js")).read()
_m = _re.search(r"const JUDGE_DIMS = new Set\(\[(.*?)\]\)", _qd_src, _re.S)
check("the Guide declares a judge list", _m is not None)
if _m:
    _front = set(_re.findall(r"'([a-z_]+)'", _m.group(1)))
    try:
        from trusted_data_agent.eval import scorer as _sc
        import trusted_data_agent.eval.scorers  # noqa: F401
        _back = {x.dimension for x in _sc.all_scorers()
                 if str(getattr(x, "kind", "")).split(".")[-1].lower() == "judge"}
    except Exception:  # noqa: BLE001
        _back = None
    check("the eval registry is importable for the judge pin", _back is not None)
    if _back is not None:
        check(f"…and the Guide's judge list equals it "
              f"(guide-only: {sorted(_front - _back) or 'none'}; "
              f"registry-only: {sorted(_back - _front) or 'none'})", _front == _back)
# That the mark is RENDERED — and that it is the text tag, not a dash — is asserted by
# executing the real chipRow in test_quality_card_partition.mjs. A source grep here
# would survive the tag being deleted from the row while its CSS rule remained, which is
# exactly what a first draft of this assertion did.

# ── a count is a fact ABOUT something: state the scope, or state nothing ─────
# The Evaluation entry read "42 of the 52 dimensions are deterministic code; 9 are LLM
# judges" one sentence before "only the residue is counted here". Two scopes in one
# paragraph, and the reader cannot tell which number describes what they are looking at
# — and the platform-wide figure was ALSO stale (43, not 42; 42 + 9 never summed to the
# 52 in the same sentence). The rule MECHANISM_LIMITS already lives under — numbers are
# read from what decides them, never typed in — applies to derivation prose too.
_ap_src_all = open(_ap_mod.__file__).read()
_res_set = set(residue_dimensions())
_c_scoped = _scorer_census(_res_set)
_c_all = _scorer_census()
check("the census counts the scope it is given",
      _c_scoped["total"] == len(_res_set) and _c_scoped["total"] < _c_all["total"])
check("…splitting it into deterministic + judges exactly",
      _c_scoped["deterministic"] + _c_scoped["judges"] == _c_scoped["total"])
check("…and the platform-wide split closes too (the old prose did not)",
      _c_all["deterministic"] + _c_all["judges"] == _c_all["total"])
check("an empty scope yields no census rather than a zero",
      _scorer_census([]) is None)
# The rendered prose states the RESIDUE's numbers, never the platform's.
_long = station_entry(AP.MEASUREMENT_STATION)["reads"]["checks"]
# The scope IS the platform total now — evaluation counts every measurement, so a
# census of the old residue scope would understate its own card by 23. The census rides
# the station's "its checks" reading row, as a phrase: total · deterministic · judges.
check("the JUDGED station states the count of what it actually counts",
      _long.startswith(f"{_c_all['total']} ·") and f"{_c_all['deterministic']} deterministic" in _long
      and f"{_c_all['judges']} judges" in _long)
# `_c_scoped` is computed above with the RESIDUE scope; the entry now counts everything,
# so the assertion is that the entry's own prose uses the full scope — which the check
# above already proves. What is worth pinning separately is that the residue scope is now
# strictly SMALLER, i.e. the two are no longer interchangeable and using the old one
# would understate the card.
check("the residue scope is a strict subset — using it would understate the card",
      _c_scoped["total"] < _c_all["total"])
# No count is typed into the prose — every one is read from the registry.
_der_src = _ap_src_all[_ap_src_all.index("MECHANISM_DERIVATION: Dict"):
                       _ap_src_all.index("def _scorer_census")]
for _n in (_c_all["total"], _c_all["deterministic"], _c_all["judges"],
           _c_scoped["total"], _c_scoped["deterministic"]):
    check(f"the count {_n} is not hardcoded into the derivation prose",
          str(_n) not in _der_src)

# ── JUDGED owns nothing by declaration ───────────────────────────────────────
# Its definition is "the checks no other station owns", and it used to hold ONE owned
# dimension anyway: `governed_data_ratio`, because `governed_data` sat at `scored` by
# elimination (the registry recorded only "SCORED, not TRUSTED"). That cost nothing while
# JUDGED was the UNION — a mechanism's station did not decide where its dimension was
# counted. The partition made the station the decision, and the card then contradicted
# its own sentence one line down.
check("JUDGED declares no owned dimension — it is purely the residue",
      not station_dimensions().get("scored"))
check("governed_data measures whether the answer rested on declared data ops → GROUNDED",
      "governed_data_ratio" in station_dimensions()["grounded"])
check("…and the mechanism moved with it",
      next(p.station for p in REGISTRY if p.key == "governed_data") == "grounded")
check("the partition is still conflict-free after the move", partition_conflicts() == [])
check("the JUDGED station entry offers no graded-by row at all",
      not station_entry("scored")["dims"])

# ── the two vocabularies, and the ONE bridge ─────────────────────────────────
# A band is not a state. They overlap on exactly one word (`danger`), which is why three
# hand-written aliases in the renderer looked correct while every one of them dropped
# `review` — the single value where the vocabularies genuinely differ. One declaration
# per language, pinned equal here; nothing else may convert a band.
# ── band ↔ state: one bridge, mirrored
check("every band maps into the state vocabulary",
      all(v in STATES for v in BAND_STATE.values()))
check("review is amber, not silence", BAND_STATE["review"] == "warn")
check("danger survives the crossing", BAND_STATE["danger"] == "danger")
check("healthy is quiet", BAND_STATE["healthy"] == "pass")
check("an unknown band contributes nothing rather than a guess",
      dimension_state({"k": "x", "b": "not_a_band"}) == "na"
      and dimension_state({"k": "x"}) == "na" and dimension_state(None) == "na")
# `sc` wins over `b` — Rule 3.0c — and the two legitimately differ.
check("a refinement contribution overrides the band it refines",
      dimension_state({"k": "grounded_overlap", "b": "danger", "sc": "warn"}) == "warn")
check("…while the chip still shows what was MEASURED",
      band_state({"k": "grounded_overlap", "b": "danger", "sc": "warn"}) == "danger")

_gp = os.path.join(os.path.dirname(__file__), "..", "static", "js", "handlers",
                   "trustGrammar.js")
_gjs = open(_gp).read()
_probe = ("%s\nconst m = (typeof window!=='undefined'?window:globalThis).TrustGrammar.BAND_STATE;"
          "\nconsole.log(JSON.stringify(m));" % "")
_res = _sp.run(["node", "-e",
                "const w={};(new Function('window','document',%s))(w,{addEventListener(){}});"
                "console.log(JSON.stringify(w.TrustGrammar.BAND_STATE));"
                % __import__("json").dumps(_gjs)],
               capture_output=True, text=True)
check("trustGrammar.BAND_STATE is readable", _res.returncode == 0)
check("…and is byte-equal to the Python declaration",
      _res.returncode == 0
      and __import__("json").loads(_res.stdout.strip().splitlines()[-1]) == dict(BAND_STATE))

# W1/W4 (TRUST_CIRCLE_LEGIBILITY.md) — the posture→state and posture→label maps are
# declared in core/posture_modes.py and mirrored in trustGrammar.js; a drift here is
# the ring, the matrix and the anatomy disagreeing about one stored mode again.
from trusted_data_agent.core.posture_modes import POSTURE_STATE, POSTURE_LABELS  # noqa: E402
_res2 = _sp.run(["node", "-e",
                 "const w={};(new Function('window','document',%s))(w,{addEventListener(){}});"
                 "console.log(JSON.stringify([w.TrustGrammar.POSTURE_STATE, w.TrustGrammar.POSTURE_LABEL]));"
                 % __import__("json").dumps(_gjs)],
                capture_output=True, text=True)
check("trustGrammar.POSTURE_STATE / POSTURE_LABEL are readable", _res2.returncode == 0)
if _res2.returncode == 0:
    _js_state, _js_label = __import__("json").loads(_res2.stdout.strip().splitlines()[-1])
    check("trustGrammar.POSTURE_STATE is byte-equal to posture_modes.POSTURE_STATE", _js_state == dict(POSTURE_STATE))
    check("trustGrammar.POSTURE_LABEL is byte-equal to posture_modes.POSTURE_LABELS", _js_label == dict(POSTURE_LABELS))

# W5 — the station registry: ONE table (assurance_points.STATION_TABLE), mirrored in
# trustGrammar.STATIONS. Seven JS copies drifted before; this pin is why there is one.
from trusted_data_agent.agent.assurance_points import STATION_TABLE, station_label, station_row  # noqa: E402
check("STATIONS derives from STATION_TABLE", tuple(r["key"] for r in STATION_TABLE) == STATIONS)
check("every row has key/label/sub/q/lane", all(set(r) == {"key", "label", "sub", "q", "lane"} for r in STATION_TABLE))
check("every question ends with ?", all(r["q"].endswith("?") for r in STATION_TABLE))
check("scored is labelled JUDGED", station_label("scored") == "JUDGED")
check("an unknown key stays visible", station_label("bogus") == "BOGUS" and station_row("bogus") is None)
check("station_entry carries the question and the character line",
      station_entry("grounded")["q"] == "Does it stay on its corpus?"
      and station_entry("scored")["sub"] == "execution quality, graded"
      and station_entry("scored")["label"] == "JUDGED")
_res3 = _sp.run(["node", "-e",
                 "const w={};(new Function('window','document',%s))(w,{addEventListener(){}});"
                 "console.log(JSON.stringify(w.TrustGrammar.STATIONS));"
                 % __import__("json").dumps(_gjs)],
                capture_output=True, text=True)
check("trustGrammar.STATIONS is readable", _res3.returncode == 0)

# ── class scope (legibility follow-up) — a mechanism outside the class is LISTED, flagged ──
# GROUNDED read six mechanisms on an Ideate turn beside nine in the Guide with nothing
# saying why: the reader dropped what did not apply. Now every registered point is a
# reading on every class; the ones outside it carry applies=False, state na, and name
# where they run in IFOC words — so the dial's station and the Guide's entry always list
# the same mechanisms, and the count says why they differ.
_ide = {r.key: r for r in AP.read_turn({}, "llm_only")}
check("an Ideate turn still lists preflight_sql", "preflight_sql" in _ide)
check("…flagged as not applying", _ide["preflight_sql"].applies is False)
check("…as an honest absence, never a pass", _ide["preflight_sql"].state == "na")
check("…with its own word", _ide["preflight_sql"].word == "not part of this class")
check("…naming where it runs in IFOC words, never a raw type",
      "Optimize" in _ide["preflight_sql"].classes and "Ideate + tools" in _ide["preflight_sql"].classes
      and not any("_" in c for c in _ide["preflight_sql"].classes))
check("…and never acted", _ide["preflight_sql"].acted is False)
check("an applicable mechanism is applies=True with no classes", _ide["grounding_gate"].applies and not _ide["grounding_gate"].classes)
check("as_dict carries the flag", AP.read_turn({}, "llm_only")[0].as_dict().get("applies") in (True, False))
check("a tool_enabled turn has no out-of-class readings", not [r for r in AP.read_turn({}, "tool_enabled") if not r.applies])
_scored_turn = {"eval": {"dimension_verdicts": {"grounded_overlap": {"band": "danger"}}}}
check("the flag survives the refinement join and the guide attach",
      {r.key: r.applies for r in AP.read_turn(_scored_turn, "llm_only")}.get("preflight_sql") is False)
# THE consistency gate: per class, per station, the dial's rows ≡ the Guide's mechanisms
# that are on a turn's path. The Guide also lists a station's deliberate exclusions (they
# are its members — without them `memory_firewall` had no parent), and marks them, so
# the difference between the two surfaces is named rather than silent.
_off_path = {p.key for p in AP.points(include_excluded=True) if p.excluded}
for _cls in ("llm_only", "rag_focused", "tool_enabled", "genie", "conversation_with_tools"):
    _by = AP.read_turn_by_station({}, _cls)
    for _st in STATIONS:
        _dial = sorted(r.key for r in _by.get(_st, []))
        _guide = sorted(k for k in AP.station_entry(_st)["controls"]
                        if k not in _off_path) if AP.station_entry(_st) else []
        check(f"dial rows ≡ Guide mechanisms at {_st} for {_cls}", _dial == _guide)
for _p in AP.points(include_excluded=True):
    check(f"{_p.key} is listed by its own station {_p.station} (its parent)",
          _p.key in (AP.station_entry(_p.station) or {}).get("controls", ()))
if _res3.returncode == 0:
    check("trustGrammar.STATIONS is byte-equal to assurance_points.STATION_TABLE",
          __import__("json").loads(_res3.stdout.strip().splitlines()[-1]) == [dict(r) for r in STATION_TABLE])

# ── G1 · the allocation is TOTAL and EXCLUSIVE, per class ────────────────────
# Every scored dimension lands at exactly one station — `MEASUREMENT_STATION`. Totality
# and exclusivity are the two properties that survived the axis change intact, and they
# are the ones worth pinning: totality because a dimension counted nowhere is invisible
# (the defect that predated the registry lookup), exclusivity because a dimension counted
# twice is the pre-Aug-2026 union's defect, which said "2 need you now" for one cause.
# ── allocation — every measurement counted exactly once
try:
    from trusted_data_agent.eval import scorer as _S
    import trusted_data_agent.eval.scorers  # noqa: F401  (registers)
    _have_scorers = True
except Exception:  # pragma: no cover - eval package optional in some checkouts
    _have_scorers = False
check("the eval scorer registry is importable", _have_scorers)
if _have_scorers:
    for _cls in ("tool_enabled", "rag_focused", "llm_only", "genie",
                 "conversation_with_tools"):
        _dims = sorted({getattr(x, "dimension", None) or getattr(x, "name", "?")
                        for x in _S.scorers_for(_cls)})
        _turn = {"eval": {"scored": True, "checks": len(_dims),
                          "dimensions": [{"k": d, "b": "healthy"} for d in _dims]}}
        _alloc = read_turn_measures(_turn, _cls)
        _seen = [d["k"] for v in _alloc.values() for d in v]
        check(f"{_cls}: every scored dimension is allocated",
              sorted(_seen) == _dims)
        check(f"{_cls}: no dimension is counted twice",
              len(_seen) == len(set(_seen)))
        check(f"{_cls}: every dimension counts at JUDGED and nowhere else",
              sorted(d["k"] for d in _alloc[AP.MEASUREMENT_STATION]) == _dims
              and all(not v for st, v in _alloc.items()
                      if st != AP.MEASUREMENT_STATION))
        check(f"{_cls}: every allocated record carries its contribution",
              all(d.get("st") in STATES and d.get("bst") in STATES
                  for v in _alloc.values() for d in v))
        # The LABEL survives the counting change — this is the half that was kept, and
        # every reading surface (scorecard groups, roster bars, Guide homes) depends on
        # it. A dimension that is counted but no longer resolvable to a guarantee would
        # pass the two checks above and still break four surfaces.
        _labelled = {d: st for st, ds in station_dimensions().items() for d in ds}
        check(f"{_cls}: every guarantee-labelled dimension still resolves to ONE station",
              all(_labelled[d] in STATIONS for d in _dims if d in _labelled))

# A review-band measurement must reach JUDGED as amber — the defect, at the source.
_rt = {"eval": {"scored": True, "checks": 2, "dimensions": [
    {"k": "context_utilization", "b": "review", "s": 0.56},
    {"k": "grounded_overlap", "b": "healthy"}]}}
_alloc = read_turn_measures(_rt, "rag_focused")
check("a review band allocates to JUDGED, with its guarantee-labelled neighbour",
      [d["k"] for d in _alloc[AP.MEASUREMENT_STATION]]
      == ["context_utilization", "grounded_overlap"])
check("…contributing amber, not silence",
      [d["st"] for d in _alloc[AP.MEASUREMENT_STATION]
       if d["k"] == "context_utilization"] == ["warn"])
check("…and GROUNDED holds no measurement of its own to fold",
      not _alloc.get("grounded"))

# ── G1b · a judge alone cannot drive JUDGED to danger ────────────────────────
# `analysis.convicting_dimensions` has held since Aug 2026 that a judge alone does not
# convict a turn. `dimension_state` did not know it, so one fact had two answers: the
# turn verdict read no conviction while the station beside it went red on the same
# finding. It matters far more now — JUDGED carries every measurement, so one sampled
# judge would put the sole measurement station into permanent danger, and a station that
# can never go green gets ignored (this platform's own finding about GROUNDED).
_judges = AP.judge_dimensions()
check("the judge set is non-empty and read from the registry, not a name pattern",
      len(_judges) >= 5 and "groundedness" in _judges
      and "answer_correctness" in _judges)
check("a rule in danger convicts — full weight, unchanged",
      dimension_state({"k": "grounded_overlap", "b": "danger"}) == "danger")
check("a judge in danger is CAPPED at warn, never silenced",
      dimension_state({"k": "groundedness", "b": "danger"}) == "warn")
check("…and its own chip still shows what it measured (never rewritten)",
      band_state({"k": "groundedness", "b": "danger"}) == "danger")
check("a judge in review is unaffected by the cap",
      dimension_state({"k": "groundedness", "b": "review"}) == "warn")
check("a judge that passed is unaffected by the cap",
      dimension_state({"k": "groundedness", "b": "healthy"}) == "pass")
check("Rule 3.0c still outranks the cap — an explicit contribution wins",
      dimension_state({"k": "groundedness", "b": "danger", "sc": "danger"}) == "danger")
# Fail-soft direction: with no registry every dimension reads as rule-measured, so
# attention is OVER-reported rather than a conviction being silently withdrawn.
check("an unknown dimension is treated as rule-measured (the safe direction)",
      dimension_state({"k": "not_a_registered_dimension", "b": "danger"}) == "danger")
# The cap's REAL-WORLD consequence, asserted at the allocation rather than the resolver:
# a turn whose only failing check is a judge must leave JUDGED amber, not red. Without
# this, the rule has exactly one sensitive assertion and a regression could halve its
# coverage while still reading green.
_jt = {"eval": {"scored": True, "checks": 2, "dimensions": [
    {"k": "groundedness", "b": "danger"},
    {"k": "latency", "b": "healthy"}]}}
_jalloc = read_turn_measures(_jt, "rag_focused")[AP.MEASUREMENT_STATION]
check("a turn failed only by a judge contributes amber to JUDGED, never danger",
      sorted(d["st"] for d in _jalloc) == ["pass", "warn"])
check("…while a turn failed by a RULE does reach danger",
      sorted(d["st"] for d in read_turn_measures(
          {"eval": {"scored": True, "checks": 2, "dimensions": [
              {"k": "grounded_overlap", "b": "danger"},
              {"k": "latency", "b": "healthy"}]}},
          "rag_focused")[AP.MEASUREMENT_STATION]) == ["danger", "pass"])
# The memo must not latch an EMPTY resolution. The scorer registry fills at import of
# `eval.scorers`, so an early call can legitimately find nothing; latching that would
# make every judge read as a rule for the life of the process and silently restore the
# conviction the cap removes. (`ensureVocabulary`'s lesson, same shape of bug.)
_saved_cache = AP._JUDGE_DIMS_CACHE
AP._JUDGE_DIMS_CACHE = frozenset()
check("an empty judge resolution never latches — the next call re-resolves",
      bool(AP.judge_dimensions()) and "groundedness" in AP.judge_dimensions())
AP._JUDGE_DIMS_CACHE = _saved_cache


# ── product text describes the PRESENT ───────────────────────────────────────
# Application text must not explain itself by contrast with a version the reader never
# saw. "It used to hold whatever no other station claimed" tells someone reading the
# JUDGED panel today about a model that no longer exists — changelog content in a
# product surface, and unanswerable for anyone who arrived after the change.
#
# Scoped to the trust MODEL's own prose. A statement about the READER's artifact
# ("this deployment moved after it was attested") legitimately refers to that artifact's
# history and is a different thing entirely.
print("\n[16] product text states what IS, never what changed")
import re as _re2
_PAST = _re2.compile(r"used to\b|previously|formerly|the old |it was once|"
                     r"rather than a leftover|blending the two made|the lane cut", _re2.I)
_prose = []
for _k in AP.STATIONS:
    _prose.append((f"STATION_NOTE[{_k}]", AP.STATION_NOTE.get(_k, "")))
    _prose.append((f"station_derivation({_k})", AP.station_derivation(_k)))
for _k, _v in AP.MECHANISM_NOTE.items():
    _prose.append((f"MECHANISM_NOTE[{_k}]", _v))
for _k in AP.MECHANISM_LIMITS:
    for _i, _t in enumerate(AP.limits_text(_k)):
        _prose.append((f"MECHANISM_LIMITS[{_k}][{_i}]", _t))
for _k in AP.STATIONS:
    for _i, _t in enumerate(AP.reads_text(_k)):
        _prose.append((f"STATION_READS[{_k}][{_i}]", _t))
for _k in ST_STAGES.STAGE_ORDER:
    _prose.append((f"STAGE_DESC[{_k}]", ST_STAGES.STAGE_DESC[_k]))
    _prose.append((f"STAGE_DERIVATION[{_k}]", ST_STAGES.STAGE_DERIVATION[_k]))
_offenders = [(n, _PAST.search(t).group(0)) for n, t in _prose if _PAST.search(t)]
check(f"no rendered trust prose refers to a previous version ({_offenders or 'none'})",
      not _offenders)
# The JS notes are rendered text too, and carried the same defect.
_aph = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                         "handlers", "agentPerformanceHandler.js")).read()
_note = _aph[_aph.index("const _JUDGED_NOTE ="):]
_note = _note[:_note.index(";\n")]
check("…nor does the station note the ring renders",
      not _PAST.search(_note))
check("…and it still states the rule it replaced the history with (one sentence since W3)",
      "counted at JUDGED" in _note and "grades it afterwards" in _note and "Measurement is a" not in _note)

# ── 14 · why a mechanism is ungraded — declared, and DIFFERENTIATED ──────────
# The Guide shipped ONE sentence for all six ungraded mechanisms — "it is a yes/no
# control" — which is wrong for an attestation, a boundary and a MEASUREMENT mislabelled
# as a mechanism, and which presented every absence as CORRECT. Three genuine controls run
# and are never graded at all; that sentence concealed them.
print("\n[14] an absence is explained, and by_design is not confused with a gap")
_ungraded = [p.key for p in AP.REGISTRY if not AP.POINT_DIMENSIONS.get(p.key)]
_unexplained = [k for k in _ungraded if not AP.grading_state(k)["reason"]]
if _unexplained:
    print(f"      unexplained: {_unexplained}")
check(f"every ungraded mechanism has a declared reason ({len(_ungraded)})",
      not _unexplained)
check("a graded mechanism derives `graded` — no declaration can contradict it",
      AP.grading_state("grounding_gate")["state"] == "graded")
check("…and carries the dimensions that grade it",
      len(AP.grading_state("grounding_gate")["dimensions"]) > 0)
# The distinction the one-size sentence destroyed.
check("an ACT reads by_design", AP.grading_state("agent_integrity")["state"] == "by_design")
check("a control that simply is not graded reads gap",
      AP.grading_state("preflight_sql")["state"] == "gap")
# A reader who cannot tell them apart cannot tell a finished design from an unfinished one.
check("…and the two are not the same state",
      AP.grading_state("agent_integrity")["state"]
      != AP.grading_state("preflight_sql")["state"])
# An undeclared absence must LOOK unexplained rather than inherit a comforting default.
check("an unknown mechanism reads gap with no reason",
      AP.grading_state("not_a_mechanism") == {"state": "gap", "dimensions": [],
                                              "reason": ""})
check("the reading is SERVED on the entry",
      (AP.guide_entry("preflight_sql") or {}).get("grading", {}).get("state") == "gap")
_qd = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                        "handlers", "qualityDimensions.js")).read()
check("the frontend renders the served reading, not a hardcoded sentence",
      "GRADING[k]" in _qd and "is a yes/no control, and its entry above" not in _qd)
# Colour alone would not survive greyscale; the class carries a border weight too.
check("…and a gap is distinguishable without reading the words", "qd-row-gap" in _qd)

print("\n[live progress] the tally's drain — read mid-turn, not only at the end")

from trusted_data_agent.agent import assurance_tally as _tally      # noqa: E402

_tally.begin_turn()
_tally.mark_ran("preflight_sql")
_tally.record("tool_authority", ran=True, acted=False)
_first = _tally.drain_new()
check("a drain reports every mechanism that has recorded",
      {r["gate"] for r in _first} == {"preflight_sql", "tool_authority"})
# Without this the dial would re-announce every mechanism on every engine event.
check("...and is IDEMPOTENT - a second drain re-announces nothing",
      _tally.drain_new() == [])
_tally.record("grounding_gate", ran=True, acted=True)
_second = _tally.drain_new()
check("...while a mechanism recorded LATER is reported when it lands",
      [r["gate"] for r in _second] == ["grounding_gate"])
check("...carrying whether it ACTED, which the dial renders differently",
      _second[0]["acted"] is True)
# snapshot() copies every stamp onto the persisted turn and the audit reads it, so the
# drain's own bookkeeping lives in a separate contextvar and can never get there.
check("the drain's bookkeeping never reaches the persisted stamps",
      all(not k.startswith("_") for v in _tally.snapshot().values() for k in v))
# The reset must be tested with a gate that was ALREADY announced last turn: an empty
# tally drains empty whether or not the seen-set was cleared, so the first version of this
# assertion could not fail and a mutation removing the reset sailed through it.
_tally.begin_turn()
_tally.mark_ran("preflight_sql")
check("a new turn re-announces a gate the PREVIOUS turn already reported",
      [r["gate"] for r in _tally.drain_new()] == ["preflight_sql"])
_tally.end_turn()
check("and a drain after the turn closes is empty, never a crash",
      _tally.drain_new() == [])


print("\n[N] absent stamp = the gate did NOT run (na), never 'should have recorded' (unknown)")
# The bug class: a gate reader returned `unknown` when the turn carried no stamp for it. But an
# absent stamp means the gate's path was never reached this turn — structurally absent (na),
# NOT a recording gap. Production evidence (not theory): real turns carry every partial-stamp
# shape — ['grounding'] alone, ['guardrail_inbound'] alone, ['honesty'] alone — from benign
# engine-path variance; and preflight/retrieval_hygiene stamp only when their subject occurs
# (a SQL call / a retrieval), so absence is their NORMAL state on a legitimate turn. `unknown`
# (rank 3) outranks `pass`, so ONE absent reading dragged its whole station to "not recorded"
# (the OPTIM defect: GROUNDED/GUARDED/BOUNDED "not recorded" on a clean SQL turn). A genuine
# gap (the gate RAN but ERRORED) is stamped action="error" by the gate itself and still reads
# unknown from THERE. This is the rule `_read_integrity` already documents, applied everywhere.

# every absent-stamp reader now reads na (the four that were wrong + the three RAG-lane ones):
for _fn, _label in ((AP._read_quarantine, "corpus quarantine"),
                     (AP._read_retrieval_firewall, "retrieval firewall"),
                     (AP._read_sensitivity, "sensitivity ceiling"),
                     (AP._read_preflight, "preflight SQL"),
                     (AP._read_inbound, "inbound guard"),
                     (AP._read_outbound, "outbound guard"),
                     (AP._read_grounding, "grounding gate"),
                     (AP._read_honesty, "honesty gate")):
    check(f"{_label}: absent stamp reads na (not unknown)",
          _fn({}, "tool_enabled").state == "na")

# the SYSTEMIC property: a gates-off turn (no stamps at all) reads NO station as unknown —
# it reads na ("nothing ran"), which is honest, instead of alarming the whole ring.
_off = AP.read_turn_by_station({"profile_type": "tool_enabled"}, "tool_enabled")
check("gates-off turn: NO station reads unknown (the default-deployment defect)",
      all(AP.worst_state(rs) != "unknown" for rs in _off.values()))
check("gates-off turn: GROUNDED/GUARDED/BOUNDED read na, not unknown",
      all(AP.worst_state(_off[s]) == "na" for s in ("grounded", "guarded", "bounded")))

# the legitimate unknown is PRESERVED: a gate that RAN but ERRORED still reads unknown.
check("outbound guard that ERRORED still reads unknown (real gap preserved)",
      AP._read_outbound({"guardrail": {"ran": True, "action": "error"}},
                        "tool_enabled").state == "unknown")
check("grounding gate that ERRORED still reads unknown (real gap preserved)",
      AP._read_grounding({"grounding": {"action": "error"}}, "tool_enabled").state == "unknown")
check("honesty gate that ERRORED still reads unknown (real gap preserved)",
      AP._read_honesty({"honesty": {"ran": True, "action": "error"}},
                       "tool_enabled").state == "unknown")

# regression: the STAMPED-CLEAN path is unchanged — a run guard still passes, a finding warns.
check("outbound guard still PASSES when it ran clean",
      AP._read_outbound({"guardrail": {"ran": True, "action": "pass"}},
                        "tool_enabled").state == "pass")
check("retrieval firewall still WARNS when hygiene defused a chunk",
      AP._read_retrieval_firewall({"retrieval_hygiene": {"defused_chunks": 2}},
                                  "tool_enabled").state == "warn")

# the user's real scenario: a passing SQL tool_enabled turn (all gates ran, no RAG) reads its
# stations GREEN — not "not recorded".
_pass_turn = {"profile_type": "tool_enabled",
              "grounding": {"decision": "pass", "reason": "observe",
                            "scope_databases": ["fitness_db"], "ground": "scope"},
              "preflight": {"ran": True, "mode": "observe", "checked_count": 1},
              "guardrail": {"ran": True, "acted": False, "action": "pass"},
              "guardrail_inbound": {"ran": True, "acted": False, "action": "pass"},
              "honesty": {"ran": True, "acted": False, "action": "pass"}}
_g = AP.read_turn_by_station(_pass_turn, "tool_enabled")
check("passing SQL tool_enabled turn: GROUNDED reads pass (not 'not recorded')",
      AP.worst_state(_g["grounded"]) == "pass")
check("passing SQL tool_enabled turn: GUARDED reads pass",
      AP.worst_state(_g["guarded"]) == "pass")


# ── JUDGED's pending state is BOUNDED by turn age (Sep 2026) ──────────────────
# "not in yet" is a claim that a score is still coming. The online eval runs right after
# the turn persists and its judge layer is bounded at 25 s, so an unscored turn minutes
# old is not pending — nothing more is coming — and the truthful reading is the absence.
# Left unbounded, an unscored turn read pending FOREVER, and the Live Status dial
# (which renders this state) showed "not in yet" until a page refresh.
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
_now = _dt.now(_tz.utc)
_fresh = {"grounding": {"ran": True}, "timestamp": _now.isoformat()}
_old = {"grounding": {"ran": True}, "timestamp": (_now - _td(minutes=10)).isoformat()}
_old_z = {"grounding": {"ran": True}, "timestamp": (_now - _td(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")}
_legacy = {"grounding": {"ran": True}}
_bad = {"grounding": {"ran": True}, "timestamp": "not a time"}
check("pending window: a fresh unscored turn reads pending",
      AP.measurement_status(_fresh)["state"] == "pending")
check("pending window: an unscored turn past the window reads absent, never pending",
      AP.measurement_status(_old)["state"] == "absent")
check("pending window: a 'Z'-suffixed ISO time is read (the engines' format)",
      AP.measurement_status(_old_z)["state"] == "absent")
check("pending window: a turn with NO timestamp keeps the pre-window reading (pending)",
      AP.measurement_status(_legacy)["state"] == "pending")
check("pending window: an unparseable timestamp never pushes a turn into absent",
      AP.measurement_status(_bad)["state"] == "pending")
check("pending window: a scored old turn still reads scored (the bound touches only absence)",
      AP.measurement_status({**_old, "eval": {"checks": 3}})["state"] == "scored")
check("pending window: the constant is a real bound (seconds, > the 25 s judge cap, < 10 min)",
      25 < AP.EVAL_PENDING_WINDOW_SECONDS < 600)
check("pending window: the helper is pure on an injected clock",
      AP.turn_within_scoring_window(_fresh, now=_now.timestamp() + AP.EVAL_PENDING_WINDOW_SECONDS + 1) is False
      and AP.turn_within_scoring_window(_fresh, now=_now.timestamp() + 1) is True)

# ── [O] one master switch, three readers: the mechanism's stamp, the module line, the scorer ─
# Operator finding (prod session 4f59f838, Sep 2026): the GROUNDED panel read "Governed data
# · no data operations on this turn" (a turn that HAD queried) beside "1 of 1 found something"
# (the module line, which re-ran the assessor without the switch). One property, one gate.
print("\n[O] the governed-data lane's master switch is ONE declaration, honoured by every reader")
from trusted_data_agent.core.config import APP_CONFIG as _CFG
from trusted_data_agent.agent.assurance_points import _read_obda as _ro, active_modules as _am
_q_turn = {"profile_type": "conversation_with_tools", "workflow_history": [],
           "conversation_agent_events": [{"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}},
                                         {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": True}}]}
_prev = _OP.plane_available("read")
_OP.force("read", False)
_r_off = _ro(_q_turn, "conversation_with_tools"); _m_off = _am("governed_data", _q_turn)
check("switch OFF, a turn that queried, no stamp ⇒ the mechanism reads na 'lane is off here' — never 'no data operations' (which would be false)",
      _r_off.state == "na" and "off here" in _r_off.detail and "no data operations" not in _r_off.detail)
check("…and the module line agrees: the bound module ABSTAINS with the same words, triggers nothing (no '1 of 1 found something')",
      len(_m_off) == 1 and _m_off[0]["abstained"] and not _m_off[0].get("triggered") and "off here" in str(_m_off[0]["summary"]))
_OP.force("read", True)
_r_on = _ro({"profile_type": "conversation_with_tools", "workflow_history": []}, "conversation_with_tools"); _m_on = _am("governed_data", _q_turn)
check("switch ON, no stamp ⇒ 'no data operations' is the honest reading; the module then reports the lane split it found",
      "no data operations" in _r_on.detail and len(_m_on) == 1 and not _m_on[0]["abstained"] and _m_on[0].get("triggered"))
_OP.force("read", _prev)

# ── [P] a grounding PASS reads by the family that ran (prod 2c24e2a1 turn 1) ─────
# A knowledge-only agent has no declared scope, so "stays inside the declared scope" named
# a check that never happened; and spans the trigger recorded but did not act on must say
# why, never read as though nothing was found.
print("\n[P] a grounding pass names the family that ran and why recorded spans were not acted on")
_gp = lambda ev: AP._read_grounding({"grounding": ev}, "rag_focused")
_g_clean = _gp({"decision": "pass", "reason": "corpus-supported", "ground": "corpus"})
check("corpus pass, nothing unsupported ⇒ supported by the retrieved knowledge, no 'declared scope'",
      _g_clean.state == "pass" and "retrieved knowledge" in _g_clean.detail
      and "declared scope" not in _g_clean.detail)
_g_floor = _gp({"decision": "pass", "reason": "corpus-supported", "ground": "corpus",
                "unsupported_claims": ["A B", "C D"]})
check("corpus pass with recorded spans ⇒ names them and 'below the trigger threshold'",
      "2 unsupported claims recorded" in _g_floor.detail and "below the trigger threshold" in _g_floor.detail)
_g_obs = _gp({"decision": "pass", "reason": "observe — unsupported claims recorded",
              "ground": "corpus", "unsupported_claims": ["A B"]})
check("…and 'observe mode' when that was the reason", "observe mode" in _g_obs.detail)
check("a scope pass keeps its scope wording",
      "declared scope" in _gp({"decision": "pass", "reason": "in scope", "ground": "scope"}).detail)

# ── [Q] every point declares the FATES it can cause, and they agree with its power ──
# The Guide's "can" line answers "does this withhold, or only classify?" from this table.
# A point missing here would render without the line (an honest absence) — but the
# table is the declaration, so a missing key is a gap this gate names. The tuple must
# agree with `power`: the coarse verb and the reader's words cannot tell two stories.
print("\n[Q] fates: declared for every point, drawn from the four words, in step with power")
_fk = set(AP.MECHANISM_FATES)
_rk = {p.key for p in AP.points(include_excluded=True)}
check(f"every registered point has a fates entry{' — missing ' + str(sorted(_rk - _fk)) if _rk - _fk else ''}",
      not (_rk - _fk))
check(f"no orphan fates for a key the registry lacks{' — ' + str(sorted(_fk - _rk)) if _fk - _rk else ''}",
      not (_fk - _rk))
check("every fate is one of the four words",
      all(set(v) <= set(AP.FATES) for v in AP.MECHANISM_FATES.values()))
_bad = []
for _p in AP.points(include_excluded=True):
    _f = set(AP.fates_of(_p.key))
    # A constraint may refuse a step, or (the trusted scope) merely DECLARE the boundary
    # the grounding gate judges against — "it acts on nothing", so an empty tuple is right.
    ok = {"attest": not _f,
          "constrain": _f <= {"refused"},
          "alter": bool(_f) and _f <= {"changed", "noted"},
          "withhold": bool(_f & {"stopped", "refused"})}.get(_p.power, False)
    if not ok:
        _bad.append((_p.key, _p.power, sorted(_f)))
check(f"fates agree with power{' — ' + str(_bad) if _bad else ''}", not _bad)
check("the outbound guard can block, so its power is withhold (its verdict set declares BLOCK)",
      next(p.power for p in AP.REGISTRY if p.key == "outbound_guard") == "withhold"
      and "stopped" in AP.fates_of("outbound_guard"))
check("the grounding gate can stop the answer and add a note, never change it",
      set(AP.fates_of("grounding_gate")) == {"stopped", "noted"})
check("fates are served on every Guide entry, an empty list included",
      all(isinstance((AP.guide_entry(p.key) or {}).get("fates"), list) for p in AP.REGISTRY))
check("a reporting point serves an empty list, not a missing key",
      (AP.guide_entry("provenance_seal") or {}).get("fates") == [])

# ── [R] the reading line is short: one fact per sentence, no sentence over the budget ──
# The three text segments the Guide shows (description, fires, settings) were paragraphs:
# four triggers, a threshold note, a second-chance rule and an exclusion in one block.
# Structure carries the facts now, and this gate keeps each sentence a reading-line
# sentence. The reasoning lives under `why`, which is folded and exempt.
print("\n[R] limits and reading rows stay short: no sentence over the word budget")
_BUDGET = 32
def _sentences(t):
    return [x.strip() for x in _re.split(r"(?<=[.;])\s+", t or "") if x.strip()]
_too_long = []
for _p in AP.points(include_excluded=True):
    _l = limits_of(_p.key); _f = _l.get("fires") or {}
    for _t in (_f.get("when") or []) + [_f.get(k) for k in ("then", "also", "ignores", "none") if _f.get(k)]:
        for _s in _sentences(_t):
            if len(_s.split()) > _BUDGET: _too_long.append((_p.key, _s[:50]))
    for _k in ("tunable", "fixed"):
        for _set, _eff in _l.get(_k) or []:
            if len(_eff.split()) > _BUDGET: _too_long.append((_p.key, _set))
    if len((AP.MECHANISM_NOTE.get(_p.key) or "").split()) > 3 * _BUDGET:
        _too_long.append((_p.key, "note"))
check(f"no limits sentence exceeds {_BUDGET} words{' — ' + str(_too_long[:4]) if _too_long else ''}",
      not _too_long)
check("every trigger is one line (no sentence break inside a `when` item)",
      all(len(_sentences(w)) == 1 for p in AP.REGISTRY
          for w in (limits_of(p.key).get("fires") or {}).get("when") or []))
# The description is ONE fact (the note), and "how it decides" is STEPS, one line each:
# the two segments that were paragraphs on the Honesty entry (55 + 116 words).
_NOTE_BUDGET, _STEP_BUDGET, _STEPS_MAX = 34, 32, 6
_long_notes = [(p.key, len(AP.MECHANISM_NOTE.get(p.key, "").split())) for p in AP.points(include_excluded=True)
               if len(AP.MECHANISM_NOTE.get(p.key, "").split()) > _NOTE_BUDGET]
check(f"every note is at most {_NOTE_BUDGET} words{' — ' + str(_long_notes) if _long_notes else ''}", not _long_notes)
_long_steps = [(p.key, len(st.split())) for p in AP.points(include_excluded=True)
               for st in AP.mechanism_how(p.key) if len(st.split()) > _STEP_BUDGET]
check(f"every how-step is at most {_STEP_BUDGET} words{' — ' + str(_long_steps) if _long_steps else ''}", not _long_steps)
check(f"no point has more than {_STEPS_MAX} steps",
      all(len(AP.mechanism_how(p.key)) <= _STEPS_MAX for p in AP.points(include_excluded=True)))
check("every point has at least one how-step, served on its entry",
      all(AP.mechanism_how(p.key) and (AP.guide_entry(p.key) or {}).get("how") == AP.mechanism_how(p.key)
          for p in AP.REGISTRY))
check("the derivation string is the steps joined (one declaration, two readers)",
      all(AP.mechanism_derivation(p.key) == " ".join(AP.mechanism_how(p.key)) for p in AP.REGISTRY))
check("the longest texts carry steps, not a paragraph",
      all(len(AP.mechanism_how(k)) >= 3 for k in ("honesty_gate", "attachment_sources", "prompt_cache", "outcome_loop")))
check("a station note is at most two sentences and 34 words",
      all(len(AP.STATION_NOTE[s].split()) <= 34 for s in AP.STATIONS))
# ONE slot, ONE fact: a how-step says HOW the verdict is produced; it never restates a
# trigger, a `then` or an `also` (the Honesty entry carried its three triggers twice).
# Measured as word overlap: a step sharing most of its words with a fires row is a
# restatement, whatever the wording.
_STOP = {"the", "a", "an", "of", "to", "in", "on", "and", "or", "is", "it", "its", "that", "for", "at", "by", "as", "with", "so"}
def _bag(t):
    return {w for w in _re.findall(r"[a-z][a-z'\-]+", (t or "").lower()) if w not in _STOP}
_restated = []
for _p in AP.points(include_excluded=True):
    _f = limits_of(_p.key).get("fires") or {}
    _fire_rows = list(_f.get("when") or []) + [_f.get(k) for k in ("then", "also") if _f.get(k)]
    for _st in AP.mechanism_how(_p.key):
        _b = _bag(_st)
        for _row in _fire_rows:
            _r = _bag(_row)
            if _b and _r and len(_b & _r) / min(len(_b), len(_r)) >= 0.6:
                _restated.append((_p.key, _st[:40], _row[:40]))
check(f"no how-step restates a fires row{' — ' + str(_restated[:3]) if _restated else ''}", not _restated)

# ── [S] a station says HOW it reads, at both altitudes, and the ring rule is the ring's ──
# The station description said what does NOT colour it. `reads` says what does: on a
# turn (the dial), on the ring (configuration · execution, worst-of, raised by open
# items), and where its checks count. The ring sentence is pinned to the function that
# implements it, so a change to `_attention` cannot leave the Guide describing a rule
# the ring no longer applies.
print("\n[S] every station carries its three reading rows, and the ring row matches the ring")
_reads = {s: AP.station_reads(s) for s in AP.STATIONS}
check("every station has answer, agent and checks blocks",
      all(set(_reads[s]) == {"answer", "agent", "checks"} and _reads[s]["checks"]
          and _reads[s]["answer"]["rows"] and _reads[s]["agent"]["rows"] for s in AP.STATIONS))
check("…each block names its surface (the sub-label under the lead)",
      all(_reads[s]["answer"]["where"] == "Live Status dial" and _reads[s]["agent"]["where"].startswith("Trust Circle")
          for s in AP.STATIONS))
# Phrases joined by `·` and `→`, never sentences: the separators do not count as words,
# and no phrase chain runs past 24 words.
_phrase_words = lambda t: [w for w in t.split() if w not in ("·", "→")]
_long_rows = [(s, r[0]) for s in AP.STATIONS for b in ("answer", "agent") for r in _reads[s][b]["rows"]
              if not (isinstance(r, list) and len(r) == 2 and all(r) and len(_phrase_words(r[1])) <= 24)]
check(f"…and every row is a (label, phrase) pair of phrases, not sentences{' — ' + str(_long_rows) if _long_rows else ''}",
      not _long_rows)
check("the station entry serves them, and its description is the note alone",
      all((AP.station_entry(s) or {}).get("reads") == _reads[s]
          and (AP.station_entry(s) or {}).get("long") == AP.STATION_NOTE.get(s) for s in AP.STATIONS))
_aph_src = open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                             "handlers", "agentPerformanceHandler.js")).read()
_att = _aph_src[_aph_src.index("function _attention("):_aph_src.index("function _driver(")]
check("the ring folds configuration and execution worst-of (the function the row describes)",
      "worst" in _att and "designState" in _att and "execState" in _att)
check("…and open items can only raise it (`_assemble`)",
      "They can only RAISE attention" in _aph_src and "openItems" in _aph_src)
_agent_labels = {s: [r[0] for r in _reads[s]["agent"]["rows"]] for s in AP.STATIONS}
check("every station's agent block carries the ring's two readings, asked as questions, first",
      all(_agent_labels[s][0].startswith("configuration") and "could it act?" in _agent_labels[s][0]
          and _agent_labels[s][1].startswith("execution") and "what did it do?" in _agent_labels[s][1]
          for s in AP.STATIONS))
check("…and the two rules the ring applies to every station: several agents · an open finding",
      all("several agents" in _agent_labels[s] and "open finding" in _agent_labels[s] for s in AP.STATIONS))
check("…and the guarantee blocks say the checks count at JUDGED, with the partition's count",
      all(_reads[s]["checks"].startswith(f"{len(AP.station_dimensions().get(s) or ())} ·")
          and "JUDGED" in _reads[s]["checks"] for s in ("grounded", "guarded", "bounded", "proven")))
check("the sentence 'the lane cut' and other history words stay out of the rows",
      not any(_PAST.search(t) for s in AP.STATIONS for t in AP.reads_text(s)))

# ── [T] a term is used on screen only if the screen explains it — no exception ───────
# The rows speak the reader's words. The code's own words — state tokens (na · warn ·
# pending · danger · pass), fold names (worst-of · aggregated · posture · lane · partition),
# crypto and scheduler names (Ed25519 · breaker-open · retry-after · backoff) — never
# reach a row, a note or a reading. The technical words a row may still need (judge ·
# chunk · grain · window · Assurance Inbox …) are defined ONCE in GUIDE_TERMS and served
# with the vocabulary, where the Guide's glossary renders them.
print("\n[T] rows use no word the screen does not explain")
_viol = []
for _p in AP.points(include_excluded=True):
    for _t in AP.limits_text(_p.key):
        _m = AP.ROW_BANNED.search(_t)
        if _m: _viol.append((_p.key, _m.group(0)))
    _m = AP.ROW_BANNED.search(AP.MECHANISM_NOTE.get(_p.key, ""))
    if _m: _viol.append((_p.key, "note:" + _m.group(0)))
for _s in AP.STATIONS:
    for _t in AP.reads_text(_s):
        _m = AP.ROW_BANNED.search(_t)
        if _m: _viol.append((_s, "reads:" + _m.group(0)))
    _m = AP.ROW_BANNED.search(AP.STATION_NOTE.get(_s, ""))
    if _m: _viol.append((_s, "note:" + _m.group(0)))
check(f"no row, note or reading uses a banned internal word{' — ' + str(_viol[:5]) if _viol else ''}", not _viol)
_terms = AP.guide_terms()
check("the Terms glossary is served, non-empty, each with a definition",
      len(_terms) >= 15 and all(t["term"] and len(t["def"].split()) >= 4 for t in _terms))
check("…and the ring's two readings are asked as questions on every agent block",
      all(any("could it act?" in l for l in _agent_labels[s]) and any("what did it do?" in l for l in _agent_labels[s])
          for s in AP.STATIONS))
check("…and the colour words replace the state tokens on the answer blocks",
      all(any(w in " ".join(v for _, v in _reads[s]["answer"]["rows"])
              for w in ("green", "red", "amber", "withheld", "sealed", "score"))
          for s in AP.STATIONS))
_qd_src = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "handlers", "qualityDimensions.js")).read()
_rr_src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "api", "rest_routes.py")).read()
check("the vocabulary endpoint serves the terms and the Guide renders them",
      '"terms": _terms' in _rr_src and "registerTerms(d.terms)" in _qd_src and "Terms used on the rows" in _qd_src)
# Every term the rows actually use is in the table (the words a reader meets that are not
# plain language). Checked by name: a term dropped from the table while a row still uses
# it is exactly the defect this section exists to catch.
_term_names = {t["term"].lower() for t in _terms}
_all_rows = " ".join(t for p in AP.points(include_excluded=True) for t in AP.limits_text(p.key))
_all_rows += " ".join(t for s in AP.STATIONS for t in AP.reads_text(s))
_used = [w for w in ("judge", "chunk", "grain", "data product", "contract", "agent authority", "master switch",
                     "window", "assurance inbox", "signature", "sealed", "receipt", "disclosure", "parked",
                     "connector", "quarantine", "breaker", "cache", "lineage", "fallback")
         if w in _all_rows.lower()]
_undefined = [w for w in _used if w not in _term_names]
check(f"every technical word the rows use is a defined term{' — undefined: ' + str(_undefined) if _undefined else ''}",
      not _undefined)

# ── [P2] the gate's reading names the finding it acted on; a module's role is a mark ──
# prod 510f7011 (2026-10-30): a Focus turn annotated for ONE unmatched statement read
# "out-of-scope references — answer annotated" (the wrong family), and the three modules
# beneath it carried no sign that the gate had consulted them during the turn.
print("\n[P2] the gate's reading is the finding, in its family's words; a module that decides the gate is marked")
_a_corpus = _gp({"decision": "annotate", "reason": "flagged", "ground": "corpus",
                 "unsupported_claims": ["-1"], "claims_checked": 13})
check("annotate on a corpus finding ⇒ '1 of 13 statements unmatched — annotated' (never 'out-of-scope')",
      _a_corpus.state == "warn" and _a_corpus.acted and "1 of 13 statements unmatched — annotated" in _a_corpus.detail
      and "out-of-scope" not in _a_corpus.detail)
_a_scope = _gp({"decision": "annotate", "reason": "flagged", "ground": "scope", "out_of_scope_dbs": ["hr_db"]})
check("annotate on a scope finding ⇒ 'out of scope: hr_db — annotated'", "out of scope: hr_db — annotated" in _a_scope.detail)
_b = _gp({"decision": "block", "reason": "judge", "ground": "both", "unsupported_claims": ["x", "y"], "claims_checked": 4,
          "ungrounded_numbers": ["42"], "out_of_scope_dbs": ["a"], "answer_oos_refs": ["b"]})
check("block ⇒ 'withheld — ' + every family that found something, '·'-joined, sources merged and sorted",
      _b.state == "danger" and _b.detail == "Grounding gate: withheld — 2 of 4 statements unmatched · 1 figure unmatched · out of scope: a, b")
check("nothing recorded ⇒ the honest fallback words", "withheld — drift confirmed" in _gp({"decision": "block"}).detail
      and "flagged — annotated" in _gp({"decision": "annotate"}).detail)
from trusted_data_agent.agent.assurance import INFLIGHT_CONSUMERS as _IC
_rows = _am("grounding_gate", {"profile_type": "rag_focused", "final_summary_text": "Returns within 30 days.",
                               "knowledge_retrieval_event": {"documents": [{"text": "Returns within 30 days."}]}})
check("every module row says whether THIS gate consults it in flight — derived from INFLIGHT_CONSUMERS, never listed",
      len(_rows) == 3 and all(r.get("decides") == (_IC.get(r["property"]) == "grounding_gate") for r in _rows)
      and all(r.get("decides") for r in _rows))

# ── [P3] what a mechanism consults during the turn — one helper, two surfaces ───────────
print("\n[P3] consulted_properties: the dial's mark and the Guide's fold read one declaration")
from trusted_data_agent.agent.assurance import INFLIGHT_CONSUMERS as _IC3
check("the grounding gate consults all three of its properties in flight",
      AP.consulted_properties("grounding_gate") == ("answer_support", "answer_scope", "context_use"))
check("Trusted scope owns answer_scope but does not consult it — the grounding gate does",
      "answer_scope" in AP.POINT_PROPERTIES["trusted_scope"] and AP.consulted_properties("trusted_scope") == ())
check("the helper is exactly INFLIGHT_CONSUMERS restricted to the mechanism's own properties, for every mechanism",
      all(set(AP.consulted_properties(k)) == {p for p in ps if _IC3.get(p) == k} for k, ps in AP.POINT_PROPERTIES.items()))
_ge = AP.guide_entry("grounding_gate") or {}
check("the Guide entry serves it as `consults`, in the Guide's property keys",
      _ge.get("consults") == ["property_answer_support", "property_answer_scope", "property_context_use"])
check("the gate's description names both checks (sources · declared scope), not the scope family alone",
      "sources" in (_ge.get("desc") or _ge.get("long") or "") and "declared scope" in (_ge.get("desc") or _ge.get("long") or ""))

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
