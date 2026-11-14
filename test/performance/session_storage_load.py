"""Session-storage load harness (R1.5).

Measures the throughput and latency of the session WRITE path — the
load-modify-save transaction that R1.1–R1.4 hardened — under concurrency, for
each storage backend (file vs Postgres). Each "turn" runs a real
``session_manager._session_transaction`` that appends to ``workflow_history``
and bumps token counters: the file backend re-serializes the whole (growing)
session body per turn; the Postgres backend does a single-row JSONB upsert.

This deliberately exercises the storage tier directly (not the full HTTP/LLM
stack), so the numbers isolate what R1 changed and are reproducible with no
external services.

Run:
    PYTHONPATH=src python test/performance/session_storage_load.py
    PYTHONPATH=src python test/performance/session_storage_load.py --sessions 80 --turns 40 --rows 20
"""
import os
import sys
import time
import asyncio
import argparse
import tempfile
import shutil
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _pct(values, q):
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((q / 100.0) * (len(s) - 1))))
    return s[idx]


def _make_turn(n, rows):
    return {
        "turn": n, "turn_number": n, "isValid": True,
        "user_query": f"load query turn {n} " + ("x" * 40),
        "final_summary": "ok " + ("y" * 60),
        "turn_input_tokens": 1200, "turn_output_tokens": 300,
        "provider": "loadprov", "model": "loadmodel",
        "execution_trace": [{
            "action": {"tool_name": "base_readQuery",
                       "arguments": {"sql": "SELECT * FROM t WHERE k=" + str(n)}},
            "result": {"tool_output_summary": {
                "status": "success",
                "results": [{"c0": i, "c1": f"v{i}", "c2": i * 1.5} for i in range(rows)],
            }},
        }],
    }


def _make_body(sid, user):
    return {
        "id": sid, "user_uuid": user, "name": f"load-{sid}",
        "created_at": "2026-06-21T00:00:00", "last_updated": "2026-06-21T00:00:00",
        "profile_id": "pload", "profile_tag": "LOAD", "profile_type": "tool_enabled",
        "provider": "loadprov", "model": "loadmodel",
        "input_tokens": 0, "output_tokens": 0,
        "models_used": [], "profile_tags_used": ["LOAD"], "genie_metadata": {},
        "last_turn_data": {"workflow_history": []},
        "session_history": [], "chat_object": [],
    }


async def _run_session(sm, user, sid, turns, rows, latencies):
    await sm.save_session(user, sid, _make_body(sid, user))
    for n in range(turns):
        t0 = time.perf_counter()
        async with sm._session_transaction(user, sid) as data:
            if data is None:
                continue
            wf = data.setdefault("last_turn_data", {}).setdefault("workflow_history", [])
            wf.append(_make_turn(n, rows))
            data["input_tokens"] = data.get("input_tokens", 0) + 1200
            data["output_tokens"] = data.get("output_tokens", 0) + 300
        latencies.append(time.perf_counter() - t0)


async def _run_backend_async(sm, sessions, turns, rows, user):
    latencies = []
    t0 = time.perf_counter()
    await asyncio.gather(*[
        _run_session(sm, user, f"s{i:05d}", turns, rows, latencies)
        for i in range(sessions)
    ])
    wall = time.perf_counter() - t0
    # Drain the fire-and-forget summary-index upserts before the loop closes
    # (keeps teardown clean and the index consistent). Not counted in `wall`.
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return latencies, wall


def run_backend(backend, sessions, turns, rows):
    """Set up the backend, run the load, return a stats dict. Fresh process state
    per call via reset_session_backend + a fresh temp dir / pg instance."""
    pgdata = None
    pgserver_obj = None
    tmpdir = None
    if backend == "postgres":
        import pgserver
        pgdata = tempfile.mkdtemp(prefix="tda_pgload_")
        pgserver_obj = pgserver.get_server(pgdata)
        os.environ["TDA_SESSION_BACKEND"] = "postgres"
        os.environ["TDA_SESSION_PG_DSN"] = pgserver_obj.get_uri()
    else:
        tmpdir = tempfile.mkdtemp(prefix="tda_fileload_")
        os.environ["TDA_SESSION_BACKEND"] = "file"
        os.environ["TDA_SESSIONS_DIR"] = tmpdir

    from trusted_data_agent.core import session_manager as sm
    from trusted_data_agent.core.session_backends import reset_session_backend

    loop = asyncio.new_event_loop()
    try:
        reset_session_backend(None)
        loop.run_until_complete(sm._init_session_index())
        latencies, wall = loop.run_until_complete(
            _run_backend_async(sm, sessions, turns, rows, user=f"loaduser_{backend}"))
    finally:
        try:
            loop.run_until_complete(
                __import__("trusted_data_agent.core.session_backends",
                           fromlist=["close_session_backend"]).close_session_backend())
        except Exception:
            pass
        loop.close()
        if pgserver_obj is not None:
            try:
                pgserver_obj.cleanup()
            except Exception:
                pass
        if pgdata:
            shutil.rmtree(pgdata, ignore_errors=True)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    total = len(latencies)
    ms = [x * 1000 for x in latencies]
    return {
        "backend": backend,
        "writes": total,
        "wall_s": round(wall, 3),
        "throughput_wps": round(total / wall, 1) if wall else 0,
        "p50_ms": round(_pct(ms, 50), 2),
        "p95_ms": round(_pct(ms, 95), 2),
        "p99_ms": round(_pct(ms, 99), 2),
        "max_ms": round(max(ms), 2) if ms else 0,
        "mean_ms": round(statistics.mean(ms), 2) if ms else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=int(os.environ.get("LOAD_SESSIONS", "50")))
    ap.add_argument("--turns", type=int, default=int(os.environ.get("LOAD_TURNS", "30")))
    ap.add_argument("--rows", type=int, default=int(os.environ.get("LOAD_ROWS", "20")))
    ap.add_argument("--backends", default="file,postgres")
    args = ap.parse_args()

    print(f"== session-storage load: {args.sessions} sessions x {args.turns} turns "
          f"({args.sessions * args.turns} writes), {args.rows} rows/turn ==\n")

    results = []
    for backend in args.backends.split(","):
        backend = backend.strip()
        if backend == "postgres":
            try:
                import pgserver  # noqa: F401
            except Exception:
                print(f"[skip] postgres: pgserver not installed\n")
                continue
        print(f"[run] {backend} ...")
        results.append(run_backend(backend, args.sessions, args.turns, args.rows))

    hdr = f"{'backend':<10} {'writes':>7} {'wall(s)':>8} {'writes/s':>9} {'p50ms':>7} {'p95ms':>7} {'p99ms':>7} {'max ms':>8}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in results:
        print(f"{r['backend']:<10} {r['writes']:>7} {r['wall_s']:>8} {r['throughput_wps']:>9} "
              f"{r['p50_ms']:>7} {r['p95_ms']:>7} {r['p99_ms']:>7} {r['max_ms']:>8}")
    print()
    return results


if __name__ == "__main__":
    main()
