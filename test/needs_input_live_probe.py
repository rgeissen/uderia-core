"""Needs-input live probe (PROP-011) — every face of a question, end to end, on a TEST server.

Drives the real server with the fire drill on (`TDA_NEEDS_INPUT_INJECT=1`, and a short
`INPUT_ELICIT_HOLD_SECONDS`): a marked prompt raises its question in code on any engine, so
the machinery around a question is exercised without betting on a model's cooperation:

  A  a choice → input_required (declared) → answered over REST → the re-entry turn completes (reentry_of)
  B  a typed value → declined → the turn says so
  C  remembered for the session → the next marked turn is answered from the default, nothing asked
  D  an unconnected connector → auth_required → "connected" → the re-entry completes
  E  a data source elicits → held, then the turn ends asking → answered → the re-entry completes
  F  the coordinator: its own turn stops to ask (the drill fires on the parent); relay noted
  G  a scheduled run waits → delivered through its webhook → answered → resumed and delivered → another expires unanswered
  H  a flow pauses at the node that asked → answered → resumed from that node to completion

Each step is one or two real turns of the campaign's weak agents (cents). Requires the campaign
agents (`test/trust_campaign/agents.py ensure`), admin/admin on `UDERIA_BASE` (localhost:5050),
and the server started with the drill environment. Prints PASS/FAIL per step and the spend.

Usage: python test/needs_input_live_probe.py [--max-usd 0.50] [--steps A,B,...]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test")); sys.path.insert(0, str(ROOT / "src"))
BASE = os.environ.get("UDERIA_BASE", "http://localhost:5050")
PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def req(method, path, token=None, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}


def poll_task(tok, tid, secs=300):
    t0 = time.time()
    while time.time() - t0 < secs:
        st, task = req("GET", f"/api/v1/tasks/{tid}", tok)
        if task.get("status") in ("complete", "input_required", "auth_required", "error", "failed", "cancelled"):
            return task
        time.sleep(3)
    return task


def ask(tok, pid, prompt, sid=None):
    if not sid:
        st, s = req("POST", "/api/v1/sessions", tok, {})
        sid = s.get("session_id")
    st, q = req("POST", f"/api/v1/sessions/{sid}/query", tok, {"prompt": prompt, "profile_id": pid},
                headers={"X-Uderia-Accepts": "input_required"})
    return sid, poll_task(tok, q.get("task_id"))


def pick(rec, fallback="kg-sales-1"):
    """An answer the question's own validator accepts: the first option where the item
    offers options (a model's `choose` names its own), else the drill's graph id."""
    item = (rec.get("items") or [{}])[0]
    opts = item.get("options") or []
    val = (opts[0].get("value") if isinstance(opts[0], dict) else opts[0]) if opts else fallback
    return {item.get("header", "value"): val}


def answer(tok, rec, values, decision="accept", remember=None):
    body = {"decision": decision, "answer": values, "resume": rec.get("resume")}
    if remember:
        body["remember"] = remember
    return req("POST", f"/api/v1/input-requests/{rec['id']}/answer", tok, body, headers={"X-Uderia-Accepts": "input_required"})


class _Hook(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            _Hook.received.append(json.loads(self.rfile.read(n) or b"{}"))
        except Exception:  # noqa: BLE001
            _Hook.received.append({"raw": True})
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

    def log_message(self, *a):  # quiet
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.50)
    ap.add_argument("--steps", default="A,B,C,D,E,F,G,H")
    a = ap.parse_args()
    steps = {s.strip().upper() for s in a.steps.split(",") if s.strip()}
    st, d = req("POST", "/api/v1/auth/login", body={"username": os.environ.get("UDERIA_USER", "admin"), "password": os.environ.get("UDERIA_PASS", "admin")})
    tok = d.get("token")
    if not tok:
        print("login failed", st, d); return 2
    from campaign_budget import Budget
    budget = Budget(a.max_usd, os.environ.get("UDERIA_USER", "admin"), db_path=str(ROOT / "tda_auth.db"))
    st, profs = req("GET", "/api/v1/profiles", tok)
    by_tag = {p.get("tag"): p for p in (profs if isinstance(profs, list) else profs.get("profiles") or [])}
    ide, coo = by_tag.get("TCIDE"), by_tag.get("TCCOO")
    if not ide:
        print("the campaign agents are not on this server (agents.py ensure)"); return 2
    pid = ide["id"]

    def guard(step):
        if budget.exceeded():
            print(f"budget cap reached ({budget.line()}) — stopping before {step}"); return False
        return True

    if "A" in steps and guard("A"):
        print("\n[A] a choice: input_required → answered → the re-entry completes")
        sid, t = ask(tok, pid, "Summarise the Sales graph for me. [needs-input:choose]")
        rec = (t.get("result") or {}).get("input_request") or {}
        check("the declaring client reads input_required with the question and its binding",
              t.get("status") == "input_required" and rec.get("kind") == "choose" and rec.get("resume") and rec.get("raised_by") == "code", json.dumps(t.get("status")))
        check("the answer carries the question verbatim", "Which knowledge graph do you mean by 'Sales'?" in str((t.get("result") or {}).get("final_answer_text")))
        st, got = answer(tok, rec, {"Knowledge Gr": "kg-sales-2"} if rec.get("items") and rec["items"][0].get("header") == "Knowledge Gr" else {rec["items"][0]["header"]: "kg-sales-2"})
        check("the answer is accepted and a task continues under the same id", st == 202 and got.get("task_id") == t.get("task_id"), f"{st} {got}")
        t2 = poll_task(tok, got.get("task_id") or t.get("task_id"))
        check("the re-entry turn completes (no second question: the answer is on the ledger)", t2.get("status") == "complete" and not (t2.get("result") or {}).get("input_request"), t2.get("status"))
        st, again = answer(tok, rec, {rec["items"][0]["header"]: "kg-sales-1"})
        check("the same binding a second time is refused", st in (409, 403), f"{st} {again}")
        st, sess = req("GET", f"/session/{sid}", tok)   # the UI blueprint has no /api prefix
        wf = (sess or {}).get("workflow_history") or []
        check("the records: turn 1 input_required with the question, turn 2 carries reentry_of",
              len(wf) >= 2 and wf[0].get("status") == "input_required" and (wf[0].get("input_request") or {}).get("id") == rec["id"] and wf[1].get("reentry_of") == rec["id"],
              json.dumps([(w.get("status"), w.get("reentry_of")) for w in wf]))
        st, stats = req("GET", f"/api/v1/input-requests/stats?profile_id={pid}", tok)
        check("the stats count it", st == 200 and stats.get("accepted", 0) >= 1 and stats.get("by_raiser", {}).get("code", 0) >= 1)

    if "B" in steps and guard("B"):
        print("\n[B] a typed value, declined")
        sid, t = ask(tok, pid, "Give me this quarter's figures. [needs-input:provide]")
        rec = (t.get("result") or {}).get("input_request") or {}
        check("a provide question is raised", t.get("status") == "input_required" and rec.get("kind") == "provide")
        st, got = answer(tok, rec, None, decision="decline")
        t2 = poll_task(tok, got.get("task_id") or t.get("task_id"))
        check("the declined re-entry completes and says the user declined", t2.get("status") == "complete" and "declin" in str((t2.get("result") or {}).get("final_answer_text", "")).lower(), str((t2.get("result") or {}).get("final_answer_text", ""))[:160])

    if "C" in steps and guard("C"):
        print("\n[C] remembered for the session")
        sid, t = ask(tok, pid, "Open the Sales graph. [needs-input:choose]")
        rec = (t.get("result") or {}).get("input_request") or {}
        st, got = answer(tok, rec, {rec["items"][0]["header"]: "kg-sales-1"}, remember="session")
        poll_task(tok, got.get("task_id") or t.get("task_id"))
        sid2, t3 = ask(tok, pid, "And the Sales graph again. [needs-input:choose]", sid=sid)
        st, sess = req("GET", f"/session/{sid}", tok)   # the UI blueprint has no /api prefix
        wf = (sess or {}).get("workflow_history") or []
        check("the next marked turn in the session asks nothing — the remembered answer is used and recorded",
              t3.get("status") == "complete" and wf and (wf[-1].get("input_defaults_used") or [{}])[0].get("scope") == "session",
              f"{t3.get('status')} {json.dumps((wf[-1] if wf else {}).get('input_defaults_used'))}")

    if "D" in steps and guard("D"):
        print("\n[D] auth_required")
        sid, t = ask(tok, pid, "Read my latest emails. [needs-input:auth]")
        rec = (t.get("result") or {}).get("input_request") or {}
        check("the turn reads auth_required with a connector item", t.get("status") == "auth_required" and rec.get("kind") == "auth" and (rec.get("items") or [{}])[0].get("connector", {}).get("platform") == "google", t.get("status"))
        st, got = answer(tok, rec, {rec["items"][0]["header"]: "connected"})
        t2 = poll_task(tok, got.get("task_id") or t.get("task_id"))
        check("connected → the re-entry completes", st == 202 and t2.get("status") == "complete", f"{st} {t2.get('status')}")

    if "E" in steps and guard("E"):
        print("\n[E] a data source elicits")
        sid, t = ask(tok, pid, "Run the warehouse report. [needs-input:elicit]")
        rec = (t.get("result") or {}).get("input_request") or {}
        check("the held elicitation ends the turn asking a provide with the schema's options",
              t.get("status") == "input_required" and rec.get("raised_by") == "data_source" and [o["value"] for o in (rec.get("items") or [{}])[0].get("options", [])] == ["north", "south"], t.get("status"))
        st, got = answer(tok, rec, {rec["items"][0]["header"]: "north"})
        t2 = poll_task(tok, got.get("task_id") or t.get("task_id"))
        check("the re-entry completes (the same step finds the answer on the ledger)", t2.get("status") == "complete", t2.get("status"))

    if "F" in steps and coo and guard("F"):
        print("\n[F] the coordinator")
        sid, t = ask(tok, coo["id"], "Which region sold most last month? [needs-input:choose]")
        rec = (t.get("result") or {}).get("input_request") or {}
        check("the coordinator's turn stops to ask (the drill fires on the parent) and the question is on its result", t.get("status") == "input_required" and rec.get("id"), t.get("status"))
        if rec.get("id"):
            st, got = answer(tok, rec, {rec["items"][0]["header"]: "kg-sales-1"})
            t2 = poll_task(tok, got.get("task_id") or t.get("task_id"))
            rec2 = (t2.get("result") or {}).get("input_request") or {}
            if t2.get("status") == "input_required" and rec2.get("raised_by") == "child":
                # the re-entry forwarded the original request (marker and all) to an EXPERT, whose own
                # turn asked; the coordinator RELAYED it — the chain, end to end
                check("an expert's question is relayed by the coordinator as its own, with the chain on the record",
                      rec2.get("parent_id") and str(rec2.get("source", "")).startswith("coordinate:@"), json.dumps(rec2)[:200])
                # the relay answers the child that asked; the coordinator may then route the same
                # (marked) wording to its OTHER expert, which asks again — a known limit (the
                # remembered-answer option covers it): tolerate a bounded number of relays
                relays, t3 = 0, t2
                while relays < 3 and t3.get("status") == "input_required":
                    rq = (t3.get("result") or {}).get("input_request") or {}
                    if rq.get("raised_by") != "child":
                        break
                    st, gotn = answer(tok, rq, pick(rq))
                    if st != 202:
                        check("a relayed question takes an answer from its own options", False, f"{st} {json.dumps(gotn)[:160]}")
                        break
                    t3 = poll_task(tok, gotn.get("task_id") or t3.get("task_id"))
                    relays += 1
                check(f"the answer travels down: the expert is continued and the coordinator's re-entry completes (relays answered: {relays})",
                      t3.get("status") == "complete", f"{t3.get('status')} {str((t3.get('result') or {}).get('final_answer_text', ''))[:160]}")
            else:
                check("the coordinator's re-entry completes", t2.get("status") == "complete", t2.get("status"))

    if "G" in steps and guard("G"):
        print("\n[G] a scheduled run waits, delivers, resumes; another expires")
        srv = HTTPServer(("127.0.0.1", 5799), _Hook)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        st, task = req("POST", "/api/v1/scheduled-tasks", tok, {"name": "needs-input probe", "profile_id": pid,
                        "prompt": "Weekly: list the Sales graph's top entities. [needs-input:choose]", "schedule": "interval:1d",
                        "output_channel": "webhook", "output_config": {"webhook_url": "http://127.0.0.1:5799/hook"}})
        tid = task.get("task_id") or task.get("id") or (task.get("task") or {}).get("id")
        check("the task is created", st in (200, 201) and tid, f"{st} {json.dumps(task)[:200]}")
        if tid:
            st, r = req("POST", f"/api/v1/scheduled-tasks/{tid}/run-now", tok, {})
            run = None
            for _ in range(60):
                time.sleep(3)
                st, runs = req("GET", f"/api/v1/scheduled-tasks/{tid}/runs", tok)
                rl = runs.get("runs") if isinstance(runs, dict) else runs
                run = next((x for x in (rl or []) if x.get("status") not in ("running",)), None)
                if run:
                    break
            check("the run records input_required with the question", run and run.get("status") == "input_required" and "Waiting for your input" in str(run.get("result_summary")), json.dumps(run)[:200])
            time.sleep(2)
            hook = next((h for h in _Hook.received if "needs your input" in json.dumps(h) or "stopped to ask" in json.dumps(h)), None)
            check("the question reached the task's webhook with a link", hook is not None and "/?session=" in json.dumps(hook), json.dumps(_Hook.received)[:200])
            st, pend = req("GET", "/api/v1/input-requests?status=pending", tok)
            rec = next((x for x in pend.get("items", []) if str(x.get("task_id", "")).startswith(f"sched:{tid}:")), None)
            check("the Inbox lists it, named for its run", rec is not None)
            if rec:
                st, full = req("GET", f"/api/v1/input-requests/{rec['id']}", tok)
                st, got = answer(tok, full, {full["items"][0]["header"]: "kg-sales-2"})
                check("the answer goes back to the scheduler", st == 202 and got.get("scheduled_task_id") == tid and got.get("run_id"), f"{st} {got}")
                run2 = None
                for _ in range(60):
                    time.sleep(3)
                    st, runs = req("GET", f"/api/v1/scheduled-tasks/{tid}/runs", tok)
                    rl = runs.get("runs") if isinstance(runs, dict) else runs
                    run2 = next((x for x in (rl or []) if x.get("id") == got.get("run_id") and x.get("status") != "running"), None)
                    if run2:
                        break
                check("the resumed run completes and delivers", run2 and run2.get("status") == "success", json.dumps(run2)[:200])
                check("the webhook got the result", any("result" in h and "stopped to ask" not in json.dumps(h) for h in _Hook.received[1:]))
            # expiry: a second run waits, its question is aged, the platform job ends it unanswered
            _Hook.received.clear()
            st, r = req("POST", f"/api/v1/scheduled-tasks/{tid}/run-now", tok, {})
            rec2 = None
            for _ in range(60):
                time.sleep(3)
                # the question is raised at the turn's start; wait until the RUN is waiting too
                st, runs = req("GET", f"/api/v1/scheduled-tasks/{tid}/runs", tok)
                rl = runs.get("runs") if isinstance(runs, dict) else runs
                waiting = [x for x in (rl or []) if x.get("status") == "input_required"]
                st, pend = req("GET", "/api/v1/input-requests?status=pending", tok)
                rec2 = next((x for x in pend.get("items", [])
                             if any(str(x.get("task_id", "")) == f"sched:{tid}:{w.get('id')}" for w in waiting)), None)
                if rec2:
                    break
            if rec2:
                con = sqlite3.connect(str(ROOT / "tda_auth.db")); con.execute("UPDATE input_requests SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (rec2["id"],)); con.commit(); con.close()
                st, r = req("POST", "/api/v1/admin/scheduler/platform-jobs/platform-input-expiry/run-now", tok, {})
                run3 = None
                for _ in range(20):
                    time.sleep(3)
                    st, runs = req("GET", f"/api/v1/scheduled-tasks/{tid}/runs", tok)
                    rl = runs.get("runs") if isinstance(runs, dict) else runs
                    run3 = next((x for x in (rl or []) if x.get("status") == "unanswered"), None)
                    if run3:
                        break
                check("the expiry job ends the waiting run unanswered", run3 is not None, json.dumps(rl)[:200] if 'rl' in dir() else "")
                time.sleep(2)
                check("…and says so through the webhook", any("expired" in json.dumps(h) for h in _Hook.received), json.dumps(_Hook.received)[:200])
            req("DELETE", f"/api/v1/scheduled-tasks/{tid}", tok)
        srv.shutdown()

    if "H" in steps and guard("H"):
        print("\n[H] a flow pauses at the node that asked and resumes there")
        definition = {"nodes": [{"id": "start", "type": "start", "label": "Start"},
                                {"id": "ask", "type": "task_llm", "label": "Ask", "data": {"prompt": "Describe the Sales graph in one line. [needs-input:choose]", "profile_id": pid, "output_as": "line"}},
                                {"id": "end", "type": "end", "label": "End"}],
                      "edges": [{"id": "e1", "source": "start", "target": "ask", "type": "normal"},
                                {"id": "e2", "source": "ask", "target": "end", "type": "normal"}], "scopes": []}
        st, fl = req("POST", "/api/v1/flows", tok, {"name": "needs-input probe", "description": "probe", "definition": definition})
        fid = fl.get("flow_id") or (fl.get("flow") or {}).get("flow_id")
        check("the flow is created", st in (200, 201) and fid, f"{st} {json.dumps(fl)[:200]}")
        if fid:
            st, run = req("POST", f"/api/v1/flows/{fid}/execute", tok, {})
            rid = run.get("run_id")
            r = {}
            for _ in range(80):
                time.sleep(3)
                st, r = req("GET", f"/api/v1/flows/runs/{rid}", tok)
                if r.get("status") in ("input_required", "complete", "error", "cancelled"):
                    break
            check("the run pauses input_required at the node that asked", r.get("status") == "input_required" and any(n.get("node_id") == "ask" and n.get("status") == "input_required" for n in r.get("node_runs", [])), r.get("status"))
            st, pend = req("GET", "/api/v1/input-requests?status=pending", tok)
            node_task = next((n.get("task_id") for n in r.get("node_runs", []) if n.get("node_id") == "ask"), None)
            rec = next((x for x in pend.get("items", []) if x.get("task_id") == node_task), None)
            check("the node's question is pending, keyed by the node's task", rec is not None, f"task {node_task}")
            if rec:
                st, full = req("GET", f"/api/v1/input-requests/{rec['id']}", tok)
                st, got = answer(tok, full, {full["items"][0]["header"]: "kg-sales-2"})
                check("the answer resumes the flow from the node", st == 202 and got.get("flow_run_id") and got.get("paused_run_id") == rid and got.get("node_id") == "ask", f"{st} {got}")
                r2 = {}
                for _ in range(80):
                    time.sleep(3)
                    st, r2 = req("GET", f"/api/v1/flows/runs/{got.get('flow_run_id')}", tok)
                    if r2.get("status") in ("input_required", "complete", "error", "cancelled"):
                        break
                check("the resumed run completes", r2.get("status") == "complete", f"{r2.get('status')} {json.dumps([(n.get('node_id'), n.get('status'), (n.get('error_msg') or '')[:80]) for n in r2.get('node_runs', [])])}")
            req("DELETE", f"/api/v1/flows/{fid}", tok)

    print(f"\nspent: {budget.line()}")
    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} checks ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
