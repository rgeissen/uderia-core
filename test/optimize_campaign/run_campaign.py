#!/usr/bin/env python3
"""Optimize campaign runner — live turns on the lane's levers, scored against the oracle,
priced from the persisted turn, read for the machinery that fired.

    python3 test/optimize_campaign/run_campaign.py                         # OCFL (the working model)
    python3 test/optimize_campaign/run_campaign.py --levers OCFL,OCWEAK    # the stress test (the gap)
    python3 test/optimize_campaign/run_campaign.py --levers OCFL,OCFL-NORAG --only E
    python3 test/optimize_campaign/run_campaign.py --case R6
    python3 test/optimize_campaign/run_campaign.py --baseline write        # accept (the rule below)
    python3 test/optimize_campaign/run_campaign.py --baseline write --from <dir>[,<dir>]

ONE NEW SESSION PER CASE (a multi-turn case is one session, its turns in order). Levers run
in parallel, cases in order. Judges are left as configured — this campaign reads the
deterministic dimensions and the oracle; it does not need the panel.

THE ORACLE is run before each case through the campaign's MCP entry; the answer's figure
is compared to the data's. THE FRONTIER per lever: correctness rate, cost per correct
answer, p50 latency, LLM calls per case. THE GAP: OCFL − OCWEAK correctness per case.

THE ACCEPT RULE (`--baseline write`): correctness per lever never drops against the last
accepted baseline, and the harvest ledger holds no undispositioned candidate. A drop or a
pending candidate refuses (`--force-accept` records an override).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util as _ilu
sys.path.insert(0, os.path.join(HERE, ".."))
from campaign_budget import Budget, DEFAULT_MAX_USD  # noqa: E402

#: the run's cost cap (set in main); None = no cap (an offline --from accept)
BUDGET = None


def _load(path, name):
    spec = _ilu.spec_from_file_location(name, path)
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# the trust campaign's transport, loaded by PATH — both campaigns have a suite.py and an
# agents.py, so a sys.path entry would shadow one with the other
_tc = _load(os.path.join(HERE, "..", "trust_campaign", "agents.py"), "tc_agents")
login, req, USER = _tc.login, _tc.req, _tc.USER
from registry import REGISTRY, fired, llm_calls, tool_calls  # noqa: E402
from suite import (ALL_LEVERS, CASES, LEVERS, WASTE, answer_text, cases_for, check_expectations,  # noqa: E402
                   check_invariants, self_corrections)
import oracle  # noqa: E402

POLL_SECONDS, POLL_MAX = 3, 160
QUOTA = "QUOTA: the platform's per-user consumption limit refused the turn"
_QUOTA_HIT = threading.Event()
BASELINE_PATH = os.path.join(HERE, "baseline.json")
LEDGER_PATH = os.path.join(HERE, "harvest_ledger.json")
FORCE_ACCEPT = False


# ── the oracle, once per case ────────────────────────────────────────────────
def expected_for(case_or_turn: dict, cache: dict):
    sql = case_or_turn.get("oracle_sql")
    if not sql:
        return None
    if sql not in cache:
        try:
            cache[sql] = oracle.expected_value(oracle.run_sql(sql), case_or_turn.get("extract", "number"))
        except Exception as e:  # noqa: BLE001
            cache[sql] = {"__oracle_error__": str(e)[:200]}
    return cache[sql]


# ── one turn ─────────────────────────────────────────────────────────────────
def run_turn(token, sid, pid, prompt, turn_no):
    t0 = time.time()
    q = req("POST", f"/api/v1/sessions/{sid}/query", token, {"prompt": prompt, "profile_id": pid})
    tid = q.get("task_id")
    if not tid:
        return None, f"query rejected: {json.dumps(q)[:200]}", 0
    status = "?"
    for _ in range(POLL_MAX):
        t = req("GET", f"/api/v1/tasks/{tid}", token)
        status = t.get("status", "?")
        if status in ("complete", "failed", "error"):
            break
        time.sleep(POLL_SECONDS)
    if status in ("failed", "error") and "rate limit" in json.dumps(t).lower():
        # the platform's per-user consumption quota, not the lane: the run stops cleanly
        # instead of recording every remaining case as an error
        return None, QUOTA, 0
    time.sleep(5)
    turn = req("GET", f"/api/session/{sid}/turn/{turn_no}/details", token)
    for _ in range(8):
        if (turn.get("eval") or {}).get("dimensions"):
            break
        time.sleep(4)
        turn = req("GET", f"/api/session/{sid}/turn/{turn_no}/details", token)
    if turn.get("__http_error__") or turn.get("__error__"):
        return None, f"turn fetch failed: {json.dumps(turn)[:200]}", 0
    return turn, status, round(time.time() - t0, 1)


def scorable_answer(turn, ans: str) -> str:
    """What the user was shown: the answer's text AND the rows of a chart the turn drew. A chart is
    a legitimate answer — found on the first cross-lane run (2026-10-29, ReAct · A2): the right
    five products, charted in order, the text naming only #1 and #5 and pointing at "the chart
    above"; read as text alone it scored wrong. Lane-neutral: Optimize charts too. Only a
    SUCCESSFUL chart call counts, and its rows are appended in their own order."""
    rows_text = []
    for name, args, ok in tool_calls(turn):
        if name != "TDA_Charting" or not ok or not isinstance(args, dict):
            continue
        data = args.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:  # noqa: BLE001
                data = None
        for row in data if isinstance(data, list) else []:
            if isinstance(row, dict):
                rows_text.append(" · ".join(str(v) for v in row.values()))
    return ans if not rows_text else ans + "\n\n[chart rows]\n" + "\n".join(rows_text)


def readings(turn, case_like, expected, band):
    """Everything the campaign reads from one persisted turn."""
    ans = answer_text(turn)
    if isinstance(expected, dict) and "__oracle_error__" in expected:
        correct, cdetail = None, f"oracle error: {expected['__oracle_error__']}"
    elif case_like.get("oracle_sql"):
        correct, cdetail = oracle.check(scorable_answer(turn, ans), expected, case_like.get("extract", "number"),
                                        case_like.get("tolerance", 0.0), case_like.get("prompt", ""))
    else:
        correct, cdetail = None, "no oracle"
    calls = llm_calls(turn)
    tools = tool_calls(turn)
    r = {
        "answer": ans[:500], "correct": correct, "correct_detail": cdetail,
        "cost_usd": float(turn.get("turn_cost") or 0), "input_tokens": int(turn.get("turn_input_tokens") or 0),
        "output_tokens": int(turn.get("turn_output_tokens") or 0), "latency_ms": turn.get("duration_ms"),
        "llm_calls": len(calls), "llm_call_kinds": [m for m, *_ in calls],
        "tool_calls": len(tools), "tool_errors": sum(1 for *_, ok in tools if not ok),
        "self_corrections": self_corrections(turn), "fired": sorted(fired(turn)),
        "rag_case_count": turn.get("rag_case_count"), "model": turn.get("model"),
        "phases": len(turn.get("original_plan") or []),
    }
    w = case_like.get("waste")
    if w and w in WASTE:
        try:
            r["waste"] = WASTE[w](turn)
        except Exception as e:  # noqa: BLE001
            r["waste"] = f"error:{e}"
    r["invariants"] = check_invariants(turn, case_like, correct, band)
    r["expectations"] = check_expectations(case_like, turn, correct, turn.get("rag_case_count"))
    return r


def run_primer(token, sid, pid, primer):
    """The agent's session primer as turn 1, exactly as the UI sends it. Its price is the
    lane's price too, recorded apart so a case's own cost stays readable."""
    stmts = [x for x in (primer or {}).get("statements") or [] if str(x).strip()]
    if not (primer or {}).get("enabled") or not stmts:
        return None
    text = " ".join(str(x).strip() for x in stmts)
    q = req("POST", f"/api/v1/sessions/{sid}/query", token,
            {"prompt": text, "profile_id": pid, "is_session_primer": True})
    tid = q.get("task_id")
    if not tid:
        return {"error": f"primer rejected: {json.dumps(q)[:160]}"}
    for _ in range(POLL_MAX):
        t = req("GET", f"/api/v1/tasks/{tid}", token)
        if t.get("status") in ("complete", "failed", "error"):
            break
        time.sleep(POLL_SECONDS)
    time.sleep(2)
    turn = req("GET", f"/api/session/{sid}/turn/1/details", token)
    return {"cost_usd": float(turn.get("turn_cost") or 0), "input_tokens": int(turn.get("turn_input_tokens") or 0),
            "status": t.get("status")}


def run_case(token, lever, pid, case, ocache, bands, primer=None):
    rec = {"lever": lever, "id": case["id"], "family": case["family"], "prompt": case["prompt"],
           "note": case.get("note", ""), "targets": list(case["targets"])}
    s = req("POST", "/api/v1/sessions", token, {"name": f"optimize-campaign {lever} {case['id']}"})
    sid = s.get("session_id")
    if not sid:
        rec["error"] = f"session creation failed: {json.dumps(s)[:200]}"
        return rec
    rec["session_id"] = sid
    rec["primer"] = run_primer(token, sid, pid, primer)
    offset = 1 if rec["primer"] and not rec["primer"].get("error") else 0
    band = (bands or {}).get(case["family"])
    turns_spec = case.get("turns") or [dict(prompt=case["prompt"], oracle_sql=case.get("oracle_sql"),
                                           extract=case.get("extract"), tolerance=case.get("tolerance", 0.0),
                                           expect=case.get("expect"), waste=case.get("waste"),
                                           targets=case.get("targets"))]
    rec["turns"] = []
    for i, ts in enumerate(turns_spec, 1):
        spec = dict(case, **{k: v for k, v in ts.items() if v is not None})
        expected = expected_for(spec, ocache)
        turn, status, wall = run_turn(token, sid, pid, ts["prompt"], i + offset)
        if turn is None:
            rec["turns"].append({"turn": i, "error": status})
            break
        rd = readings(turn, spec, expected, band)
        rd.update({"turn": i, "status": status, "wall_s": wall, "prompt": ts["prompt"],
                   "expected": expected if not isinstance(expected, set) else sorted(expected),
                   "raw": turn})
        rec["turns"].append(rd)
    # the case's headline readings are its LAST turn's (a multi-turn case is about its end)
    last = next((t for t in reversed(rec["turns"]) if "raw" in t), None)
    if last:
        for k in ("correct", "cost_usd", "input_tokens", "output_tokens", "latency_ms", "llm_calls",
                  "tool_calls", "self_corrections", "fired", "rag_case_count", "waste", "model"):
            rec[k] = last.get(k)
        rec["cost_usd"] = round(sum(t.get("cost_usd", 0) for t in rec["turns"] if "raw" in t), 6)
        rec["input_tokens"] = sum(t.get("input_tokens", 0) for t in rec["turns"] if "raw" in t)
        rec["llm_calls"] = sum(t.get("llm_calls", 0) for t in rec["turns"] if "raw" in t)
        rec["fired"] = sorted({k for t in rec["turns"] if "raw" in t for k in t.get("fired", [])})
        rec["invariants"] = [x for t in rec["turns"] if "raw" in t for x in t["invariants"]]
        rec["expectations"] = [x for t in rec["turns"] if "raw" in t for x in t["expectations"]]
    else:
        rec["error"] = rec["turns"][-1].get("error") if rec["turns"] else "no turn"
    return rec


def run_lever(token, lever, pid, cases, ocache, bands, results, lock, outdir, primer=None):
    for i, c in enumerate(cases, 1):
        if _QUOTA_HIT.is_set():
            print(f"  {lever:11s} stopped before {c['id']}: {QUOTA} — re-run the remaining cases after the window")
            return
        if BUDGET is not None and BUDGET.exceeded():
            print(f"  {lever:11s} stopped before {c['id']}: cost cap reached (${BUDGET.max_usd:.2f}); "
                  f"{len(cases) - i + 1} case(s) not run on this lever")
            return
        rec = run_case(token, lever, pid, c, ocache, bands, primer)
        if QUOTA in json.dumps(rec.get("error") or "") or any(QUOTA in str(t.get("error") or "") for t in rec.get("turns") or []):
            _QUOTA_HIT.set()
            print(f"  {lever:11s} [{i}/{len(cases)}] {c['id']:4s} {QUOTA}")
            return
        with lock:
            results.append(rec)
            json.dump(results, open(os.path.join(outdir, "raw.json"), "w"), indent=1, default=str)
        if rec.get("error"):
            print(f"  {lever:11s} [{i}/{len(cases)}] {c['id']:4s} ERROR {rec['error'][:80]}")
            continue
        oi = f"{sum(1 for x in rec['invariants'] if x['ok'])}/{len(rec['invariants'])}"
        oe = f"{sum(1 for x in rec['expectations'] if x['ok'])}/{len(rec['expectations'])}"
        cr = {True: "RIGHT", False: "WRONG", None: "  —  "}[rec.get("correct")]
        print(f"  {lever:11s} [{i}/{len(cases)}] {c['id']:4s} {cr} ${rec['cost_usd']:.4f} {rec['input_tokens']:>6d}tok "
              f"{rec['llm_calls']}llm {rec['self_corrections']}sc {str(rec.get('latency_ms') or 0):>6s}ms  "
              f"inv {oi} exp {oe}  fired={','.join(rec['fired']) or '—'}"
              + (f"  WASTE" if rec.get("waste") is True else ""))


# ── frontier, baseline, accept ───────────────────────────────────────────────
def frontier(results):
    by_id = {c["id"]: c for c in CASES}
    out = {}
    for lv in sorted({r["lever"] for r in results}):
        rs = [r for r in results if r["lever"] == lv and not r.get("error")]
        scored = [r for r in rs if r.get("correct") is not None]
        right = [r for r in scored if r["correct"]]
        # an oracle-checked case whose answer declined is neither right nor wrong — it is a
        # non-answer, counted apart so "17/17 correct" never hides seven declines
        declined = [r for r in rs if r.get("correct") is None and (by_id.get(r["id"]) or {}).get("oracle_sql")]
        out[lv] = {
            "cases": len(rs), "scored": len(scored), "correct": len(right), "declined": len(declined),
            "answered_rate": round(len(right) / (len(scored) + len(declined)), 3) if (scored or declined) else None,
            "correctness": round(len(right) / len(scored), 3) if scored else None,
            "cost_total": round(sum(r["cost_usd"] for r in rs), 4),
            "primer_cost_total": round(sum(float((r.get("primer") or {}).get("cost_usd") or 0) for r in rs), 4),
            "cost_per_correct": round(sum(r["cost_usd"] for r in rs) / len(right), 4) if right else None,
            "p50_latency_ms": st.median([r["latency_ms"] for r in rs if r.get("latency_ms")]) if rs else None,
            "llm_calls_per_case": round(sum(r["llm_calls"] for r in rs) / len(rs), 2) if rs else None,
            "self_corrections": sum(r["self_corrections"] for r in rs),
            "waste": sum(1 for r in rs if r.get("waste") is True),
            "by_family": {},
        }
        for fam in sorted({r["family"] for r in rs}):
            fr = [r for r in rs if r["family"] == fam]
            sc = [r for r in fr if r.get("correct") is not None]
            out[lv]["by_family"][fam] = {
                "cases": len(fr), "correct": sum(1 for r in sc if r["correct"]), "scored": len(sc),
                "cost": round(sum(r["cost_usd"] for r in fr), 4),
                "p95_input_tokens": sorted(r["input_tokens"] for r in fr)[int(0.95 * (len(fr) - 1))] if fr else None,
            }
    return out


def lane_gap(results, a_lv=None, b_lv="OCREACT"):
    """Per case: the Optimize lane vs the ReAct lane (@OCREACT) on the same question, same model —
    correctness, cost, LLM calls. A case the Optimize lane gets right and the ReAct lane does not
    (or gets right at a multiple of the price) is where a fix on the ReAct lane is owed.

    The Optimize side is the COLD lever (@OCFL-NORAG) whenever it ran: @OCFL is warm by design —
    its repository holds every accuracy case's own oracle SQL — so against it the ReAct lane is
    compared with a stored answer, not with a lane (found on the first comparison, 2026-10-29:
    20-70x "cheaper" Optimize answers that were replays)."""
    if a_lv is None:
        ran = {r.get("lever") for r in results}
        a_lv = "OCFL-NORAG" if "OCFL-NORAG" in ran else "OCFL"
    by = {}
    for r in results:
        if not r.get("error"):
            by.setdefault(r["id"], {})[r["lever"]] = r
    rows = []
    for cid, lv in by.items():
        if a_lv in lv and b_lv in lv:
            a, b = lv[a_lv], lv[b_lv]
            ca, cb = a.get("correct"), b.get("correct")
            ratio = round(b.get("cost_usd", 0) / a["cost_usd"], 1) if a.get("cost_usd") else None
            kind = ("both right" if ca and cb else "OPTIMIZE ONLY" if ca and cb is False else
                    "REACT ONLY" if cb and ca is False else "both wrong" if ca is False and cb is False
                    else "unscored")
            rows.append((cid, ca, cb, a.get("cost_usd"), b.get("cost_usd"), ratio,
                         a.get("llm_calls"), b.get("llm_calls"), kind))
    return rows


def gap(results):
    """Per case: OCFL − OCWEAK correctness — the lane's remaining dependence on intelligence."""
    by = {}
    for r in results:
        if not r.get("error"):
            by.setdefault(r["id"], {})[r["lever"]] = r.get("correct")
    rows = []
    for cid, lv in by.items():
        if "OCFL" in lv and "OCWEAK" in lv:
            a, b = lv["OCFL"], lv["OCWEAK"]
            kind = ("carried" if a and b else "GAP (pass proposal)" if a and b is False else
                    "both wrong (engine/data/oracle?)" if a is False and b is False else
                    "weak right, mid wrong (look)" if b and a is False else "unscored")
            rows.append((cid, a, b, kind))
    return rows


