#!/usr/bin/env python3
"""Replay a rewrite pass over every SAVED strategic plan — rung 1 of the ladder, at no cost.

    python3 test/optimize_campaign/replay.py placeholder_sql
    python3 test/optimize_campaign/replay.py placeholder_sql --show 8

Every persisted turn keeps the planner's raw plan (`raw_llm_plan`). A candidate pass is a pure
function on a plan, so it can be run over hundreds of saved plans before it ever touches a
live turn: how many plans it would change, which cases and levers, and — beside each — what
the turn actually did (the oracle's verdict, the self-corrections paid), so the change can be
read as "would have spared this" or "would have touched a plan that was fine".
"""
from __future__ import annotations

import copy
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))


def load_plan(raw):
    plan = raw.get("raw_llm_plan") or raw.get("original_plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:  # noqa: BLE001
            return None
    return plan if isinstance(plan, list) else None


def run_pass(name, plan, tools):
    from trusted_data_agent.agent import executor as ex
    from trusted_data_agent.agent import planner as pl
    exe = ex.PlanExecutor.__new__(ex.PlanExecutor)
    exe.meta_plan = copy.deepcopy(plan)
    exe.dependencies = {"STATE": {"mcp_tools": {t: {} for t in tools}}}
    exe._log_system_event = lambda ev: None
    exe._format_sse_with_depth = lambda ev, *a, **k: ev
    exe.session_id = None
    exe.rag_case_count = None
    p = pl.Planner.__new__(pl.Planner)
    p.executor = exe
    fn = getattr(p, f"_rewrite_plan_for_{name}", None) or getattr(p, f"_rewrite_{name}", None)
    if fn is None:
        sys.exit(f"no pass named {name!r} on the planner")
    events = list(fn())
    return exe.meta_plan, events


def main(argv):
    if not argv:
        sys.exit(__doc__)
    name = argv[0]
    show = int(argv[argv.index("--show") + 1]) if "--show" in argv else 0
    tools = ("base_readQuery", "base_databaseList", "base_tableList", "base_columnDescription", "TDA_DatabaseNavigator")
    seen = set()
    rows = []
    for f in sorted(glob.glob(os.path.join("tda_eval", "optimize_campaign", "*", "raw.json"))):
        for r in json.load(open(f)):
            for t in r.get("turns") or []:
                raw = t.get("raw")
                plan = load_plan(raw or {})
                if not plan:
                    continue
                key = json.dumps(plan, sort_keys=True)
                dup = key in seen
                seen.add(key)
                after, ev = run_pass(name, plan, tools)
                changed = after != plan
                rows.append({"lever": r.get("lever"), "case": r.get("id"), "run": os.path.basename(os.path.dirname(f)),
                             "changed": changed, "dup": dup, "correct": t.get("correct"),
                             "sc": t.get("self_corrections"), "plan": plan, "after": after})
    uniq = [x for x in rows if not x["dup"]]
    ch = [x for x in uniq if x["changed"]]
    print(f"pass {name}: {len(rows)} saved plans ({len(uniq)} distinct) · would change {len(ch)}")
    spared = [x for x in ch if x["correct"] is not True]
    fine = [x for x in ch if x["correct"] is True]
    print(f"  of the changed: {len(spared)} turns were wrong/declined (would have been spared), "
          f"{len(fine)} were right anyway (the change must not hurt them)")
    sc = sum(int(x["sc"] or 0) for x in spared)
    print(f"  self-corrections paid on the spared turns: {sc}")
    for x in ch[:show]:
        sql = next(((p.get("arguments") or {}).get("sql") for p in x["plan"] if (p.get("arguments") or {}).get("sql")), "")
        print(f"  {x['lever']:11s} {x['case']:4s} {x['run']} correct={x['correct']} sc={x['sc']} | {str(sql)[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
