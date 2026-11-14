"""Eval CLI — "evals as unit tests".

    PYTHONPATH=src python -m trusted_data_agent.eval.cli <command>

Commands:
    list                          list datasets and recent runs
    mine    --dataset D [--user U] [--limit N]   seed cases from production traces
    run     --dataset D [--k K] [--base-url URL] [--run-id ID] [--baseline ID]
    gate    --run ID [--baseline ID] [--min-pass-k F] [--cohort C=F ...]
    report  --run ID

Exit code is non-zero when ``gate`` fails — wire it into CI.
"""
from __future__ import annotations

import sys
import json
import time
import asyncio
import argparse

from trusted_data_agent.eval.store import get_eval_store
from trusted_data_agent.eval import scorers as _scorers  # noqa: F401 (registers)


def _cmd_list(args) -> int:
    store = get_eval_store()
    print("Datasets:")
    for d in store.list_datasets():
        print(f"  {d['id']:<24} v{d['version']}  {d['name']}  (hash {d['content_hash']})")
    return 0


def _cmd_mine(args) -> int:
    from trusted_data_agent.eval.mining import mine_cases_from_sessions
    store = get_eval_store()
    n = asyncio.get_event_loop().run_until_complete(
        mine_cases_from_sessions(store, args.dataset, user_uuid=args.user,
                                 only_anomalous=not args.all, limit=args.limit))
    print(f"Seeded {n} case(s) into '{args.dataset}'.")
    return 0


def _cmd_run(args) -> int:
    from trusted_data_agent.eval.runner import run_dataset, report_markdown
    from trusted_data_agent.eval.flow_runner import CompositeEvalExecutor
    store = get_eval_store()
    # Composite routes flow-agent cases to the flow executor, others to the turn
    # executor — a dataset may mix both agent types.
    executor = CompositeEvalExecutor(base_url=args.base_url, username=args.user or "admin",
                                     password=args.password or "admin")
    run_id = args.run_id or f"run-{args.dataset}-{int(time.time())}"
    loop = asyncio.get_event_loop()
    judge_fn = None
    if args.judges:
        from trusted_data_agent.eval.judge import build_panel
        from trusted_data_agent.eval.scorers.judges import make_judge_fn
        panel = loop.run_until_complete(build_panel(user_uuid=None, max_models=args.judge_models))
        judge_fn = make_judge_fn(panel)
    _, summary = loop.run_until_complete(
        run_dataset(store, args.dataset, executor, k=args.k, run_id=run_id,
                    baseline_run_id=args.baseline, include_judges=args.judges,
                    judge_fn=judge_fn))
    print(report_markdown(store, run_id))
    return 0


def _parse_cohorts(pairs) -> dict:
    out = {}
    for p in (pairs or []):
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = float(v)
    return out


def _cmd_gate(args) -> int:
    from trusted_data_agent.eval.gate import gate_run
    store = get_eval_store()
    result = gate_run(store, args.run, baseline_run_id=args.baseline,
                      min_pass_k=args.min_pass_k,
                      cohort_thresholds=_parse_cohorts(args.cohort))
    print(json.dumps(result, indent=2))
    if result["failures"]:
        print(f"\nGATE FAILED ({len(result['failures'])} failure(s)):")
        for f in result["failures"]:
            print(f"  ✗ {f}")
    for w in result.get("warnings", []):
        print(f"  ! {w}")
    return 0 if result["passed"] else 1


def _cmd_report(args) -> int:
    from trusted_data_agent.eval.runner import report_markdown
    print(report_markdown(get_eval_store(), args.run))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eval", description="Uderia agent evaluation")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=_cmd_list)

    m = sub.add_parser("mine"); m.set_defaults(func=_cmd_mine)
    m.add_argument("--dataset", required=True)
    m.add_argument("--user", default=None)
    m.add_argument("--limit", type=int, default=200)
    m.add_argument("--all", action="store_true", help="include non-anomalous turns")

    r = sub.add_parser("run"); r.set_defaults(func=_cmd_run)
    r.add_argument("--dataset", required=True)
    r.add_argument("--k", type=int, default=1)
    r.add_argument("--base-url", dest="base_url", default="http://localhost:5050")
    r.add_argument("--user", default=None); r.add_argument("--password", default=None)
    r.add_argument("--run-id", dest="run_id", default=None)
    r.add_argument("--baseline", default=None)
    r.add_argument("--judges", action="store_true", help="include LLM-judge scorers")
    r.add_argument("--judge-models", dest="judge_models", type=int, default=3,
                   help="panel size (distinct provider families)")

    g = sub.add_parser("gate"); g.set_defaults(func=_cmd_gate)
    g.add_argument("--run", required=True)
    g.add_argument("--baseline", default=None)
    g.add_argument("--min-pass-k", dest="min_pass_k", type=float, default=None)
    g.add_argument("--cohort", action="append", help="cohort=floor (repeatable)")

    rp = sub.add_parser("report"); rp.set_defaults(func=_cmd_report)
    rp.add_argument("--run", required=True)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
