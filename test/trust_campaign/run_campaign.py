#!/usr/bin/env python3
"""Trust Circle campaign runner — live turns on EVERY engine, measured against every
reporting surface.

    python3 test/trust_campaign/run_campaign.py                       # all five engines
    python3 test/trust_campaign/run_campaign.py --engines focus,optimize
    python3 test/trust_campaign/run_campaign.py --only G,S --case S1
    python3 test/trust_campaign/run_campaign.py --dry-run
    python3 test/trust_campaign/run_campaign.py --baseline write      # accept this run
    python3 test/trust_campaign/run_campaign.py --baseline write --from <dir1>,<dir2>
                                          # accept saved runs offline (later dirs override)
    python3 test/trust_campaign/run_campaign.py --agent UDERIA        # one operator agent

THE AGENTS are the campaign's own (`agents.py ensure` — one per engine class, one posture,
one weak model), looked up by their tags. `--agent TAG` measures one operator agent as
configured instead, as campaign 1 did.

ONE NEW SESSION PER CASE — every turn is turn 1 of its own session, so no case can
inherit context, cache or ordering from another. Engines run IN PARALLEL (one worker
each); cases within an engine run in order.

JUDGES are switched ON at rate 1.0 for the run and RESTORED in a finally block, so an
interrupted campaign cannot leave the deployment scoring every turn with judges.

THE BASELINE (`baseline.json`, committed) is the last ACCEPTED run's station readings per
(engine, case). Every run diffs against it and reports DRIFT — a station that reads
differently from the last accepted run. Drift is a finding to triage, never a failure by
itself: the model is probabilistic, and a changed reading may be the engine improving.
`--baseline write` records this run as the new baseline once its findings are triaged.

THE SWITCHES the campaign needs on the server: TDA_TURN_BUDGET=1 and TDA_OUTCOME_LOOP=1
(env, not app-config). The run detects a missing switch from the readings (a declared
budget that reads `no budget declared`) and says so in the report instead of counting it
as a miss.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agents import ENGINE_LABEL, ENGINES, TAGS, login, req  # noqa: E402
import agents as _agents  # noqa: E402
sys.path.insert(0, os.path.join(HERE, ".."))
from campaign_budget import Budget, DEFAULT_MAX_USD  # noqa: E402

#: the run's cost cap (set in main); None until then = no cap (e.g. an offline --from accept)
BUDGET = None
from suite import (CASES, EXPECTED_LABELS, MEASUREMENT_STATION, STATIONS, answer_text,  # noqa: E402
                   cases_for, check_expectations, check_invariants)

# Staleness is a PROVOCATION's signal: a case with a `landed` detector that reached none of its
# targets has gone soft. A recall/coverage case (no detector) targets a gate in order to see it
# PASS — it is "quiet" by design and was listed as a rewrite candidate on every run, burying the
# real ones (found 2026-10-29: 66 candidates, most of them recall cases).
PROVOCATIONS = frozenset(c["id"] for c in CASES if c.get("landed"))

POLL_SECONDS, POLL_MAX = 3, 160     # judges add latency; 8 min ceiling per case
BASELINE_PATH = os.path.join(HERE, "baseline.json")
SHORT = {"ideate": "llm_only", "ideate+tools": "conversation_with_tools", "focus": "rag_focused",
         "optimize": "tool_enabled", "coordinate": "genie"}
RANK = {"na": 0, "pass": 1, "pending": 2, "unknown": 3, "warn": 4, "danger": 5}


# ── campaign config: judges on for the run, restored afterwards ──────────────
def set_judges(token, on: bool, rate: float):
    return req("POST", "/api/v1/admin/app-config", token,
               {"agent_eval": {"include_judges": on, "judge_sample_rate": rate}})


def read_agent_eval(token):
    c = req("GET", "/api/v1/admin/app-config", token).get("config") or {}
    return c.get("agent_eval") or {}


# ── the registry as the server serves it ─────────────────────────────────────
def vocabulary(token) -> dict:
    """{point_key: frozenset(classes) or frozenset() for all} from /v1/assurance/vocabulary —
    the live server's own declaration, which is what the dial reads."""
    voc = req("GET", "/api/v1/assurance/vocabulary", token)
    out = {}
    for e in (voc.get("entries") or []):
        if e.get("cls") in ("station", "stage", "measure", "property") or e.get("excluded"):
            continue
        k = e.get("k") or e.get("key")
        if k and "applies_to" in e:
            out[k] = frozenset(e.get("applies_to") or [])
    return out


