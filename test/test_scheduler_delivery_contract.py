#!/usr/bin/env python3
"""What TDA_Scheduler advertises is what the scheduler runs and delivers — two silent no-ops.

Found by the Oct-2026 documentation audit:

1. ``interval:1d`` is advertised (instructions.json, scheduler-expert, CLAUDE.md) and the
   handler's preview parser accepts it, so the task card listed next runs — but the engine's
   ``task_scheduler._parse_interval`` had no ``d`` unit, so ``int("1d")`` raised at register
   time and the task never fired.
2. The manifest told the LLM ``output_config`` ``{to}`` / ``{url, token}`` and the Jobs form
   saved a webhook as ``{url}``, while ``_deliver_result`` read only ``to_address`` /
   ``webhook_url`` / ``bearer_token`` — the task ran and delivered nothing. The delivery seam
   now normalises every spelling (``_delivery_config``), which also heals stored tasks, and the
   writers use the canonical names.

  §I  the engine parser accepts every unit the handler's preview parser does, with equal values
  §D  ``_delivery_config``: aliases map to canonical keys, canonical keys win, nothing invented
  §S  ``_deliver_result`` reads through ``_delivery_config``; email/webhook really deliver an
      alias-keyed config (EmailService and httpx stubbed)
  §W  the writers: the manifest names the canonical keys; the Jobs form saves ``webhook_url``

Run:  PYTHONPATH=src:. python test/test_scheduler_delivery_contract.py
"""
import asyncio
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


from trusted_data_agent.core import task_scheduler as TS  # noqa: E402
from components.builtin.scheduler.handler import _parse_interval_seconds  # noqa: E402

# §I ─────────────────────────────────────────────────────────────────────────────────
for spec, secs in (("1d", 86400), ("2d", 172800), ("30m", 1800), ("2h", 7200), ("300s", 300), ("45", 45), (" 1D ", 86400)):
    try:
        got = TS._parse_interval(spec)
    except Exception as e:  # noqa: BLE001
        got = f"raised {e!r}"
    check(f"engine parses interval:{spec.strip()} = {secs}s", got == secs, str(got))
    check(f"engine and preview agree on interval:{spec.strip()}", got == _parse_interval_seconds(spec))

# §D ─────────────────────────────────────────────────────────────────────────────────
D = TS._delivery_config
check("{to} → to_address", D({"to": "a@x.io"}).get("to_address") == "a@x.io")
check("{url, token} → webhook_url + bearer_token",
      D({"url": "https://h/x", "token": "t"}) | {} == {"url": "https://h/x", "token": "t",
                                                       "webhook_url": "https://h/x", "bearer_token": "t"})
check("a canonical key wins over its alias",
      D({"to_address": "real@x.io", "to": "alias@x.io"})["to_address"] == "real@x.io")
check("nothing is invented for an empty config", D({}) == {} and D(None) == {})
check("unrelated keys survive (keep_in_files)", D({"to": "a@x.io", "keep_in_files": True}).get("keep_in_files") is True)

# §S ─────────────────────────────────────────────────────────────────────────────────
import inspect  # noqa: E402
src = inspect.getsource(TS._deliver_result)
check("_deliver_result reads output_config through _delivery_config", "_delivery_config(" in src)

sent = []


class _Email:
    @staticmethod
    async def send_email(to, subject, body):
        sent.append(("email", to))


posted = []


class _Resp:
    status_code = 200
    def raise_for_status(self): return None


class _Client:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, json=None, headers=None, **k):
        posted.append((url, (headers or {}).get("Authorization")))
        return _Resp()


import trusted_data_agent.auth.email_service as _es  # noqa: E402
import httpx  # noqa: E402
_real_es, _real_client = _es.EmailService, httpx.AsyncClient
try:
    _es.EmailService = _Email
    httpx.AsyncClient = _Client
    asyncio.run(TS._deliver_result({"id": "t1", "name": "n", "output_channel": "email",
                                    "output_config": json.dumps({"to": "a@x.io"})}, "result", "s1"))
    check("an email task written {to} is delivered", sent == [("email", "a@x.io")], str(sent))
    asyncio.run(TS._deliver_result({"id": "t2", "name": "n", "output_channel": "webhook",
                                    "output_config": json.dumps({"url": "https://h/x", "token": "tok"})}, "result", "s1"))
    check("a webhook task written {url, token} is delivered with its bearer",
          posted == [("https://h/x", "Bearer tok")], str(posted))
finally:
    _es.EmailService, httpx.AsyncClient = _real_es, _real_client

# §W ─────────────────────────────────────────────────────────────────────────────────
manifest = json.load(open(os.path.join(ROOT, "components/builtin/scheduler/manifest.json"), encoding="utf-8"))
blob = json.dumps(manifest)
check("the manifest tells the LLM the canonical keys",
      "'to_address'" in blob and "'webhook_url'" in blob and "'bearer_token'" in blob
      and "{'to': " not in blob and "'token': " not in blob)
jobs = open(os.path.join(ROOT, "static/js/handlers/jobsHandler.js"), encoding="utf-8").read()
check("the Jobs form saves a webhook as webhook_url",
      "JSON.stringify({ webhook_url: chanVal })" in jobs and "JSON.stringify({ url: chanVal })" not in jobs)

total = PASS + FAIL
print(f"{'FAILED' if FAIL else 'ALL PASS'} — {total} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
