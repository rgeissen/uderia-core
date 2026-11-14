"""Every Assurance Guide entry sits under at least one parent — the committed gate.

    PYTHONPATH=src python test/test_guide_parents.py

WHY THIS EXISTS. Twenty entries were roots by accident: the twelve properties (nothing
said which mechanism acts on them), the seven moments (JUDGED counted them, but only
JUDGED's entry said so), and `memory_firewall` (it declared GUARDED, GUARDED did not list
it). A reader who opened one of them could read it and had nowhere upward to go.

THE RULE IT PINS. Nothing stores a parent. Every relation is served once, ON THE PARENT,
as its list of children — a guarantee's `controls` and `counts`, a mechanism's `dims` and
`properties`, a moment's measures — and the Guide's `parentsOf` reads those lists
backwards. So the gate checks two things and needs both:

  1. the SERVED side — the real vocabulary endpoint, built in-process: every child list
     points at an entry that exists, and no entry carries a `parents` field (a second
     copy of a relation, free to disagree with the first);
  2. the RENDERED side — the shipped renderer, fed that payload under node: every entry
     except a guarantee has a parent, and the parent is on the entry as a row.

A guarantee is the root of the tree and has none; that is asserted too, so a future
"parent" for the eight stations is a decision, not a drift. No server, no network.
"""
import asyncio, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}")


# ── 1 · the served vocabulary ────────────────────────────────────────────────
print("\n── the served vocabulary")
from quart import Quart  # noqa: E402
from trusted_data_agent.api import rest_routes as _rr  # noqa: E402

_fn = _rr.assurance_vocabulary
while hasattr(_fn, "__wrapped__"):          # past @require_auth: the payload is static
    _fn = _fn.__wrapped__


async def _payload():
    async with Quart("guide-parents").app_context():
        resp, _code = await _fn({"id": "gate"})
        return await resp.get_json()

VOCAB = asyncio.run(_payload())
ENTRIES = VOCAB.get("entries") or []
KEYS = {e["k"] for e in ENTRIES}
check(f"the vocabulary serves entries ({len(ENTRIES)})", len(ENTRIES) > 0)
check("no entry stores its parents — a parent is a child list read backwards",
      not [e["k"] for e in ENTRIES if "parents" in e])
for field in ("controls", "counts", "properties"):
    dangling = sorted({c for e in ENTRIES for c in (e.get(field) or ()) if c not in KEYS})
    check(f"every `{field}` child is a served entry{' — ' + str(dangling) if dangling else ''}",
          not dangling)
_props = sorted(e["k"] for e in ENTRIES if e.get("cls") == "property")
_claimed = {p for e in ENTRIES for p in (e.get("properties") or ())}
check("every property is claimed by a mechanism's `properties`"
      + (f" — unclaimed {sorted(set(_props) - _claimed)}" if set(_props) - _claimed else ""),
      _props and set(_props) <= _claimed)
_ex = [e["k"] for e in ENTRIES if e.get("excluded")]
check("an excluded mechanism is flagged, so the Guide can mark it off a turn's path",
      "memory_firewall" in _ex)


# ── 2 · the rendered Guide ───────────────────────────────────────────────────
print("\n── the rendered Guide")
_JS = r"""
import fs from 'fs';
const [, , srcPath, vocabPath] = process.argv;
let src = fs.readFileSync(srcPath, 'utf8');
src = src.replace('window.QualityDimensions = {',
  'window.__qd = { DIM, classOf, rowHtml, setShared: (m) => { SHARED_MODULES = m; } }; window.QualityDimensions = {');
class El { constructor() { this.children = []; this.attrs = {}; this.style = {}; this.dataset = {};
  this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } }; }
  appendChild(c) { return c; } querySelector() { return null; } querySelectorAll() { return []; }
  addEventListener() {} setAttribute() {} getAttribute() {} }
const document = { getElementById: () => null, createElement: () => new El(), head: new El(), body: new El(),
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, readyState: 'complete' };
const window = { addEventListener() {}, removeEventListener() {} };
new Function('window', 'document', 'localStorage', 'fetch', 'CSS', src)(
  window, document, { getItem: () => null }, () => Promise.reject(new Error('no net')), { escape: (s) => s });
const QD = window.QualityDimensions, I = window.__qd;
const d = JSON.parse(fs.readFileSync(vocabPath, 'utf8'));
QD.registerEntries(d.entries); QD.registerStages(d.stages); I.setShared(d.shared_modules || {});
const out = {};
for (const k of Object.keys(I.DIM)) {
  const html = I.rowHtml(k, null);
  out[k] = { cls: I.classOf(k), parents: QD.parentsOf(k),
             chips: [...html.matchAll(/qd-rel-chip[^"]*" data-dim="([^"]+)"/g)].map((m) => m[1])
                    .concat([...html.matchAll(/qd-st-chip" data-dim="([^"]+)"/g)].map((m) => m[1])),
             offPath: /·not on a turn/.test(html),
             leads: [...html.matchAll(/qd-rel-lead[^>]*>([^<]*)</g)].map((m) => m[1]),
             members: (() => { const i = html.indexOf('qd-mem"'); if (i < 0) return null;
               const j = html.indexOf('not members', i);
               return [...html.slice(i, j < 0 ? html.length : j).matchAll(/qd-rel-chip[^"]*" data-dim="([^"]+)"/g)].map((m) => m[1]); })(),
             memberCount: ((html.match(/qd-mem"><div class="qd-rel-grp-h[^>]*>members? \u00b7 (\d+)/) || [])[1] || null),
             memberHead: ((html.match(/qd-mem"><div class="qd-rel-grp-h[^>]*>(members? \u00b7 [\s\S]*?)<\/div>/) || [])[1] || ''),
             ruleCount: ((html.match(/<dd>worst (?:grade )?of its (?:members|measures) \u00b7 (\d+)<\/dd>/) || [])[1] || null) };
}
console.log(JSON.stringify(out));
"""
with tempfile.TemporaryDirectory() as _tmp:
    _vp, _sp = os.path.join(_tmp, "vocab.json"), os.path.join(_tmp, "parents.mjs")
    open(_vp, "w").write(json.dumps(VOCAB, default=str))
    open(_sp, "w").write(_JS)
    _res = subprocess.run(["node", _sp, os.path.join(ROOT, "static", "js", "handlers",
                                                     "qualityDimensions.js"), _vp],
                          capture_output=True, text=True, timeout=60)
