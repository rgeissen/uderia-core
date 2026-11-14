"""The two per-request answer streams send a keepalive while a tool call runs.

Why this gate exists (2026-09-03): with the full NIBRS extract loaded, a single SQL
can take minutes. `/ask_stream` produced no bytes until the warehouse returned, the
nginx in front of production dropped the idle connection, and the browser showed a
"connection timeout" while the backend kept working — nothing in the session trace
recorded it, because nothing had failed server-side. The notification stream had
pinged every 20 s since it was written; the answer streams never did.

CI-pure: drives the pump over a queue, no app, no network.

Run: python3 test/test_sse_keepalive.py
"""
import asyncio
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


# Imported ONCE, up front: importing the routes module is slow (it pulls the whole
# app), and doing it inside the consumer task blocked the event loop for longer than
# the silence window — the pump then saw the queued items immediately and the
# "silence" assertion failed for a reason that had nothing to do with the pump.
from trusted_data_agent.api import routes as _routes  # noqa: E402


def _pump():
    return _routes._pump_with_keepalive


async def t_silence_is_pinged():
    q = asyncio.Queue()
    out = []

    async def consume():
        async for item in _pump()(q, interval=0.05):
            out.append(item)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.23)          # ~4 intervals of silence
    await q.put("data: x\n\n")
    await q.put(None)
    await asyncio.wait_for(task, 2)
    pings = [x for x in out if x == ": ping\n\n"]
    check("silence produces keepalive comments", 3 <= len(pings) <= 6, f"{len(pings)} pings: {out}")
    check("the real event still arrives, after the pings", out[-1] == "data: x\n\n", out[-2:])
    check("a ping is an SSE comment line (ignored by consumers)", all(p.startswith(":") for p in pings), pings[:1])


async def t_busy_stream_is_not_pinged():
    q = asyncio.Queue()
    for i in range(5):
        await q.put(f"data: {i}\n\n")
    await q.put(None)
    out = [x async for x in _pump()(q, interval=0.5)]
    check("a stream with events waiting never pings", not any(x.startswith(":") for x in out), out)
    check("order preserved and sentinel consumed", out == [f"data: {i}\n\n" for i in range(5)], out)


def t_both_answer_streams_use_it():
    src = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "routes.py")).read()
    uses = len(re.findall(r"async for item in _pump_with_keepalive\(queue\)", src))
    check("ask_stream and invoke_prompt_stream both pump through the keepalive", uses == 2, uses)
    check("no answer stream still blocks on a bare queue.get()",
          "item = await queue.get()" not in src, "bare get survives")
    check("the interval is declared once", src.count("SSE_KEEPALIVE_SECONDS = ") == 1, "")


async def main_async():
    await t_silence_is_pinged()
    await t_busy_stream_is_not_pinged()


for fn in (lambda: asyncio.run(main_async()), t_both_answer_streams_use_it):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{getattr(fn, '__name__', 'async')} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