def applicable_points(voc: dict, engine: str) -> set:
    return {k for k, cls in voc.items() if not cls or engine in cls}


def verify_labels_match_server(token):
    """The suite's own LABEL copy vs the server's vocabulary — a campaign that imported
    the source of truth could not detect the source of truth being wrong."""
    voc = req("GET", "/api/v1/assurance/vocabulary", token)
    server = {}
    for e in (voc.get("entries") or []):
        st, dims = e.get("station"), e.get("dims") or []
        if not st or e.get("cls") in ("station", "stage"):
            continue
        server.setdefault(st, set()).update(dims)
    rows = []
    for st in set(server) | set(EXPECTED_LABELS):
        exp, got = EXPECTED_LABELS.get(st, set()), server.get(st, set())
        rows.append({"station": st, "ok": exp == got,
                     "only_in_suite": sorted(exp - got), "only_on_server": sorted(got - exp)})
    return rows


def station_states(turn: dict, with_dims: bool = True) -> dict:
    """What the Assurance dial ACTUALLY shows per station.

    Mirrors `assuranceDial.stations()`: a station is worst-of its mechanisms **and the
    dimensions those mechanisms declare** — a station that names its measures cannot then
    ignore them. JUDGED, which runs no mechanism, is worst-of its measurements."""
    a = (turn or {}).get("assurance") or {}
    out = {}
    for st in STATIONS:
        worst = "na"
        for p in ((a.get("by_station") or {}).get(st) or []):
            if RANK.get(p.get("state"), 0) > RANK.get(worst, 0):
                worst = p.get("state")
            if not with_dims:
                continue
            for d in (p.get("dims") or []):
                b = d.get("b") if isinstance(d, dict) else None
                as_state = "pass" if b == "healthy" else b
                if as_state and RANK.get(as_state, 0) > RANK.get(worst, 0):
                    worst = as_state
        if st == MEASUREMENT_STATION and with_dims:
            for d in ((a.get("measures") or {}).get(st) or []):
                b = d.get("b") if isinstance(d, dict) else None
                as_state = "pass" if b == "healthy" else b
                if as_state and RANK.get(as_state, 0) > RANK.get(worst, 0):
                    worst = as_state
        out[st] = worst
    return out