check("the shipped renderer runs on the real vocabulary"
      + (f" — {_res.stderr.strip()[-300:]}" if _res.returncode else ""), _res.returncode == 0)
R = json.loads(_res.stdout.strip().splitlines()[-1]) if _res.returncode == 0 else {}

_non_root = {k: v for k, v in R.items() if v["cls"] != "station"}
_orphans = sorted(k for k, v in _non_root.items() if not v["parents"])
check(f"every entry but a guarantee has a parent ({len(_non_root)} entries)"
      + (f" — orphans {_orphans}" if _orphans else ""), R and not _orphans)
_unshown = sorted(k for k, v in _non_root.items()
                  if v["parents"] and not set(v["parents"]) & set(v["chips"]))
check("…and the parent is on the entry as a row, one click upward"
      + (f" — not rendered on {_unshown}" if _unshown else ""), R and not _unshown)
_rooted = sorted(k for k, v in R.items() if v["cls"] == "station" and v["parents"])
check("a guarantee is a root: the eight stations have no parent"
      + (f" — {_rooted}" if _rooted else ""),
      sum(1 for v in R.values() if v["cls"] == "station") == 8 and not _rooted)

# The classes that were roots by accident, by name — a regression names its class.
for cls, want in (("property", "mechanism"), ("stage", "station_"), ("measure", None)):
    _bad = sorted(k for k, v in R.items() if v["cls"] == cls and not v["parents"])
    check(f"every {cls} has a parent" + (f" — {_bad}" if _bad else ""),
          any(v["cls"] == cls for v in R.values()) and not _bad)
check("a property's parent is the mechanism that acts on it (answer_scope ← grounding gate)",
      "grounding_gate" in (R.get("property_answer_scope") or {}).get("parents", ()))
check("a moment's parent is JUDGED, the guarantee that counts it",
      (R.get("stage_compose") or {}).get("parents") == ["station_scored"])
check("memory_firewall sits under GUARDED", "station_guarded"
      in (R.get("memory_firewall") or {}).get("parents", ()))
check("…and GUARDED marks it off a turn's path", (R.get("station_guarded") or {}).get("offPath"))

# ── 3 · the member names its guarantee in its own kind's verb ────────────────
# The return label was a constant `upholds`, so a measure read "upholds SIGNED" while SIGNED
# listed it under "evidenced by" — one relation, two roles. The pair comes from one table.
print("\n── the member's verb")
_VERB = {"control": "upholds", "boundary": "bounds", "attestation": "attests",
         "measure": "evidence for"}
_by_k = {e["k"]: e for e in ENTRIES}
_wrong = sorted(k for k, v in R.items() if _by_k.get(k, {}).get("station") and v["cls"] in _VERB
                and v["cls"] != "station" and _VERB[v["cls"]] not in v["leads"])
check("every mechanism names its guarantee in its kind's verb"
      + (f" — {_wrong}" if _wrong else ""), R and not _wrong)
_leaks = sorted(k for k, v in R.items() if v["cls"] in ("measure", "attestation", "boundary")
                and _by_k.get(k, {}).get("station") and "upholds" in v["leads"])
check("…so only a control says `upholds`" + (f" — {_leaks}" if _leaks else ""), R and not _leaks)
check("Recovery is evidence for SIGNED, beside Prompt cache",
      all("evidence for" in (R.get(k) or {}).get("leads", ()) and "station_signed"
          in (R.get(k) or {}).get("parents", ()) for k in ("llm_recovery", "prompt_cache")))
check("…and SIGNED lists both under `evidenced by`, not `upheld by`",
      "evidenced by" in (R.get("station_signed") or {}).get("leads", ())
      and "upheld by" not in (R.get("station_signed") or {}).get("leads", ()))

