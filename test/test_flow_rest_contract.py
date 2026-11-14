#!/usr/bin/env python3
"""
Rigid contract test for the Flow Execution & Monitoring REST surface.

Validates that the live server behaves EXACTLY as documented in
docs/RestAPI/restAPI.md §3.32 (Flow Execution & Monitoring). This is the
Phase-1 gate: the documentation is only "done" if every documented endpoint,
request field, response shape, status value, and auth rule is real.

It uses a minimal `start -> task_function -> end` flow so execution needs no
LLM provider or MCP server — the executor, run persistence, SSE stream, polling,
node-data aggregation, and cancellation are all exercised with zero external deps.

Usage:
    python test/test_flow_rest_contract.py
    python test/test_flow_rest_contract.py --base-url http://localhost:5050 --user admin --password admin

Exit code 0 = all assertions passed; non-zero = at least one failed.
"""
import argparse
import json
import sys
import time
import uuid

import requests

# ---------------------------------------------------------------------------
# Tiny assertion harness — accumulate, never abort early, fail hard at the end.
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0
_FAILURES = []


_CREATED_SESSIONS = []   # sessions the test created itself (removed in cleanup)


def check(name: str, condition: bool, detail: str = ""):
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        _FAIL += 1
        _FAILURES.append(name + (f"  ({detail})" if detail else ""))
        print(f"  \033[31mFAIL\033[0m  {name}" + (f"  -> {detail}" if detail else ""))


def section(title: str):
    print(f"\n=== {title} ===")


# Minimal executable flow: start -> compute(42) -> end. No LLM, no MCP.
MINIMAL_FLOW = {
    "name": f"contract-test-{uuid.uuid4().hex[:8]}",
    "description": "Rigid REST contract test (auto-created, safe to delete).",
    "definition": {
        "nodes": [
            {"id": "node-start", "type": "start", "label": "Start", "data": {}, "position": {"x": 0, "y": 0}},
            {"id": "node-fn", "type": "task_function", "label": "Compute",
             "data": {"expression": "40 + 2", "output_as": "answer"}, "position": {"x": 0, "y": 160}},
            {"id": "node-end", "type": "end", "label": "End", "data": {}, "position": {"x": 0, "y": 320}},
        ],
        "edges": [
            {"id": "e1", "source": "node-start", "target": "node-fn"},
            {"id": "e2", "source": "node-fn", "target": "node-end"},
        ],
        "settings": {"autoLayout": True},
    },
}