# ── one case = one session = one turn ────────────────────────────────────────
def run_case(token, engine, profile_id, tag, case, applicable):
    t0 = time.time()
    rec = {"engine": engine, "tag": tag, "id": case["id"], "family": case["family"],
           "prompt": case["prompt"], "note": case.get("note", ""), "station": case.get("station"),
           "targets": list(case.get("targets") or ())}

    s = req("POST", "/api/v1/sessions", token, {"name": f"trust-campaign {tag} {case['id']}"})
    sid = s.get("session_id")
    if not sid:
        rec["error"] = f"session creation failed: {json.dumps(s)[:200]}"
        return rec
    rec["session_id"] = sid

    q = req("POST", f"/api/v1/sessions/{sid}/query", token,
            {"prompt": case["prompt"], "profile_id": profile_id})
    tid = q.get("task_id")
    if not tid:
        rec["error"] = f"query rejected: {json.dumps(q)[:300]}"
        return rec

    status = "?"
    for _ in range(POLL_MAX):
        t = req("GET", f"/api/v1/tasks/{tid}", token)
        status = t.get("status", "?")
        if status in ("complete", "failed", "error"):
            break
        time.sleep(POLL_SECONDS)
    rec["status"] = status
    rec["wall_s"] = round(time.time() - t0, 1)

    # Scoring is a fire-and-forget task that lands AFTER the answer — give it a moment,
    # then re-read. Reading too early records "pending" and would misreport the engine.
    time.sleep(6)
    turn = req("GET", f"/api/session/{sid}/turn/1/details", token)
    for _ in range(10):
        if isinstance((turn.get("eval") or {}), dict) and (turn.get("eval") or {}).get("dimensions"):
            break
        time.sleep(4)
        turn = req("GET", f"/api/session/{sid}/turn/1/details", token)

    if turn.get("__http_error__") or turn.get("__error__"):
        rec["error"] = f"turn fetch failed: {json.dumps(turn)[:200]}"
        return rec

    rec["answer"] = answer_text(turn)[:600]
    rec["model"] = turn.get("model")
    rec["duration_ms"] = turn.get("duration_ms")
    rec["class_read"] = (turn.get("assurance") or {}).get("profile_class")
    rec["turn"] = turn
    rec["invariants"] = check_invariants(turn, applicable, case)
    rec["expectations"] = check_expectations(case, turn, engine)

    a = turn.get("assurance") or {}
    rec["stations"] = station_states(turn)
    rec["stations_mech_only"] = station_states(turn, with_dims=False)
    rec["points"] = {p.get("key"): p.get("state") for p in (a.get("points") or [])}
    rec["acted"] = [p.get("key") for p in (a.get("points") or []) if p.get("acted")]
    ev = turn.get("eval") or {}
    rec["checks"] = ev.get("checks")
    rec["failed_dimensions"] = ev.get("failed_dimensions") or []
    rec["judged_dims"] = sorted({d.get("k") for d in (ev.get("dimensions") or [])
                                 if isinstance(d, dict) and d.get("k")})
    rec["judge_dims_scored"] = sorted(
        d.get("k") for d in (ev.get("dimensions") or [])
        if isinstance(d, dict) and d.get("k") in
        {"groundedness", "scope_faithfulness", "answer_groundedness", "content_safety",
         "answer_correctness", "instruction_following", "synthesis_quality",
         "tool_arg_optimality", "source_consistency"})
    # the switches: a declared budget that reads `no budget` means the server switch is off
    tb = rec["points"].get("turn_budget")
    rec["budget_switch_off"] = (tb == "na" and "turn_budget" in rec["targets"]
                                and "no budget declared" in
                                next((p.get("detail", "") for p in (a.get("points") or [])
                                      if p.get("key") == "turn_budget"), ""))
    return rec


def run_engine(token, engine, profile_id, tag, cases, applicable, results, lock, outdir):
    total = len(cases)
    for i, c in enumerate(cases, 1):
        if BUDGET is not None and BUDGET.exceeded():
            print(f"  {ENGINE_LABEL[engine]:13s} [{i}/{total}] {c['id']:3s} SKIPPED — cost cap reached "
                  f"(${BUDGET.max_usd:.2f}); {total - i + 1} case(s) not run on this engine")
            return
        rec = run_case(token, engine, profile_id, tag, c, applicable)
        with lock:
            results.append(rec)
            json.dump(results, open(os.path.join(outdir, "raw.json"), "w"), indent=1, default=str)
        if rec.get("error"):
            print(f"  {ENGINE_LABEL[engine]:13s} [{i}/{total}] {c['id']:3s} ERROR  {rec['error'][:90]}")
            continue
        ok_i = sum(1 for x in rec["invariants"] if x["ok"])
        ok_e = sum(1 for x in rec["expectations"] if x["ok"])
        print(f"  {ENGINE_LABEL[engine]:13s} [{i}/{total}] {c['id']:3s} {rec['status']:9s} "
              f"{rec['wall_s']:6.1f}s  inv {ok_i}/{len(rec['invariants'])}  "
              f"exp {ok_e}/{len(rec['expectations'])}  checks={rec['checks']} "
              f"failed={len(rec['failed_dimensions'])} judges={len(rec['judge_dims_scored'])}")


