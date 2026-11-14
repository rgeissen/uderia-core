#!/usr/bin/env python3
"""
The server stops promptly with browser tabs open (Nov 2026).

A live notification stream (one per open tab) never finishes on its own, and hypercorn's
graceful stop waits for open responses — every restart with a tab open hung until it was
forced (kill -9). The fix: hypercorn's shutdown trigger (`main._stop_requested`) ends every
live stream FIRST (`main.close_live_streams` puts a sentinel on each per-user queue; the
stream's generator returns on it), then lets the graceful stop run with nothing left to wait
for; and the stream re-raises a cancellation instead of swallowing it.

Measured on the reproduction (a real server, real SSE clients, SIGTERM): before — still running
after 15 s; after — stopped in 1.3 s with one stream, 1.6 s with three, every client released.
This gate holds the mechanism deterministically (no server, no LLM).

Run: PYTHONPATH=src python3 test/test_server_stop.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("SECRET_KEY", "server-stop-test")

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:300]) if detail else ''}")


main_src = (ROOT / "src/trusted_data_agent/main.py").read_text()
routes_src = (ROOT / "src/trusted_data_agent/api/routes.py").read_text()

print("§A the server uses its own shutdown trigger")
check("hypercorn is given the trigger that ends the streams first",
      "hypercorn.asyncio.serve(app, config, shutdown_trigger=_stop_requested)" in main_src)
check("the trigger listens for SIGINT and SIGTERM", '("SIGINT", "SIGTERM")' in main_src and "add_signal_handler" in main_src)
check("it ends the live streams before the graceful stop runs",
      re.search(r"await stop\.wait\(\)\s+APP_STATE\[\"shutting_down\"\] = True\s+n = close_live_streams\(\)", main_src) is not None)

print("§B every live stream is told")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
from trusted_data_agent import main as M  # noqa: E402
logging.disable(logging.NOTSET)
from trusted_data_agent.core.config import APP_STATE  # noqa: E402


async def _told():
    a, b, c = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
    saved = APP_STATE.get("notification_queues")
    APP_STATE["notification_queues"] = {"u1": {a, b}, "u2": {c}}
    try:
        n = M.close_live_streams()
        return n, [q.get_nowait() for q in (a, b, c)]
    finally:
        APP_STATE["notification_queues"] = saved if saved is not None else {}
n, got = asyncio.run(_told())
check("one sentinel per open stream, across users", n == 3 and all(g == M.STREAM_CLOSE for g in got), (n, got))
saved = APP_STATE.get("notification_queues")
APP_STATE["notification_queues"] = {}
check("no open streams: nothing to tell, no error", M.close_live_streams() == 0)
APP_STATE["notification_queues"] = saved if saved is not None else {}

print("§C the stream ends on the sentinel, and never swallows a cancellation")
check("the stream returns on the sentinel the trigger sends",
      'notification.get("type") == "__stream_close__"' in routes_src and M.STREAM_CLOSE == {"type": "__stream_close__"})
block = routes_src[routes_src.index("async def notification_generator"):routes_src.index('return Response(notification_generator()')]
check("a cancellation is re-raised after cleanup (a swallowed one leaves asyncio's stop waiting)",
      re.search(r"except asyncio\.CancelledError:\s+app_logger\.debug\([^\n]+\)\s+raise", block) is not None)
check("the stream still removes its queue on the way out", "queues_for_user.remove(queue)" in block)

print(f"\n{'ALL PASS' if not failed else 'FAILED'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