def load_baseline():
    try:
        return json.load(open(BASELINE_PATH))
    except Exception:  # noqa: BLE001
        return {}


def bands_from(base):
    """Per family: the accepted run's p95 input tokens × 1.5 — O7's band."""
    out = {}
    for lv, f in (base.get("frontier") or {}).items():
        for fam, v in (f.get("by_family") or {}).items():
            if v.get("p95_input_tokens"):
                out[fam] = {"max_input_tokens": max(out.get(fam, {}).get("max_input_tokens", 0),
                                                    int(v["p95_input_tokens"] * 1.5))}
    return out


def pending_harvest():
    try:
        led = json.load(open(LEDGER_PATH))
    except Exception:  # noqa: BLE001
        return []
    return [k for k, e in (led.get("entries") or {}).items() if e.get("status") == "new"]


def write_baseline(results, fr):
    base = load_baseline()
    pending = pending_harvest()
    drops = []
    for lv, f in fr.items():
        was = ((base.get("frontier") or {}).get(lv) or {}).get("correctness")
        now = f.get("correctness")
        if was is not None and now is not None and now < was:
            drops.append(f"{lv}: correctness {was} → {now}")
    if (drops or pending) and not FORCE_ACCEPT:
        sys.exit("REFUSED: " + " · ".join(drops + ([f"{len(pending)} harvested candidate(s) undispositioned"] if pending else []))
                 + " — a run is accepted only when correctness never drops and the ledger is clear "
                   "(--force-accept records an override)")
    cases = {}
    for r in results:
        if r.get("error"):
            continue
        cases[f"{r['lever']}/{r['id']}"] = {k: r.get(k) for k in
                                           ("correct", "cost_usd", "input_tokens", "llm_calls", "self_corrections",
                                            "fired", "latency_ms", "waste", "model")}
    json.dump({"written": datetime.now(timezone.utc).isoformat(timespec="seconds"), "frontier": fr,
               "cases": cases, "case_count": len(cases),
               "forced": {"drops": drops, "pending": pending} if (drops or pending) else {}},
              open(BASELINE_PATH, "w"), indent=1, sort_keys=True, default=str)
    return BASELINE_PATH