# ── the ring's own aggregate reading, after the campaign ────────────────────
def ring_snapshot(token, tags):
    dash = req("GET", "/api/v1/evals/agents/kpi-dashboard?scope=my&limit=2000", token)
    tb = req("GET", "/api/v1/evals/agents/trust-behavior?scope=my", token)
    tr = req("GET", "/api/v1/trust/agents", token)
    out = {}

    def _cells(d):
        """kpi-dashboard serves its per-agent rows as a LIST (campaign 2 crashed on `.items()`
        at the very end of a run); older shapes were a dict keyed by tag — read both."""
        raw = (d.get("by_agent") or d.get("agents") or d.get("scoreboard") or {}) if isinstance(d, dict) else {}
        if isinstance(raw, dict):
            return list(raw.items())
        return [(c.get("tag") or c.get("agent") or c.get("key"), c) for c in raw if isinstance(c, dict)]

    for tag in tags:
        cell = None
        for k, c in _cells(dash):
            if k == tag or (isinstance(c, dict) and c.get("tag") == tag):
                cell = c
                break
        beh = None
        for k, c in _cells(tb):
            if k == tag or (isinstance(c, dict) and c.get("tag") == tag):
                beh = c
                break
        integ = next((a for a in (tr.get("agents") or []) if a.get("tag") == tag), None)
        out[tag] = {"cell": cell, "behavior": beh, "integrity": integ}
    out["_dashboard_error"] = dash.get("__http_error__") or dash.get("__error__")
    return out


# ── baseline ─────────────────────────────────────────────────────────────────
def load_baseline():
    try:
        return json.load(open(BASELINE_PATH))
    except Exception:  # noqa: BLE001
        return {}


def baseline_key(r):
    return f"{r['engine']}/{r['id']}"


def diff_baseline(results, base):
    """DRIFT: a station reading that differs from the last accepted run."""
    drift = []
    for r in results:
        if not r.get("turn"):
            continue
        b = (base.get("cases") or {}).get(baseline_key(r))
        if not b:
            drift.append({"key": baseline_key(r), "kind": "new", "detail": "no baseline for this case"})
            continue
        for st in STATIONS:
            was, now = (b.get("stations") or {}).get(st), r["stations"].get(st)
            if was != now:
                drift.append({"key": baseline_key(r), "kind": "station", "station": st,
                              "was": was, "now": now})
        was_a, now_a = sorted(b.get("acted") or []), sorted(r.get("acted") or [])
        if was_a != now_a:
            drift.append({"key": baseline_key(r), "kind": "acted", "was": was_a, "now": now_a})
    return drift


STALE_AFTER = 3     # accepted runs in which a provocation reached none of its targets
FORCE_ACCEPT = False


def provoked(r) -> bool:
    """Did this case reach what it targets — a targeted point warn/danger/acted, or the
    bait landed (I10 either way)? A provocation that reaches nothing run after run has gone
    soft: the model stopped taking the bait or the prompt is too polite."""
    tg = set(r.get("targets") or ())
    pts = ((r.get("turn") or {}).get("assurance") or {}).get("points") or []
    if any((p.get("state") in ("warn", "danger") or p.get("acted")) and (not tg or p.get("key") in tg)
           for p in pts):
        return True
    i10 = next((x for x in (r.get("invariants") or []) if x["id"] == "I10"), None)
    return bool(i10 and "landed" in i10["detail"] and "not land" not in i10["detail"])


