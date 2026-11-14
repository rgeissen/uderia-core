#!/usr/bin/env python3
"""Turn a CONTROL GAP into a control proposal — the half of a registry entry the finding
already knows.

    python3 test/trust_campaign/propose.py                  # newest run's I10 gaps
    python3 test/trust_campaign/propose.py <run-dir>

For every case where the bait LANDED and no targeted control named it (invariant I10),
writes `test/trust_campaign/proposals/CONTROL-<engine>-<case>.md`: the station the
control would be evidence for, the power it would need, the stamp it would read, the
measurement it would own, the classes it applies to, and the turns that prove the gap.
Written only on evidence — no gap, no file. An existing proposal is left untouched (it
may carry triage notes); re-run with --force to regenerate.

The ladder a proposal climbs (SKILL.md): a measurement first (post-hoc, calibrated on the
campaign's saved turns — `calibrate.py`), then an assurance module in observe, then a
builtin `AssurancePoint` — at which moment the coverage gate demands a case per class,
which the proposal already names.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agents import ENGINE_LABEL  # noqa: E402
from suite import CASES, answer_text  # noqa: E402

OUT = os.path.join(HERE, "proposals")
POWER_FOR_STATION = {"bounded": "constrain", "grounded": "alter", "guarded": "withhold",
                     "reviewed": "withhold", "proven": "attest", "signed": "attest"}


def gaps(rs):
    for r in rs:
        for x in (r.get("invariants") or []):
            if x["id"] == "I10" and not x["ok"]:
                yield r, x


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    d = args[0] if args else sorted(glob.glob("tda_eval/trust_campaign/*/"))[-1]
    rs = json.load(open(os.path.join(d, "raw.json")))
    by_id = {c["id"]: c for c in CASES}
    os.makedirs(OUT, exist_ok=True)
    n = 0
    for r, x in gaps(rs):
        c = by_id.get(r["id"]) or {}
        path = os.path.join(OUT, f"CONTROL-{r['engine']}-{r['id']}.md")
        if os.path.exists(path) and "--force" not in argv:
            print(f"  kept     {path}")
            continue
        t = r.get("turn") or {}
        a = t.get("assurance") or {}
        quiet = [f"{p['key']}={p['state']}" for p in (a.get("points") or [])
                 if p.get("applies", True) and p.get("station") == c.get("station")]
        station = c.get("station") or "?"
        lines = [
            f"# Control proposal — {ENGINE_LABEL.get(r['engine'], r['engine'])} · {r['id']}",
            "",
            f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} from "
            f"`{d}` by `propose.py`. A gap the campaign found, not a decision: triage it, "
            f"then climb the ladder (measurement → module in observe → builtin)._",
            "",
            "## The gap",
            "",
            f"**Bait:** {c.get('prompt', '')}",
            "",
            f"**What landed:** {x['detail']}",
            "",
            f"**Answer (first 400c):** {answer_text(t)[:400].strip()}",
            "",
            f"**The station's own readings on that turn:** {', '.join(quiet) or '—'}",
            "",
            f"**Targeted controls that stayed quiet:** {', '.join(c.get('targets') or ()) or '—'}",
            "",
            "## The control this would need",
            "",
            "| Field | Proposal |",
            "|---|---|",
            f"| station | `{station}` — {c.get('note', '')[:120]} |",
            f"| power | `{POWER_FOR_STATION.get(station, 'constrain')}` (alter · withhold · constrain · attest) |",
            f"| key | `<name it>` — the registry key; the turn stamp `turn_key` it reads |",
            f"| applies_to | `{r['engine']}` and every class the same bait can land on — run `run_campaign.py --case {r['id']}` on all |",
            f"| measurement it owns | `<dimension>` at JUDGED, labelled `{station}` (`station_dimensions()`) |",
            f"| trigger | what deterministically identifies this failure shape on the turn (start from the `landed` detector in suite.py: `{getattr(c.get('landed'), '__name__', '—')}`) |",
            f"| evidence | session `{r.get('session_id')}` turn 1 · `{d}/raw.json` · case `{r['id']}` on `{r['engine']}` |",
            "",
            "## The ladder",
            "",
            "1. **Measurement** — write the scorer; calibrate on every saved campaign run "
            "(`calibrate.py <scorer>`): it must fire on the landed turns and stay quiet where the "
            "bait did not land, per engine.",
            "2. **Module in observe** — bind it in flight, recording only; re-run the campaign; the "
            "readings must agree with `landed` on every engine.",
            "3. **Builtin** — register the `AssurancePoint`; the coverage gate then demands a case per "
            f"class — `{r['id']}` already targets it; add the key to its `targets`.",
            "",
            "## Triage",
            "",
            "- [ ] bin: control gap · existing control's `applies_to` · lane that skips a gate · posture/config",
            "- [ ] decision:",
        ]
        open(path, "w").write("\n".join(lines) + "\n")
        print(f"  written  {path}")
        n += 1
    print(f"{n} proposal(s) written" if n else "no control gap in this run — nothing proposed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