# ── 4 · a guarantee's members are one group, and the colour rule points at it ──
print("\n── a guarantee's members")
from trusted_data_agent.agent import assurance_points as _AP  # noqa: E402
_WORST = {}
for _st in _AP.STATIONS:
    _ans = (_AP.station_reads(_st) or {}).get("answer") or {}
    _WORST[f"station_{_st}"] = any(v == "worst of its members" for _l, v in _ans.get("rows", ()))
check("the multi-member guarantees state the colour rule the renderer marks",
      all(_WORST[f"station_{s}"] for s in ("signed", "grounded", "guarded", "bounded")))
_graded = {k: set(v["chips"]) for k, v in R.items()}
for k, v in sorted(R.items()):
    if v["cls"] != "station" or k == "station_scored":
        continue
    want = sorted(_by_k[k].get("controls") or [])
    check(f"{k}: the members group holds exactly its members ({len(want)})",
          sorted(v["members"] or []) == want)
    if _WORST.get(k):
        check(f"{k}: the group's count and the colour rule's count both say {len(want)}",
              v["memberCount"] == str(len(want)) == v["ruleCount"]
              and "the worst of these colours" in v["memberHead"])
    else:   # its reading is stated in its own words — the header claims no rule it lacks
        check(f"{k}: a single member, its header claiming no worst-of rule",
              len(want) == 1 and v["ruleCount"] is None and "the worst of these" not in v["memberHead"]
              and "member \u00b7 1" in v["memberHead"])
    _dims = set(_by_k[k].get("dims") or [])
    check(f"{k}: no measure graded about it is inside the members group",
          not (_dims & set(v["members"] or [])))
# JUDGED's members are the measures it counts, shelved by moment — so membership is ONE
# partition: every mechanism under its guarantee, every scored measure under JUDGED.
_stage_of = (VOCAB.get("stages") or {}).get("dimensions") or {}
_J = R.get("station_scored") or {}
check(f"JUDGED's members group counts every scored measure ({len(_stage_of)})",
      _stage_of and _J.get("memberCount") == str(len(_stage_of)) == _J.get("ruleCount")
      and "the worst grade of these colours JUDGED" in _J.get("memberHead", ""))
check("…shelved by every moment, each moment a chip one click from its measures",
      sorted(_J.get("members") or []) == sorted(_by_k["station_scored"].get("counts") or []))
_mech_members = {m for k, v in R.items() if v["cls"] == "station" and k != "station_scored"
                 for m in (v["members"] or [])}
check("the partition: no scored measure is a member of a guarantee",
      not (_mech_members & set(_stage_of)))
_all_mechs = sorted(m for st in _AP.STATIONS if st != _AP.MEASUREMENT_STATION
                    for m in (_by_k.get(f"station_{st}", {}).get("controls") or []))
check("…and every mechanism is a member of exactly one guarantee",
      len(_all_mechs) == len(set(_all_mechs)) == len({p.key for p in _AP.points(include_excluded=True)}))

# ── 5 · a pass line belongs to a scored measure, never to a mechanism ─────────
print("\n── thresholds")
_MECH = ("control", "boundary", "attestation", "measure")
_mechs = sorted(k for k, v in R.items() if _by_k.get(k, {}).get("cls") in _MECH)
_thr = sorted(k for k in _mechs if "threshold" in R[k]["leads"])
check(f"no mechanism renders a threshold row ({len(_mechs)} mechanisms)"
      + (f" — {_thr}" if _thr else ""), _mechs and not _thr)

# ── 6 · the checks a window aggregate weights are named, read from the computation ──
# Trailing trust's "how it reads" said the trust-critical checks carry the configured weight
# and never said which. The list is served (`weights`, from `trust_behavior.DEFAULTS`), so
# the Guide cannot drift from the blend — and the relation is named at both ends.
print("\n── the weights")
from trusted_data_agent.eval.trust_behavior import DEFAULTS as _TB  # noqa: E402
_want = list(_TB["trust_dims"])
_served = _by_k.get("trailing_trust", {}).get("weights") or []
check(f"trailing trust serves its weights as the computation lists them ({len(_want)})",
      _served == _want and len(_want) >= 2)
check("every weighted check is a served scored measure",
      _served and all(k in _stage_of for k in _served))
check("no other mechanism serves weights",
      not [k for k, e in _by_k.items() if k != "trailing_trust" and e.get("weights")])
_TT = R.get("trailing_trust") or {}
check("the entry renders a `weights` row carrying every one of them",
      "weights" in _TT.get("leads", ()) and set(_want) <= set(_TT.get("chips", ())))
check("…and no `reads` row: a window aggregate has no module",
      "reads" not in _TT.get("leads", ()))
_back = sorted(k for k in _want if "weighs on" not in (R.get(k) or {}).get("leads", ())
               or "trailing_trust" not in (R.get(k) or {}).get("chips", ()))
check("each weighted check says `weighs on · Trailing trust state` back"
      + (f" — missing on {_back}" if _back else ""), R and not _back)
_stray = sorted(k for k, v in R.items() if "weighs on" in v["leads"] and k not in _want)
check("…and no other entry does" + (f" — {_stray}" if _stray else ""), not _stray)

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