def write_baseline(results, model):
    """Record this run as the accepted baseline. Carries a `quiet_runs` counter per
    provocation case across accepted runs — the staleness signal (`STALE_AFTER`).

    REFUSES while `harvest_ledger.json` holds a candidate nobody has dispositioned: a run is
    accepted only with new cases or a recorded reason for none. (`--force-accept` overrides,
    and says so in the baseline.)"""
    from harvest import undispositioned
    pending = undispositioned()
    if pending and not FORCE_ACCEPT:
        sys.exit(f"REFUSED: {len(pending)} harvested candidate(s) await a disposition in "
                 f"test/trust_campaign/harvest_ledger.json ({', '.join(pending[:6])}"
                 f"{'…' if len(pending) > 6 else ''}) — adopt each (write the case, set "
                 f"status=adopted, case=<id>) or decline it (status=declined, reason=…), "
                 f"then accept. --force-accept records an override.")
    prev = (load_baseline().get("cases") or {})
    cases, stale = {}, []
    for r in results:
        if not r.get("turn"):
            continue
        k = baseline_key(r)
        quiet = 0
        if r.get("targets") and r.get("id") in PROVOCATIONS:
            quiet = 0 if provoked(r) else int((prev.get(k) or {}).get("quiet_runs") or 0) + 1
        cases[k] = {"stations": r["stations"], "acted": sorted(r.get("acted") or []),
                    "points": r.get("points"), "class_read": r.get("class_read"),
                    "quiet_runs": quiet}
        if quiet >= STALE_AFTER:
            stale.append((k, quiet))
    json.dump({"written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "model": model, "cases": cases, "case_count": len(cases),
               "forced_over_pending_harvest": sorted(pending) if pending else []},
              open(BASELINE_PATH, "w"), indent=1, sort_keys=True)
    if stale:
        print("REWRITE CANDIDATES — provocations that reached none of their targets in "
              f"{STALE_AFTER}+ accepted runs:")
        for k, q in stale:
            print(f"  {k}  quiet for {q} runs")
    return BASELINE_PATH