TERMINAL = {"complete", "error", "cancelled"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:5050")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    args = ap.parse_args()
    base = args.base_url.rstrip("/") + "/api/v1"

    # ---- Authenticate (JWT) -------------------------------------------------
    section("Authentication")
    r = requests.post(f"{base}/auth/login",
                      json={"username": args.user, "password": args.password}, timeout=30)
    check("login returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        print("\nFATAL: cannot authenticate — is the server running with these creds?")
        return _finish()
    jwt = r.json().get("token")
    check("login returns a token", bool(jwt))
    H = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

    # ---- Access token works on flow endpoints (the doc's central auth claim) ----
    section("Access-token auth (doc claim: access tokens work on flow endpoints)")
    rt = requests.post(f"{base}/auth/tokens", headers=H,
                       json={"name": f"flow-contract-{uuid.uuid4().hex[:6]}", "expires_in_days": 1}, timeout=30)
    access_token = None
    if rt.status_code in (200, 201) and rt.json().get("token"):
        access_token = rt.json()["token"]
        check("created an access token (tda_...)", access_token.startswith("tda_"),
              f"prefix={access_token[:6]}")
    else:
        check("created an access token", False, f"got {rt.status_code}: {rt.text[:160]}")

    # ---- Setup: create the minimal flow ------------------------------------
    section("Setup: create minimal executable flow")
    r = requests.post(f"{base}/flows", headers=H, json=MINIMAL_FLOW, timeout=30)
    check("create flow returns 201", r.status_code == 201, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 201:
        print("\nFATAL: could not create the test flow.")
        return _finish()
    flow_id = r.json().get("flow_id")
    check("flow_id has 'flow-' prefix", bool(flow_id) and flow_id.startswith("flow-"), f"flow_id={flow_id}")

    # Prove the access token actually authenticates a flow endpoint.
    if access_token:
        ra = requests.get(f"{base}/flows/{flow_id}/runs",
                          headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        check("access token authenticates GET /flows/{id}/runs", ra.status_code == 200,
              f"got {ra.status_code}")

    try:
        _run_contract(base, H, jwt, flow_id)
        _run_session_binding(base, H, flow_id)
        _run_flow_run_turn(base, H, flow_id)
    finally:
        # ---- Cleanup --------------------------------------------------------
        section("Cleanup")
        # The sessions this test made — every session a run bound to, and the one it
        # passed as caller — go with the flow. Left behind they sit in the user's sidebar
        # pointing at a deleted flow (a reload then 404s on the run and the flow).
        sids = set(_CREATED_SESSIONS)
        try:
            rr = requests.get(f"{base}/flows/{flow_id}/runs", headers=H, params={"limit": 100}, timeout=30)
            for run in (rr.json().get("runs", []) if rr.status_code == 200 else []):
                if run.get("session_id"): sids.add(run["session_id"])
        except Exception:
            pass
        ui_root = base[:-len("/api/v1")]
        removed = [sid for sid in sids
                   if requests.delete(f"{ui_root}/api/session/{sid}", headers=H, timeout=30).status_code == 200]
        check("the test's sessions are removed with it", len(removed) == len(sids), f"{len(removed)} of {len(sids)}")
        rd = requests.delete(f"{base}/flows/{flow_id}", headers=H, timeout=30)
        check("delete test flow returns 200", rd.status_code == 200, f"got {rd.status_code}")

    return _finish()


def _run_contract(base, H, jwt, flow_id):
    # ---- Auth enforcement ---------------------------------------------------
    section("§3.32 Auth enforcement")
    r = requests.get(f"{base}/flows/{flow_id}/runs", timeout=30)  # no Authorization
    check("missing token -> 401", r.status_code == 401, f"got {r.status_code}")

    # ---- 3.32.1 Execute -----------------------------------------------------
    section("§3.32.1 Execute a Flow")
    r = requests.post(f"{base}/flows/{flow_id}/execute", headers=H,
                      json={"input_params": {"region": "EMEA"}}, timeout=30)
    check("execute returns 202", r.status_code == 202, f"got {r.status_code}: {r.text[:200]}")
    body = r.json() if r.status_code == 202 else {}
    run_id = body.get("run_id")
    check("execute returns run_id with 'frun-' prefix", bool(run_id) and run_id.startswith("frun-"),
          f"run_id={run_id}")
    check("execute returns status 'queued'", body.get("status") == "queued", f"status={body.get('status')}")
    # Phase 2: Task-contract parity — status_url present and points at the run.
    check("execute returns status_url (Task-contract parity)",
          body.get("status_url") == f"/api/v1/flows/runs/{run_id}", f"status_url={body.get('status_url')}")

    # ---- 3.32.5 SSE stream (subscribe AFTER execute -> exercises replay) ----
    section("§3.32.5 SSE stream (envelope, replay, terminal close, heartbeat-safe)")
    seen_types = []
    envelope_ok = True
    stamped_ok = True
    terminal_seen = False
    try:
        with requests.get(f"{base}/flows/runs/{run_id}/stream", headers={"Authorization": f"Bearer {jwt}"},
                          stream=True, timeout=40) as s:
            check("stream returns 200 text/event-stream", s.status_code == 200
                  and "text/event-stream" in s.headers.get("Content-Type", ""),
                  f"{s.status_code} / {s.headers.get('Content-Type')}")
            deadline = time.time() + 35
            for raw in s.iter_lines(decode_unicode=True):
                if time.time() > deadline:
                    break
                if not raw:
                    continue
                if raw.startswith(":"):
                    continue  # heartbeat comment — must be tolerated
                if not raw.startswith("data:"):
                    continue
                try:
                    evt = json.loads(raw[len("data:"):].strip())
                except Exception:
                    envelope_ok = False
                    continue
                if "type" not in evt or "payload" not in evt:
                    envelope_ok = False
                    continue
                seen_types.append(evt["type"])
                pl = evt.get("payload") or {}
                if pl.get("flow_id") != flow_id or pl.get("run_id") != run_id:
                    stamped_ok = False
                if evt["type"] in ("flow_run_complete", "flow_run_error"):
                    terminal_seen = True
                    break
    except Exception as exc:
        check("SSE stream readable", False, f"exception: {exc}")

    check("SSE events use {type, payload} envelope", envelope_ok and bool(seen_types),
          f"types={seen_types[:8]}")
    check("SSE payloads stamped with flow_id + run_id", stamped_ok)
    check("SSE delivered flow_run_start (via replay or live)", "flow_run_start" in seen_types,
          f"types={seen_types[:8]}")
    check("SSE reached a terminal event and closed", terminal_seen, f"types={seen_types[:8]}")

    # ---- 3.32.3 Get run (poll to terminal, validate full object) ------------
    section("§3.32.3 Get a Run (status + per-node trace)")
    run = _poll_until_terminal(base, H, run_id)
    check("run reached a terminal status", run.get("status") in TERMINAL, f"status={run.get('status')}")
    for f in ("run_id", "flow_id", "user_id", "status", "input_params", "output_result",
              "execution_trace", "tokens_input", "tokens_output", "cost_usd",
              "started_at", "completed_at", "created_at", "node_runs"):
        check(f"run object has documented field '{f}'", f in run)
    check("input_params echoed back", (run.get("input_params") or {}).get("region") == "EMEA",
          f"input_params={run.get('input_params')}")
    # Phase 2: additive Task-Object-compatible aliases (originals must still be present too).
    check("run has Task alias 'last_updated'", "last_updated" in run)
    check("run has Task alias 'result'", "result" in run)
    check("alias 'result' equals output_result", run.get("result") == run.get("output_result"))
    check("alias 'last_updated' is the latest lifecycle ts",
          run.get("last_updated") == (run.get("completed_at") or run.get("started_at") or run.get("created_at")),
          f"last_updated={run.get('last_updated')}")
    check("native fields retained alongside aliases (completed_at, output_result)",
          "completed_at" in run and "output_result" in run)
    nrs = run.get("node_runs") or []
    check("node_runs is a non-empty list", isinstance(nrs, list) and len(nrs) >= 1, f"len={len(nrs)}")
    if nrs:
        for f in ("id", "run_id", "node_id", "node_type", "scope_id", "iteration", "status",
                  "input_state", "output_state", "error_msg", "task_id", "session_id",
                  "tokens_input", "tokens_output", "started_at", "completed_at"):
            check(f"node_run has documented field '{f}'", f in nrs[0])
        statuses = {n.get("status") for n in nrs}
        check("node_run statuses ⊆ {success,error,cancelled,skipped}",
              statuses <= {"success", "error", "cancelled", "skipped"}, f"statuses={statuses}")
        fn = next((n for n in nrs if n.get("node_id") == "node-fn"), None)
        check("function node executed successfully", fn is not None and fn.get("status") == "success",
              f"fn={fn}")

    # ---- 3.32.2 List runs ---------------------------------------------------
    section("§3.32.2 List Runs for a Flow")
    r = requests.get(f"{base}/flows/{flow_id}/runs", headers=H, timeout=30)
    check("list runs returns 200", r.status_code == 200, f"got {r.status_code}")
    lb = r.json() if r.status_code == 200 else {}
    check("list runs has {runs, count}", "runs" in lb and "count" in lb)
    check("list runs count matches len(runs)", lb.get("count") == len(lb.get("runs", [])))
    check("our run is present in the list", any(x.get("run_id") == run_id for x in lb.get("runs", [])))

    # ---- 3.32.4 Node-data ---------------------------------------------------
    section("§3.32.4 Get Latest Per-Node Data")
    r = requests.get(f"{base}/flows/{flow_id}/node-data", headers=H, timeout=30)
    check("node-data returns 200", r.status_code == 200, f"got {r.status_code}")
    nd = r.json() if r.status_code == 200 else {}
    check("node-data has {run_id, status, node_data}",
          all(k in nd for k in ("run_id", "status", "node_data")))
    check("node-data keyed by node_id (node-fn present)", "node-fn" in (nd.get("node_data") or {}),
          f"keys={list((nd.get('node_data') or {}).keys())}")

    # ---- 3.32.6 Cancel (settled run -> signalled false, status flips) -------
    section("§3.32.6 Cancel a Run")
    r = requests.post(f"{base}/flows/runs/{run_id}/cancel", headers=H, timeout=30)
    check("cancel returns 200", r.status_code == 200, f"got {r.status_code}")
    cb = r.json() if r.status_code == 200 else {}
    check("cancel returns {cancelled, run_id, signalled}",
          all(k in cb for k in ("cancelled", "run_id", "signalled")))
    check("cancel of an already-settled run reports signalled=false",
          cb.get("signalled") is False, f"signalled={cb.get('signalled')}")

    # ---- 404 semantics (ownership/unknown returns 404, not 403/500) ---------
    section("§3.32 Not-found semantics")
    r = requests.get(f"{base}/flows/runs/frun-doesnotexist00", headers=H, timeout=30)
    check("unknown run_id -> 404", r.status_code == 404, f"got {r.status_code}")
    r = requests.get(f"{base}/flows/runs/frun-doesnotexist00/stream",
                     headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
    check("unknown run_id stream -> 404", r.status_code == 404, f"got {r.status_code}")


def _run_session_binding(base, H, flow_id):
    """Phase 3: every run binds to a session — reuse caller's, else auto-provision."""
    section("§3.32 Session binding (Phase 3)")

    # (1) No session_id -> a session is auto-provisioned, returned, stamped, and real.
    r = requests.post(f"{base}/flows/{flow_id}/execute", headers=H, json={}, timeout=30)
    check("execute (no session) returns 202", r.status_code == 202, f"got {r.status_code}")
    eb = r.json() if r.status_code == 202 else {}
    auto_sid = eb.get("session_id")
    run_id = eb.get("run_id")
    check("execute (no session) auto-provisions session_id in response",
          bool(auto_sid) and isinstance(auto_sid, str), f"session_id={auto_sid}")
    if auto_sid:
        # Monitorability criterion: the auto-provisioned session is real and appears in
        # the session list (i.e. the sidebar). NOTE: there is no singular
        # GET /api/v1/sessions/<id> REST route (only the list, and the UI's
        # /api/session/<id>), so we verify presence via the list, not a singular GET.
        rl = requests.get(f"{base}/sessions", headers=H, timeout=30)
        sessions = rl.json().get("sessions", []) if rl.status_code == 200 else []
        ids = {s.get("id") or s.get("session_id") for s in sessions}
        check("auto-provisioned session appears in the session list (sidebar)",
              auto_sid in ids, f"session list has {len(ids)} entries; auto_sid present={auto_sid in ids}")
    # The run record is stamped with the same session_id.
    run = _poll_until_terminal(base, H, run_id)
    check("run record is stamped with the bound session_id", run.get("session_id") == auto_sid,
          f"run.session_id={run.get('session_id')} vs {auto_sid}")

    # (2) With a session_id -> it is reused verbatim (no new session).
    rc = requests.post(f"{base}/sessions", headers=H, json={}, timeout=30)
    provided_sid = rc.json().get("session_id") if rc.status_code in (200, 201) else None
    if provided_sid: _CREATED_SESSIONS.append(provided_sid)
    check("created a session to pass as caller", bool(provided_sid), f"got {rc.status_code}")
    if provided_sid:
        r = requests.post(f"{base}/flows/{flow_id}/execute", headers=H,
                          json={"session_id": provided_sid}, timeout=30)
        check("execute (with session) returns 202", r.status_code == 202, f"got {r.status_code}")
        eb2 = r.json() if r.status_code == 202 else {}
        check("provided session_id is reused verbatim in response",
              eb2.get("session_id") == provided_sid,
              f"got {eb2.get('session_id')} expected {provided_sid}")
        run2 = _poll_until_terminal(base, H, eb2.get("run_id"))
        check("run record reuses the provided session_id", run2.get("session_id") == provided_sid,
              f"run.session_id={run2.get('session_id')}")


def _run_flow_run_turn(base, H, flow_id):
    """Phase 4a: a headless run writes a display-only cohesive flow-run turn into its
    bound session — reload-able, cost-correct (no double-count), with an LLM pointer."""
    section("§3.32 Cohesive flow-run turn (Phase 4a)")
    # The UI session-detail + turn endpoints live under /api/... (not /api/v1).
    root = base.rsplit("/v1", 1)[0]  # -> http://host/api

    r = requests.post(f"{base}/flows/{flow_id}/execute", headers=H, json={}, timeout=30)
    eb = r.json() if r.status_code == 202 else {}
    sid, run_id = eb.get("session_id"), eb.get("run_id")
    check("execute (no session) bound a session for the run", bool(sid) and bool(run_id),
          f"session_id={sid} run_id={run_id}")
    _poll_until_terminal(base, H, run_id)

    # The cohesive run turn is written just AFTER the run reaches terminal (in the
    # executor's finally), so poll the turn endpoint until it appears. The tool-only
    # flow has no LLM nodes, so the run turn is the ONLY turn (workflow_history index 1).
    td, status = {}, 0
    deadline = time.time() + 15
    while time.time() < deadline:
        rt = requests.get(f"{root}/session/{sid}/turn/1/details", headers=H, timeout=30)
        status = rt.status_code
        if status == 200:
            td = rt.json()
            break
        time.sleep(1.0)
    check("flow-run turn is reload-able (turn/1/details -> 200)", status == 200, f"got {status}")
    check("turn marked profile_type='flow'", td.get("profile_type") == "flow",
          f"profile_type={td.get('profile_type')}")
    check("turn carries origin='flow'", td.get("origin") == "flow", f"origin={td.get('origin')}")
    # A REST-triggered run turn must ALSO carry the transport so the chat log shows
    # "Rest Call" alongside "Flow" — differentiating REST-run flows from conversation-run.
    check("turn carries source='rest' (Rest Call badge, differentiates from conversation)",
          td.get("source") == "rest", f"source={td.get('source')}")
    check("turn links flow_id", td.get("flow_id") == flow_id, f"flow_id={td.get('flow_id')}")
    check("turn links flow_run_id", td.get("flow_run_id") == run_id, f"flow_run_id={td.get('flow_run_id')}")
    check("turn has display field flow_total_cost", "flow_total_cost" in td)
    # Cost integrity: the summary turn is display-only — turn_cost is 0 and it adds NO
    # tokens to the session total (tool-only flow => session totals stay 0 => no double-count).
    check("summary turn_cost is 0 (display-only, no double-count)", td.get("turn_cost") == 0.0,
          f"turn_cost={td.get('turn_cost')}")
    check("session total input tokens unchanged by the turn (==0 for tool-only flow)",
          td.get("session_input_tokens") == 0, f"session_input_tokens={td.get('session_input_tokens')}")
    check("session total output tokens unchanged by the turn (==0 for tool-only flow)",
          td.get("session_output_tokens") == 0, f"session_output_tokens={td.get('session_output_tokens')}")
    # LLM retrieval means: the summary carries the compact get_run pointer with the run_id.
    summ = td.get("final_summary") or ""
    check("turn summary is the compact LLM pointer (references get_run + run_id)",
          "get_run" in summ and run_id in summ, f"summary={summ[:120]!r}")


def _poll_until_terminal(base, H, run_id, timeout_s=60):
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        r = requests.get(f"{base}/flows/runs/{run_id}", headers=H, timeout=30)
        if r.status_code == 200:
            last = r.json()
            if last.get("status") in TERMINAL:
                return last
        time.sleep(1.5)
    return last


def _finish():
    print("\n" + "=" * 60)
    print(f"RESULT: {_PASS} passed, {_FAIL} failed")
    if _FAILURES:
        print("\nFailures:")
        for f in _FAILURES:
            print(f"  - {f}")
    print("=" * 60)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
