"""Multi-source bench (implementation plan stage 0.3).

Drives the fixture question set (fixtures/multi_source/questions.json) through two agents over
the SAME two MCP servers -- one Optimize (tool_enabled), one Ideate+tools (llm_only +
useMcpTools) -- a fresh session per question, and reads the persisted turn as ground truth.

Fixture: fixtures/multi_source/setup_fixture.py (Teradata logins) and run_endpoints.sh (the two
MCP endpoints on :8893 / :8894). The server must run with TDA_COMPUTE_LINEAGE=1 and
TDA_COMPUTE_SQL=1 (the combine step).

    python test/performance/multi_source_bench.py setup --llm-config-id <id>
    python test/performance/multi_source_bench.py run --label baseline [--lanes optimize react] [--runs 3]
    python test/performance/multi_source_bench.py report baseline [other-label]

Per question it records correctness (every check in the question passes), cost, tokens,
seconds, LLM calls, tool calls, whether a combine ran, and the calls re-routed by Uderia. Two
derived scores:

- source_routing: of the MCP calls that name their server, the share that reached it without a
  re-route (a call Uderia had to re-send counts against the lane);
- join_integrity: of the questions that need both servers, the share whose turn ran a combine
  (a TDA_Compute op:"sql" statement) -- the join was done by code, not in the model's head.

Results: test/performance/results/multi_source_<label>.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics as st
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FIXTURE = os.path.join(HERE, "fixtures", "multi_source")
RESULTS_DIR = os.path.join(HERE, "results")
AGENTS_FILE = os.path.join(FIXTURE, "agents.local.json")  # instance-local ids, gitignored
BASE = os.environ.get("UDERIA_BASE_URL", "http://localhost:5050")

SERVERS = {  # alias -> (display name, port)
    "sales": ("MS Fixture - sales", 8893),
    "crm": ("MS Fixture - crm", 8894),
}
TOOLS = ["base_readQuery", "base_databaseList", "base_tableList",
         "base_columnDescription", "base_tablePreview"]
LANES = {
    "optimize": {"tag": "MSOPT", "profile_type": "tool_enabled", "useMcpTools": False},
    "react": {"tag": "MSIDE", "profile_type": "llm_only", "useMcpTools": True},
}


# ── REST ──────────────────────────────────────────────────────────────────────────────────

class Uderia:
    def __init__(self, base=BASE, user="admin", pw=os.environ.get("UDERIA_PASSWORD", "admin")):
        self.base = base
        r = requests.post(f"{base}/api/v1/auth/login", json={"username": user, "password": pw}, timeout=30)
        r.raise_for_status()
        self.jwt = r.json()["token"]
        self.h = {"Authorization": f"Bearer {self.jwt}", "Content-Type": "application/json"}

    def call(self, method, path, body=None, timeout=60):
        r = requests.request(method, f"{self.base}/api/v1{path}", headers=self.h, json=body, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.text else {}


# ── setup ─────────────────────────────────────────────────────────────────────────────────

def _component_config() -> Dict[str, Any]:
    sys.path.insert(0, REPO)
    from agent_packs.common.component_config import focused_component_config
    return focused_component_config(memory=False, extra_enabled=["compute"])


def setup(llm_config_id: str) -> Dict[str, Any]:
    u = Uderia()
    servers = u.call("GET", "/mcp/servers").get("servers") or []
    ids: Dict[str, str] = {}
    for alias, (name, port) in SERVERS.items():
        hit = next((s for s in servers if s.get("name") == name), None)
        if hit:
            ids[alias] = hit["id"]
        else:
            res = u.call("POST", "/mcp/servers", {"name": name, "host": "127.0.0.1", "port": port,
                                                   "path": "/mcp/", "transport": {"type": "http"}})
            ids[alias] = res["server_id"]
        print(f"server {alias:6s} {ids[alias]}  ({name}, :{port})")

    profiles = u.call("GET", "/profiles").get("profiles") or []
    agents: Dict[str, Any] = {}
    for lane, spec in LANES.items():
        body = {
            "tag": spec["tag"],
            "name": f"Multi-source bench — {lane}",
            "description": "Bench agent over the multi-source fixture: sales (ms_fixture_sales) "
                           "and crm (ms_fixture_crm).",
            "profile_type": spec["profile_type"],
            "llmConfigurationId": llm_config_id,
            "mcpServerId": ids["sales"],
            "useMcpTools": spec["useMcpTools"],
            "tools": TOOLS, "prompts": [],
            "additionalMcpServers": [{"id": ids["crm"], "alias": "crm", "tools": TOOLS, "prompts": []}],
            "componentConfig": _component_config(),
            "knowledgeConfig": {"enabled": False, "collections": []},
            "ragCollections": [], "autocompleteCollections": [],
            "recommenderConfig": {"enabled": False},
            "postureConfig": {"mode": "observe"},
            "session_primer": {"enabled": False},
            "classification_mode": "light",
        }
        hit = next((p for p in profiles if p.get("tag") == spec["tag"]), None)
        if hit:
            u.call("PUT", f"/profiles/{hit['id']}", {k: v for k, v in body.items() if k != "tag"})
            pid = hit["id"]
        else:
            res = u.call("POST", "/profiles", body)
            pid = (res.get("profile") or res)["id"]
        u.call("POST", f"/profiles/{pid}/activate", {}, timeout=300)
        agents[lane] = {"tag": spec["tag"], "profile_id": pid}
        print(f"agent  {lane:9s} @{spec['tag']:6s} {pid}")

    current = u.call("GET", "/profiles/active_for_consumption").get("profile_ids") or []
    merged = current + [a["profile_id"] for a in agents.values() if a["profile_id"] not in current]
    if merged != current:
        u.call("POST", "/profiles/set_active_for_consumption", {"profile_ids": merged})
    out = {"llm_config_id": llm_config_id, "servers": ids, "agents": agents}
    json.dump(out, open(AGENTS_FILE, "w"), indent=1)
    print("written", AGENTS_FILE)
    return out


# ── one question ──────────────────────────────────────────────────────────────────────────

def ask(u: Uderia, profile_id: str, q: str, timeout_s=420):
    sid = u.call("POST", "/sessions", {"profile_id": profile_id})["session_id"]
    tid = u.call("POST", f"/sessions/{sid}/query", {"prompt": q, "profile_id": profile_id})["task_id"]
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(5)   # polls count against the user's hourly request limit
        s = u.call("GET", f"/tasks/{tid}")
        if s.get("status") in ("complete", "failed", "error"):
            return sid, s.get("status")
    return sid, "timeout"


def reset_learning(u: "Uderia", servers: Dict[str, str]) -> int:
    """Delete the learned champion cases in the planner repositories keyed to the FIXTURE's
    servers (and nothing else), so each question starts on day one. Without it, a pass in one
    question seeds a champion that guides the next — and a plan guided by a champion is planned
    differently (the stage-3.0 bench found every champion-guided turn skipped a pass). Returns
    the number of cases removed."""
    fixture = set((servers or {}).values())
    cols = u.call("GET", "/rag/collections")
    cols = cols.get("collections", cols) if isinstance(cols, dict) else cols
    removed = 0
    for c in cols or []:
        if c.get("repository_type") != "planner" or c.get("mcp_server_id") not in fixture:
            continue
        rows = (u.call("GET", f"/rag/collections/{c['id']}/rows") or {}).get("rows") or []
        for r in rows:
            if r.get("id"):
                u.call("DELETE", f"/rag/collections/{c['id']}/rows/{r['id']}")
                removed += 1
    return removed


def read_turn(sid: str) -> Optional[Dict[str, Any]]:
    files = glob.glob(os.path.join(REPO, "tda_sessions", "*", f"{sid}.json"))
    if not files:
        return None
    d = json.load(open(files[0]))
    wh = (d.get("last_turn_data") or {}).get("workflow_history") or []
    return wh[-1] if wh else None


def _walk(o: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def numbers_in(text: str) -> List[float]:
    out = []
    for m in _NUM.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def check_answer(answer: str, checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    nums = numbers_in(answer)
    low = (answer or "").lower()
    failed = []
    for c in checks:
        if "number" in c:
            want, tol = float(c["number"]), float(c.get("tol", 0.005))
            if not any(abs(n - want) <= max(abs(want) * tol, 0.01) for n in nums):
                failed.append(f"number {want}")
        elif "text" in c:
            if c["text"].lower() not in low:
                failed.append(f"text {c['text']!r}")
    return {"correct": not failed, "failed": failed}


def analyze(turn: Dict[str, Any]) -> Dict[str, Any]:
    answer = turn.get("final_summary_text") or turn.get("final_summary") or ""
    # One record per MCP call that named its server (both lanes stamp call_source's shape);
    # dedupe on identity so a record reachable twice in the turn counts once.
    seen, calls = set(), []
    for d in _walk(turn):
        if isinstance(d.get("server_id"), str) and d.get("reason") in ("primary", "qualified", "routed"):
            if id(d) not in seen:
                seen.add(id(d))
                calls.append(d)
    # ReAct records invoked + completed per call: count completed-event sources only when present.
    react_done = [e for e in (turn.get("conversation_agent_events") or [])
                  if e.get("type") == "conversation_tool_completed"]
    if react_done:
        calls = [((e.get("payload") or {}).get("source")) for e in react_done]
        calls = [c for c in calls if isinstance(c, dict) and c.get("server_id")]
    routed = sum(1 for c in calls if c.get("reason") == "routed")
    combine = any(d.get("source_id") == "combine" for d in _walk(turn))
    llm_calls = 0
    for e in turn.get("execution_trace") or []:
        a = e.get("action") or {}
        if a.get("tool_name") == "TDA_SystemLog":
            det = (a.get("arguments") or {}).get("details") or {}
            if isinstance(det, dict) and "input_tokens" in det:
                llm_calls += 1
    tool_calls = len([e for e in (turn.get("execution_trace") or [])
                      if (e.get("action") or {}).get("tool_name") not in (None, "TDA_SystemLog")])
    if react_done:
        tool_calls = len(react_done)
    return {
        "answer": answer[:600], "status": turn.get("status"),
        "tin": turn.get("turn_input_tokens") or 0, "tout": turn.get("turn_output_tokens") or 0,
        "cost": float(turn.get("turn_cost") or 0), "dur_s": (turn.get("duration_ms") or 0) / 1000,
        "llm_calls": llm_calls, "tool_calls": tool_calls,
        "server_calls": len(calls), "rerouted": routed, "combine": combine,
    }


# ── run / report ──────────────────────────────────────────────────────────────────────────

def _commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True).strip()
    except Exception:
        return "?"


def infra_error(r: Dict[str, Any]) -> bool:
    """A question the platform never answered (no turn: rate limit, server down, timeout) — not a
    wrong answer, and never scored as one."""
    return r.get("status") == "missing" or r.get("task_status") == "timeout"


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    infra = sum(1 for r in rows if infra_error(r))
    rows = [r for r in rows if not infra_error(r)]
    both = [r for r in rows if r["needs"] == "both"]
    sc = sum(r.get("server_calls", 0) for r in rows)
    rr = sum(r.get("rerouted", 0) for r in rows)
    ok = [r for r in rows if r.get("correct")]
    m = lambda k: st.mean([r.get(k, 0) for r in rows]) if rows else 0
    return {
        "n": len(rows), "correct": len(ok), "pass_rate": round(len(ok) / len(rows), 3) if rows else 0,
        "pass_rate_both": round(sum(1 for r in both if r.get("correct")) / len(both), 3) if both else None,
        "usd_per_q": round(m("cost"), 5),
        "usd_per_correct": round(sum(r.get("cost", 0) for r in rows) / len(ok), 5) if ok else None,
        "dur_s_mean": round(m("dur_s"), 1), "tin_mean": round(m("tin")),
        "llm_calls_mean": round(m("llm_calls"), 2), "tool_calls_mean": round(m("tool_calls"), 2),
        "source_routing": round(1 - rr / sc, 3) if sc else None,
        "join_integrity": round(sum(1 for r in both if r.get("combine")) / len(both), 3) if both else None,
        "non_success": sum(1 for r in rows if r.get("status") not in ("success", None)),
        "infra_errors": infra,
    }


def run(label: str, lanes: List[str], runs: int, only: Optional[List[str]], rerun_errors: bool = False,
        keep_learning: bool = False) -> None:
    agents = json.load(open(AGENTS_FILE))
    qs = json.load(open(os.path.join(FIXTURE, "questions.json")))["questions"]
    if only:
        qs = [q for q in qs if q["id"] in only]
    u = Uderia()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"multi_source_{label}.json")
    out = json.load(open(path)) if os.path.exists(path) else {
        "label": label, "commit": _commit(), "llm_config_id": agents["llm_config_id"],
        "servers": agents["servers"], "started": time.time(), "rows": [],
        "learning": "kept" if keep_learning else "reset before every question"}
    if rerun_errors:
        out["rows"] = [r for r in out["rows"] if not infra_error(r)]
    done = {(r["lane"], r["id"], r["run"]) for r in out["rows"]}
    for lane in lanes:
        pid = agents["agents"][lane]["profile_id"]
        for n in range(1, runs + 1):
            for q in qs:
                if (lane, q["id"], n) in done:
                    continue
                if not keep_learning:
                    reset_learning(u, agents["servers"])
                sid, status = ask(u, pid, q["question"])
                turn = read_turn(sid)
                row = {"lane": lane, "id": q["id"], "run": n, "needs": q["needs"],
                       "session_id": sid, "task_status": status}
                if turn:
                    row.update(analyze(turn))
                    row.update(check_answer(row["answer"], q["checks"]))
                else:
                    row.update({"status": "missing", "correct": False, "failed": ["no turn"]})
                out["rows"].append(row)
                json.dump(out, open(path, "w"), indent=1)
                print(f"[{label}] {lane:8s} r{n} {q['id']} {'PASS' if row.get('correct') else 'fail'} "
                      f"cost={row.get('cost', 0):.4f} dur={row.get('dur_s', 0):5.1f}s "
                      f"calls={row.get('tool_calls', 0)} rerouted={row.get('rerouted', 0)} "
                      f"combine={row.get('combine')} {row.get('failed') or ''}")
                sys.stdout.flush()
    out["summary"] = {lane: summarize([r for r in out["rows"] if r["lane"] == lane]) for lane in lanes}
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(out["summary"], indent=1))
    print("saved", path)


def report(labels: List[str]) -> None:
    for label in labels:
        d = json.load(open(os.path.join(RESULTS_DIR, f"multi_source_{label}.json")))
        print(f"== {label} (commit {d.get('commit')}, llm {d.get('llm_config_id')})")
        for lane in sorted({r["lane"] for r in d["rows"]}):
            s = summarize([r for r in d["rows"] if r["lane"] == lane])
            print(f"  {lane:9s} " + "  ".join(f"{k}={v}" for k, v in s.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup"); s.add_argument("--llm-config-id", required=True)
    r = sub.add_parser("run"); r.add_argument("--label", required=True)
    r.add_argument("--lanes", nargs="*", default=list(LANES)); r.add_argument("--runs", type=int, default=1)
    r.add_argument("--only", nargs="*")
    r.add_argument("--keep-learning", action="store_true",
                   help="let champion cases learned by one question guide the next (measures the "
                        "learning loop; the default measures day one)")
    r.add_argument("--rerun-errors", action="store_true",
                   help="ask again the questions the platform never answered (rate limit, timeout)")
    p = sub.add_parser("report"); p.add_argument("labels", nargs="+")
    a = ap.parse_args()
    if a.cmd == "setup":
        setup(a.llm_config_id)
    elif a.cmd == "run":
        run(a.label, a.lanes, a.runs, a.only, a.rerun_errors, a.keep_learning)
    else:
        report(a.labels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