# ── report ───────────────────────────────────────────────────────────────────
def write_report(outdir, results, ring, part, engines, judges_cfg, drift, model):
    lines = []
    A = lines.append
    A("# Trust Circle campaign — every engine\n")
    A(f"_{datetime.now(timezone.utc).isoformat(timespec='seconds')} · model {model} · judges "
      f"{'ON' if judges_cfg.get('include_judges') else 'OFF'} @ rate "
      f"{judges_cfg.get('judge_sample_rate')} · one new session per case · engines in parallel_\n")

    done = [r for r in results if r.get("turn")]
    inv_all = [(r, x) for r in done for x in r["invariants"]]
    exp_all = [(r, x) for r in done for x in r["expectations"]]
    inv_fail = [(r, x) for r, x in inv_all if not x["ok"]]
    exp_fail = [(r, x) for r, x in exp_all if not x["ok"]]
    errs = [r for r in results if r.get("error")]
    switch_off = [r for r in done if r.get("budget_switch_off")]

    A("## Headline\n")
    A("| | |\n|---|---|")
    A(f"| Engines | {', '.join(ENGINE_LABEL[e] for e in engines)} |")
    A(f"| Cases run | {len(done)} / {len(results)} |")
    A(f"| Invariant checks | **{len(inv_all) - len(inv_fail)} / {len(inv_all)} passed** |")
    A(f"| Case expectations | **{len(exp_all) - len(exp_fail)} / {len(exp_all)} passed** |")
    A(f"| Cases erroring | {len(errs)} |")
    A(f"| Drift vs baseline | {len(drift)} |")
    if switch_off:
        A(f"| Turn-budget switch | **OFF on the server** — {len(switch_off)} budget case(s) read `no budget`; "
          f"start with `TDA_TURN_BUDGET=1` |")
    A("")

    A("## Per engine\n")
    A("| Engine | Agent | Class read | Cases | Inv | Exp | Errors |\n|---|---|---|---|---|---|---|")
    for e in engines:
        rs = [r for r in results if r["engine"] == e]
        d = [r for r in rs if r.get("turn")]
        ni = sum(1 for r in d for x in r["invariants"] if x["ok"])
        ti = sum(len(r["invariants"]) for r in d)
        ne = sum(1 for r in d for x in r["expectations"] if x["ok"])
        te = sum(len(r["expectations"]) for r in d)
        classes = sorted({r.get("class_read") for r in d if r.get("class_read")})
        A(f"| {ENGINE_LABEL[e]} | @{rs[0]['tag'] if rs else '—'} | {', '.join(classes) or '—'} | "
          f"{len(d)}/{len(rs)} | {ni}/{ti} | {ne}/{te} | {len(rs) - len(d)} |")
    A("")

    A("## Station matrix — what the dial shows (worst-of, dimensions folded in)\n")
    A("| Engine | Case | " + " | ".join(s[:2].upper() for s in STATIONS) + " | Acted | Failed dims |")
    A("|---|---|" + "---|" * len(STATIONS) + "---|---|")
    AB = {"pass": "ok", "warn": "WARN", "danger": "DANG", "na": "—", "unknown": "?", "pending": "…"}
    for e in engines:
        for r in [r for r in results if r["engine"] == e]:
            if r.get("error"):
                A(f"| {ENGINE_LABEL[e]} | {r['id']} | " + " | ".join(["ERR"] * len(STATIONS)) + " | — | — |")
                continue
            A(f"| {ENGINE_LABEL[e]} | {r['id']} | " + " | ".join(AB.get(r['stations'][s], r['stations'][s]) for s in STATIONS)
              + f" | {', '.join(r['acted']) or '—'} | {', '.join(r['failed_dimensions']) or '—'} |")
    A("")

    A("## Partition — suite copy vs the server's vocabulary\n")
    A("| Station | Agrees | Only in suite | Only on server |\n|---|---|---|---|")
    for r in sorted(part, key=lambda x: x["station"]):
        A(f"| {r['station']} | {'yes' if r['ok'] else '**NO**'} | "
          f"{', '.join(r['only_in_suite']) or '—'} | {', '.join(r['only_on_server']) or '—'} |")
    A("")

    if inv_fail:
        A("## Invariant failures (defects)\n")
        for r, x in inv_fail:
            A(f"- **{ENGINE_LABEL[r['engine']]} · {r['id']} · {x['id']}** — {x['detail']}")
        A("")
    if exp_fail:
        A("## Expectation misses (engine behaviour vs the case's claim)\n")
        for r, x in exp_fail:
            A(f"- **{ENGINE_LABEL[r['engine']]} · {r['id']} · {x['id']}** — {x['detail']}")
        A("")
    if errs:
        A("## Cases that errored\n")
        for r in errs:
            A(f"- **{ENGINE_LABEL[r['engine']]} · {r['id']}** — {r['error']}")
        A("")
    if drift:
        A("## Drift vs the last accepted baseline\n")
        for d in drift:
            if d["kind"] == "station":
                A(f"- `{d['key']}` {d['station']}: {d['was']} → {d['now']}")
            elif d["kind"] == "acted":
                A(f"- `{d['key']}` acted: {d['was']} → {d['now']}")
            else:
                A(f"- `{d['key']}` {d['detail']}")
        A("")

    A("## Judge coverage\n")
    A("| Engine | Case | Judge dimensions scored |\n|---|---|---|")
    for r in done:
        A(f"| {ENGINE_LABEL[r['engine']]} | {r['id']} | {', '.join(r['judge_dims_scored']) or '—'} |")
    A("")

    A("## The ring, after the campaign\n")
    A("```json")
    A(json.dumps(ring, indent=1, default=str)[:4000])
    A("```")

    A("\n## Case notes\n")
    for e in engines:
        for r in [r for r in results if r["engine"] == e]:
            A(f"### {ENGINE_LABEL[e]} · {r['id']} — {r.get('note','')}\n")
            A(f"**Prompt:** {r['prompt']}\n")
            if r.get("error"):
                A(f"**ERROR:** {r['error']}\n")
                continue
            A(f"**Answer (600c):** {(r.get('answer') or '').strip()[:600]}\n")
            bad = [x for x in r["invariants"] + r["expectations"] if not x["ok"]]
            if bad:
                A("**Not satisfied:**\n")
                for x in bad:
                    A(f"- {x['id']} — {x['detail']}")
                A("")

    p = os.path.join(outdir, "report.md")
    open(p, "w").write("\n".join(lines))
    return p, len(inv_fail), len(exp_fail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="all",
                    help="comma list of ideate,ideate+tools,focus,optimize,coordinate (or all)")
    ap.add_argument("--agent", default="", help="measure ONE operator agent by tag instead")
    ap.add_argument("--only", default="", help="comma-separated families, e.g. G,S")
    ap.add_argument("--case", default="", help="comma-separated case ids")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-judges", action="store_true", help="leave judge config untouched")
    ap.add_argument("--baseline", default="diff", choices=("diff", "write", "none"))
    ap.add_argument("--serial", action="store_true", help="one engine at a time")
    ap.add_argument("--force-accept", action="store_true",
                    help="accept despite undispositioned harvest candidates (recorded in the baseline)")
    ap.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                    help=f"hard cost cap for the run in USD, read from the ledger (default "
                         f"{DEFAULT_MAX_USD}; a full campaign is ~$2 on Nova 2 Lite — raise it for one; 0 = no cap)")
    ap.add_argument("--from", dest="from_dirs", default="",
                    help="with --baseline write: accept these saved runs (comma list, later override)")
    args = ap.parse_args()
    global FORCE_ACCEPT
    FORCE_ACCEPT = bool(args.force_accept)

    if args.from_dirs:
        # OFFLINE accept: merge saved runs (re-checked against the current suite) and write
        # the baseline — no server, no tokens. A partial re-run on top of a full run is the
        # common shape: the later directory's cases override the earlier one's.
        merged = {}
        model = None
        for d in [x.strip() for x in args.from_dirs.split(",") if x.strip()]:
            rs = json.load(open(os.path.join(d, "raw.json")))
            for r in rs:
                if r.get("turn"):
                    merged[baseline_key(r)] = r
                    model = model or (r.get("turn") or {}).get("model")
        results = list(merged.values())
        print(f"{len(results)} cases from {args.from_dirs}")
        if args.baseline != "write":
            sys.exit("--from needs --baseline write")
        print(f"baseline written: {write_baseline(results, model)}")
        return 0

    token = login()
    profs = req("GET", "/api/v1/profiles", token)
    by_tag = {p.get("tag"): p for p in profs.get("profiles", [])}
    active = set(profs.get("active_for_consumption_profile_ids") or [])

    # which (engine, agent) pairs run
    plan = []
    if args.agent:
        p = by_tag.get(args.agent)
        if not p:
            sys.exit(f"agent @{args.agent} not found")
        eng = p.get("profile_type") or "tool_enabled"
        if eng == "llm_only" and p.get("useMcpTools"):
            eng = "conversation_with_tools"
        plan.append((eng, p, args.agent))
    else:
        wanted = ENGINES if args.engines == "all" else [SHORT.get(x.strip().lower(), x.strip())
                                                        for x in args.engines.split(",")]
        for eng in wanted:
            if eng not in ENGINES:
                sys.exit(f"unknown engine {eng!r}")
            p = by_tag.get(TAGS[eng])
            if not p:
                sys.exit(f"campaign agent @{TAGS[eng]} is absent — run agents.py ensure")
            plan.append((eng, p, TAGS[eng]))
    for eng, p, tag in plan:
        if p["id"] not in active:
            sys.exit(f"@{tag} is not ACTIVATED — the REST firewall will refuse every case")

    def select(eng):
        cases = cases_for(eng)
        if args.only:
            fams = {f.strip().upper() for f in args.only.split(",")}
            cases = [c for c in cases if c["family"] in fams]
        if args.case:
            ids = {c.strip().upper() for c in args.case.split(",")}
            cases = [c for c in cases if c["id"] in ids]
        return cases

    total = sum(len(select(e)) for e, _, _ in plan)
    if not total:
        sys.exit("no cases selected")
    model = (by_tag.get(plan[0][2]) or {}).get("llmConfigurationId")
    print(f"{len(plan)} engine(s) · {total} cases · " +
          " · ".join(f"@{t}={ENGINE_LABEL[e]}" for e, _, t in plan))

    if args.dry_run:
        for eng, _, tag in plan:
            for c in select(eng):
                print(f"  {ENGINE_LABEL[eng]:13s} {c['id']:3s} [{c['family']}] → {', '.join(c['targets']) or '—':40s} {c['prompt'][:70]}")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join("tda_eval", "trust_campaign", stamp)
    os.makedirs(outdir, exist_ok=True)

    before = read_agent_eval(token)
    print(f"agent_eval before: judges={before.get('include_judges')} rate={before.get('judge_sample_rate')}")
    results, lock = [], threading.Lock()
    global BUDGET
    BUDGET = Budget(args.max_usd, _agents.USER)
    print(f"cost cap: ${args.max_usd:.2f}" if args.max_usd else "cost cap: none")
    try:
        if not args.no_judges:
            set_judges(token, True, 1.0)
            now = read_agent_eval(token)
            print(f"agent_eval for the run: judges={now.get('include_judges')} rate={now.get('judge_sample_rate')}")
        part = verify_labels_match_server(token)
        voc = vocabulary(token)
        threads = []
        for eng, p, tag in plan:
            t = threading.Thread(target=run_engine,
                                 args=(token, eng, p["id"], tag, select(eng),
                                       applicable_points(voc, eng), results, lock, outdir))
            if args.serial:
                t.start()
                t.join()
            else:
                t.start()
                threads.append(t)
        for t in threads:
            t.join()
    finally:
        if not args.no_judges:
            set_judges(token, bool(before.get("include_judges")),
                       float(before.get("judge_sample_rate") or 0.05))
            after = read_agent_eval(token)
            print(f"agent_eval restored: judges={after.get('include_judges')} rate={after.get('judge_sample_rate')}")

    order = {(e, c["id"]): i for e, _, _ in plan for i, c in enumerate(select(e))}
    results.sort(key=lambda r: (ENGINES.index(r["engine"]), order.get((r["engine"], r["id"]), 999)))
    ring = ring_snapshot(token, [t for _, _, t in plan])
    drift = diff_baseline(results, load_baseline()) if args.baseline != "none" else []
    judges_cfg = {"include_judges": True, "judge_sample_rate": 1.0} if not args.no_judges else before
    path, nf_i, nf_e = write_report(outdir, results, ring, part, [e for e, _, _ in plan],
                                    judges_cfg, drift, model)
    print(f"\nreport: {path}\ninvariant failures: {nf_i} · expectation misses: {nf_e} · drift: {len(drift)}")
    print(BUDGET.line())
    with open(path, "a") as fh:
        fh.write(f"\n## Cost\n\n{BUDGET.line()}\n")
    if args.baseline == "write" and BUDGET.hit:
        sys.exit("REFUSED: the cost cap ended this run early — a partial run is never the accepted "
                 "baseline (its missing cases would distort the staleness counters). Re-run with a "
                 "higher --max-usd, or accept saved runs with --from.")
    if args.baseline == "write":
        print(f"baseline written: {write_baseline(results, model)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
