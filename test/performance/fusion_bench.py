"""
Fusion Optimizer before/after benchmark — drives a fixed query set through ONE
tool_enabled profile (fresh session per query) and reads the persisted turn as
ground truth (tokens, cost, latency, LLM calls, self-corrections, tool errors,
the SQL the planner produced, the final answer).

Run:  python3 test/performance/fusion_bench.py --profile-tag OPGGT --label before
      python3 test/performance/fusion_bench.py --compare before after

Results land in test/performance/results/fusion_bench_<label>.json
"""
import argparse
import glob
import json
import os
import sys
import time
import statistics as st

import requests

BASE = os.environ.get("UDERIA_BASE_URL", "http://localhost:5050")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

QUERIES = [
    "how many customers do we have?",
    "what are the top 5 products based on revenue generated?",
    "which customer has the most tickets?",
    "what are the top 5 customers based on revenue generated?",
    "which customer state has the most tickets?",
    "what is the average sale amount per customer state?",
    "list the 3 best selling product brands by quantity sold",
    "how many sales happened in the past 30 days?",
]


def login(user="admin", pw="admin"):
    r = requests.post(f"{BASE}/api/v1/auth/login", json={"username": user, "password": pw}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def resolve_profile(jwt, tag):
    r = requests.get(f"{BASE}/api/v1/profiles", headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    for p in d.get("profiles", []):
        if p.get("tag") == tag:
            return p["id"], d.get("user_uuid")
    raise SystemExit(f"profile tag {tag} not found")


def run_query(jwt, profile_id, q, timeout_s=240):
    h = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    sid = requests.post(f"{BASE}/api/v1/sessions", headers=h, json={"profile_id": profile_id}, timeout=30).json()["session_id"]
    tid = requests.post(f"{BASE}/api/v1/sessions/{sid}/query", headers=h,
                        json={"prompt": q, "profile_id": profile_id}, timeout=30).json()["task_id"]
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(2)
        s = requests.get(f"{BASE}/api/v1/tasks/{tid}", headers=h, timeout=30).json()
        if s.get("status") in ("complete", "failed", "error"):
            return sid, s.get("status")
    return sid, "timeout"


def read_turn(user_uuid, sid):
    files = glob.glob(f"tda_sessions/{user_uuid}/{sid}.json")
    if not files:
        return None
    d = json.load(open(files[0]))
    wh = d.get("last_turn_data", {}).get("workflow_history") or []
    return wh[-1] if wh else None


def analyze(turn):
    out = {
        "tin": turn.get("turn_input_tokens") or 0, "tout": turn.get("turn_output_tokens") or 0,
        "cost": turn.get("turn_cost") or 0, "dur_ms": turn.get("duration_ms") or 0,
        "status": turn.get("status"), "llm_calls": 0, "plan_tokens": None, "cached_tokens": 0,
        "corrections": 0, "tool_errors": 0, "tool_calls": [], "sql": [], "steps": [],
        "answer": (turn.get("final_summary") or "")[:300],
    }
    for e in turn.get("execution_trace") or []:
        a = e.get("action", {}); r = e.get("result")
        tn = a.get("tool_name")
        if tn == "TDA_SystemLog":
            det = (a.get("arguments") or {}).get("details") or {}
            if isinstance(det, dict):
                s = str(det.get("summary", ""))
                if "input_tokens" in det:
                    out["llm_calls"] += 1
                    out["cached_tokens"] += int(det.get("cached_tokens") or 0)
                    if "meta-plan" in s:
                        out["plan_tokens"] = det["input_tokens"]
                    if "self-correction" in s.lower() or "recovery" in s.lower():
                        out["corrections"] += 1
                if s:
                    out["steps"].append(s[:80])
        else:
            out["tool_calls"].append(tn)
            sql = (a.get("arguments") or {}).get("sql")
            if isinstance(sql, str):
                out["sql"].append(sql[:300])
            if isinstance(r, dict) and r.get("status") == "error":
                out["tool_errors"] += 1
    return out


def summarize(rows):
    def m(k):
        v = [r[k] for r in rows if r.get(k) is not None]
        return (st.mean(v) if v else 0, sum(v))
    return {
        "n": len(rows),
        "tin_mean": m("tin")[0], "tin_sum": m("tin")[1],
        "tout_mean": m("tout")[0],
        "cost_sum": m("cost")[1], "dur_mean_s": m("dur_ms")[0] / 1000,
        "llm_calls_sum": m("llm_calls")[1], "corrections_sum": m("corrections")[1],
        "tool_errors_sum": m("tool_errors")[1], "plan_tokens_mean": m("plan_tokens")[0],
        "cached_sum": m("cached_tokens")[1],
        "non_success": sum(1 for r in rows if r.get("status") != "success"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-tag", default="OPGGT")
    ap.add_argument("--label", default="run")
    ap.add_argument("--compare", nargs=2)
    ap.add_argument("--queries", nargs="*")
    a = ap.parse_args()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    if a.compare:
        A = json.load(open(os.path.join(RESULTS_DIR, f"fusion_bench_{a.compare[0]}.json")))
        B = json.load(open(os.path.join(RESULTS_DIR, f"fusion_bench_{a.compare[1]}.json")))
        sa, sb = summarize(A["rows"]), summarize(B["rows"])
        print(f"{'metric':22} {a.compare[0]:>12} {a.compare[1]:>12}   delta")
        for k in sa:
            va, vb = sa[k], sb[k]
            d = (vb - va) / va * 100 if isinstance(va, (int, float)) and va else 0
            print(f"{k:22} {va:12.4f} {vb:12.4f}  {d:+6.1f}%")
        print("\nper-query (tin / cost / calls / corr / errors / dur s):")
        for ra, rb in zip(A["rows"], B["rows"]):
            print(f"  {ra['query'][:55]:55}")
            print(f"     A: {ra['tin']:6} {ra['cost']:.4f} {ra['llm_calls']} {ra['corrections']} {ra['tool_errors']} {ra['dur_ms']/1000:5.1f} | {ra['sql'][:1]}")
            print(f"     B: {rb['tin']:6} {rb['cost']:.4f} {rb['llm_calls']} {rb['corrections']} {rb['tool_errors']} {rb['dur_ms']/1000:5.1f} | {rb['sql'][:1]}")
        return

    jwt = login()
    pid, user_uuid = resolve_profile(jwt, a.profile_tag)
    user_uuid = user_uuid or os.environ.get("UDERIA_USER_UUID")
    if not user_uuid:
        me = requests.get(f"{BASE}/api/v1/auth/me", headers={"Authorization": f"Bearer {jwt}"}, timeout=30).json()
        user_uuid = me.get("user_uuid") or me.get("uuid") or (me.get("user") or {}).get("uuid") or (me.get("user") or {}).get("user_uuid")
    rows = []
    for q in (a.queries or QUERIES):
        sid, status = run_query(jwt, pid, q)
        turn = read_turn(user_uuid, sid)
        row = {"query": q, "session_id": sid, "task_status": status}
        row.update(analyze(turn) if turn else {"status": "missing", "tin": 0, "tout": 0, "cost": 0, "dur_ms": 0,
                                                "llm_calls": 0, "corrections": 0, "tool_errors": 0, "sql": [], "cached_tokens": 0})
        rows.append(row)
        print(f"[{a.label}] {q[:50]:50} tin={row['tin']:6} cost={row['cost']:.4f} calls={row['llm_calls']} "
              f"corr={row['corrections']} err={row['tool_errors']} dur={row['dur_ms']/1000:5.1f}s sid={sid[:8]}")
        sys.stdout.flush()
    out = {"label": a.label, "profile_tag": a.profile_tag, "ts": time.time(), "rows": rows, "summary": summarize(rows)}
    path = os.path.join(RESULTS_DIR, f"fusion_bench_{a.label}.json")
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(out["summary"], indent=1))
    print("saved", path)


if __name__ == "__main__":
    main()