def diff_baseline(results, base):
    drift = []
    bc = base.get("cases") or {}
    for r in results:
        if r.get("error"):
            continue
        k = f"{r['lever']}/{r['id']}"
        b = bc.get(k)
        if not b:
            drift.append((k, "new", ""))
            continue
        if b.get("correct") != r.get("correct"):
            drift.append((k, "correct", f"{b.get('correct')} → {r.get('correct')}"))
        for m, tol in (("cost_usd", 0.25), ("input_tokens", 0.25), ("llm_calls", 0)):
            was, now = b.get(m), r.get(m)
            if was is not None and now is not None and was and abs(now - was) > tol * was:
                drift.append((k, m, f"{was} → {now}"))
        if sorted(b.get("fired") or []) != sorted(r.get("fired") or []):
            drift.append((k, "fired", f"{b.get('fired')} → {r.get('fired')}"))
    return drift


# ── report ───────────────────────────────────────────────────────────────────
def write_report(outdir, results, fr, drift, levers):
    L = []
    A = L.append
    A("# Optimize campaign\n")
    A(f"_{datetime.now(timezone.utc).isoformat(timespec='seconds')} · levers {', '.join(levers)} · one session per case_\n")
    A("## The frontier\n")
    A("| Lever | Cases | Correct | Declined | Correctness (of scored) | Answered right (of oracle cases) | Cost | Cost / correct | p50 latency | LLM calls / case | Self-corr. | Waste |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lv, f in fr.items():
        A(f"| {lv} | {f['cases']} | {f['correct']}/{f['scored']} | {f['declined']} | {f['correctness']} | {f['answered_rate']} | ${f['cost_total']} | "
          f"{'$' + str(f['cost_per_correct']) if f['cost_per_correct'] is not None else '—'} | {f['p50_latency_ms']} ms | "
          f"{f['llm_calls_per_case']} | {f['self_corrections']} | {f['waste']} |")
    A("")
    g = gap(results)
    if g:
        A("## The gap — OCFL − OCWEAK per case\n")
        A("| Case | OCFL | OCWEAK | Reading |\n|---|---|---|---|")
        for cid, a, b, kind in g:
            A(f"| {cid} | {a} | {b} | {kind} |")
        A("")
    lg = lane_gap(results)
    if lg:
        _a = "OCFL-NORAG" if any(r.get("lever") == "OCFL-NORAG" for r in results) else "OCFL"
        A(f"## The lanes — Optimize (@{_a}{', cold' if _a == 'OCFL-NORAG' else ', WARM: seeded with the answers'}) "
          "vs ReAct (@OCREACT), same model, same question\n")
        A("| Case | Optimize | ReAct | $ Optimize | $ ReAct | × | LLM O | LLM R | Reading |\n"
          "|---|---|---|---|---|---|---|---|---|")
        for cid, ca, cb, xa, xb, ratio, la, lb, kind in lg:
            A(f"| {cid} | {ca} | {cb} | {xa} | {xb} | {ratio if ratio is not None else '—'} | {la} | {lb} | {kind} |")
        A("")
    A("## Per case\n")
    A("| Lever | Case | Correct | Cost | In tokens | LLM | Tools | SC | Latency | Fired | Inv | Exp | Waste |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        if r.get("error"):
            A(f"| {r['lever']} | {r['id']} | ERROR | | | | | | | | | | {r['error'][:60]} |")
            continue
        oi = f"{sum(1 for x in r['invariants'] if x['ok'])}/{len(r['invariants'])}"
        oe = f"{sum(1 for x in r['expectations'] if x['ok'])}/{len(r['expectations'])}"
        A(f"| {r['lever']} | {r['id']} | {r.get('correct')} | ${r['cost_usd']:.4f} | {r['input_tokens']} | {r['llm_calls']} | "
          f"{r['tool_calls']} | {r['self_corrections']} | {r.get('latency_ms')} | {', '.join(r['fired']) or '—'} | {oi} | {oe} | "
          f"{'YES' if r.get('waste') is True else ''} |")
    A("")
    bad = [(r, x) for r in results if not r.get("error") for x in r["invariants"] + r["expectations"] if not x["ok"]]
    if bad:
        A("## Not satisfied\n")
        for r, x in bad:
            A(f"- **{r['lever']} · {r['id']} · {x['id']}** — {x['detail']}")
        A("")
    if drift:
        A("## Drift vs the last accepted baseline\n")
        for k, m, d in drift:
            A(f"- `{k}` {m} {d}")
        A("")
    A("## Case notes\n")
    for r in results:
        A(f"### {r['lever']} · {r['id']} — {r.get('note', '')}\n")
        for t in r.get("turns") or []:
            if "raw" not in t:
                A(f"**turn {t.get('turn')}: ERROR** {t.get('error')}\n")
                continue
            A(f"**turn {t['turn']} prompt:** {t['prompt']}\n")
            A(f"**expected:** `{t.get('expected')}` · **{t.get('correct_detail')}**\n")
            A(f"**answer (500c):** {t['answer'].strip()[:500]}\n")
    p = os.path.join(outdir, "report.md")
    open(p, "w").write("\n".join(L))
    return p


def main():
    global FORCE_ACCEPT
    ap = argparse.ArgumentParser()
    ap.add_argument("--levers", default="OCFL")
    ap.add_argument("--only", default="")
    ap.add_argument("--case", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--baseline", default="diff", choices=("diff", "write", "none"))
    ap.add_argument("--from", dest="from_dirs", default="")
    ap.add_argument("--force-accept", action="store_true")
    ap.add_argument("--serial", action="store_true")
    ap.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                    help=f"hard cost cap for the run in USD, read from the ledger (default {DEFAULT_MAX_USD}; 0 = no cap)")
    args = ap.parse_args()
    FORCE_ACCEPT = args.force_accept

    if args.from_dirs:
        merged = {}
        for d in [x.strip() for x in args.from_dirs.split(",") if x.strip()]:
            for r in json.load(open(os.path.join(d, "raw.json"))):
                if not r.get("error"):
                    merged[f"{r['lever']}/{r['id']}"] = r
        results = list(merged.values())
        fr = frontier(results)
        if args.baseline != "write":
            sys.exit("--from needs --baseline write")
        print(f"{len(results)} cases from {args.from_dirs}\nbaseline written: {write_baseline(results, fr)}")
        return 0

    token = login()
    profs = {p.get("tag"): p for p in req("GET", "/api/v1/profiles", token).get("profiles") or []}
    active = set(req("GET", "/api/v1/profiles", token).get("active_for_consumption_profile_ids") or [])
    levers = [x.strip() for x in args.levers.split(",") if x.strip()]
    for lv in levers:
        if lv not in ALL_LEVERS:
            sys.exit(f"unknown lever {lv!r} (one of {ALL_LEVERS})")
        if lv not in profs:
            sys.exit(f"@{lv} absent — run agents.py ensure")
        if profs[lv]["id"] not in active:
            sys.exit(f"@{lv} is not activated")

    def select(lv):
        cs = cases_for(lv)
        if args.only:
            fams = {f.strip().upper() for f in args.only.split(",")}
            cs = [c for c in cs if c["family"] in fams]
        if args.case:
            ids = {c.strip().upper() for c in args.case.split(",")}
            cs = [c for c in cs if c["id"].upper() in ids]
        return cs

    total = sum(len(select(lv)) for lv in levers)
    print(f"{len(levers)} lever(s) · {total} cases · " + " · ".join(f"@{lv}" for lv in levers))
    if args.dry_run:
        for lv in levers:
            for c in select(lv):
                print(f"  {lv:11s} {c['id']:4s} [{c['family']}] → {', '.join(c['targets']) or '—':40s} {c['prompt'][:70]}")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join("tda_eval", "optimize_campaign", stamp)
    os.makedirs(outdir, exist_ok=True)
    base = load_baseline()
    bands = bands_from(base)
    ocache, results, lock = {}, [], threading.Lock()
    global BUDGET
    BUDGET = Budget(args.max_usd, USER)
    print(f"cost cap: ${args.max_usd:.2f}" if args.max_usd else "cost cap: none")
    threads = []
    for lv in levers:
        t = threading.Thread(target=run_lever, args=(token, lv, profs[lv]["id"], select(lv), ocache, bands,
                                                     results, lock, outdir, profs[lv].get("session_primer")))
        t.start()
        if args.serial:
            t.join()
        else:
            threads.append(t)
    for t in threads:
        t.join()
    order = {(lv, c["id"]): i for lv in levers for i, c in enumerate(select(lv))}
    results.sort(key=lambda r: (levers.index(r["lever"]), order.get((r["lever"], r["id"]), 999)))
    fr = frontier(results)
    drift = diff_baseline(results, base) if args.baseline != "none" else []
    path = write_report(outdir, results, fr, drift, levers)
    print(f"\nreport: {path}")
    for lv, f in fr.items():
        print(f"  {lv:11s} correct {f['correct']}/{f['scored']} · declined {f['declined']} · answered-right {f['answered_rate']} · ${f['cost_total']} · cost/correct "
              f"{f['cost_per_correct']} · p50 {f['p50_latency_ms']} ms · {f['llm_calls_per_case']} llm/case · waste {f['waste']}")
    for cid, a, b, kind in gap(results):
        print(f"  gap {cid:4s} OCFL={a} OCWEAK={b} → {kind}")
    for cid, ca, cb, xa, xb, ratio, la, lb, kind in lane_gap(results):
        print(f"  lane {cid:4s} Optimize={ca} ReAct={cb} · ${xa} vs ${xb} ({ratio}×) · {la} vs {lb} LLM → {kind}")
    print(BUDGET.line())
    with open(path, "a") as fh:
        fh.write(f"\n## Cost\n\n{BUDGET.line()}\n")
    if args.baseline == "write" and BUDGET.hit:
        sys.exit("REFUSED: the cost cap ended this run early — a partial run is never the accepted "
                 "baseline. Re-run with a higher --max-usd, or accept saved runs with --from.")
    if args.baseline == "write":
        print(f"baseline written: {write_baseline(results, fr)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
