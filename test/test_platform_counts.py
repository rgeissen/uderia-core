#!/usr/bin/env python3
"""The platform counts; the model does not.

FOUND (2026-11-01, live, twice): an agent asked which of the user's agents are signed read the
44 records `trust.agents` returned and answered "11 trusted, 36 unsigned, 48 agents" — the route
said 10, 33 and 1 changed. Tallying a long list by hand is not something a model does reliably,
and the playbook's example figures pulled it further off. A descriptor now names the keys to
count (`count_by`) and the platform answers with the total and the records per value — counted
over every record, before anything is summarised or shortened.

  §A  `count_records` — the one list of records in an answer; per value of each key; an answer
      with two lists is not guessed at
  §B  the descriptor — `count_by` is validated (key names; a read only)
  §C  the answer — counts survive summarising and `fields`; the note says to use them
  §D  the shipped operations — `trust.agents` by state and class, `profiles.list` by class;
      through the real route, the counts equal the records
  §E  the playbook reads the counts, and no longer carries example figures to copy

No LLM calls, no writes. Run: python test/test_platform_counts.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "platform-counts-test")
logging.disable(logging.CRITICAL)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


from trusted_data_agent.core import platform_operations as P  # noqa: E402
from trusted_data_agent.api import operations as O  # noqa: E402

# ── §A count_records ────────────────────────────────────────────────────────────
print("§A count_records")
rows = [{"state": "trusted"}, {"state": "unsigned"}, {"state": "unsigned"}, {"state": None}]
c = P.count_records({"agents": rows, "note": "x"}, ("state",))
check("the one list of records: total and per value, most first", c == {"total": 4, "state": {"unsigned": 2, "none": 1, "trusted": 1}}, c)
check("a bare list is counted too", P.count_records(rows, ("state",))["total"] == 4)
check("an empty list counts as none", P.count_records({"agents": []}, ("state",)) == {"total": 0, "state": {}})
check("two lists of records are not guessed at", P.count_records({"a": rows, "b": rows}, ("state",)) is None)
check("a list of plain values is not records", P.count_records({"ids": ["a", "b"]}, ("state",)) is None)

# ── §B the descriptor ───────────────────────────────────────────────────────────
print("§B the descriptor")
mk = lambda **k: O.Descriptor(op="x.y", effect=k.get("effect", "read"), scope="own", tier="user", summary="s",
                              count_by=k.get("count_by", ()))
check("a read may count", not O._check(mk(count_by=("state",)), "w"))
check("a write may not", any("only a read" in p for p in O._check(mk(effect="write", count_by=("state",)), "w")))
check("an empty key is refused", any("count_by" in p for p in O._check(mk(count_by=("",)), "w")))

# ── §C the answer ───────────────────────────────────────────────────────────────
print("§C the answer")


class _Op:
    op, summary_fields, count_by = "trust.agents", ("tag", "state"), ("state",)


big = {"agents": [{"tag": f"A{i}", "state": "trusted" if i % 4 == 0 else "unsigned", "blob": "x" * 400}
                  for i in range(80)]}
out = P._shape(_Op(), 200, big)
check("a summarised answer still carries the counts over EVERY record",
      out.get("summarised") and out["counts"] == {"total": 80, "state": {"unsigned": 60, "trusted": 20}}, out.get("counts"))
check("…with the note to use them", "never count the records yourself" in out.get("counts_note", ""))
out = P._shape(_Op(), 200, big, fields=["tag"])
check("asking for some fields keeps the counts", out["counts"]["total"] == 80)
check("a failed answer carries no counts", "counts" not in P._shape(_Op(), 500, {"error": "x"}))


class _NoCount:
    op, summary_fields, count_by = "x.y", (), ()


check("an operation that names no keys carries no counts", "counts" not in P._shape(_NoCount(), 200, big))

# ── §D the shipped operations ───────────────────────────────────────────────────
print("§D the shipped operations")
from trusted_data_agent.main import create_app  # noqa: E402
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
app = create_app()
cat = build_catalog(app)
check("trust.agents counts by state and class", cat.by_op["trust.agents"].count_by == ("state", "ifoc"))
check("profiles.list counts by class", cat.by_op["profiles.list"].count_by == ("profile_type",))
from trusted_data_agent.auth.database import get_db_session  # noqa: E402
from trusted_data_agent.auth.models import User  # noqa: E402
with get_db_session() as _s:
    _admin = next((u.id for u in _s.query(User).filter(User.is_active.is_(True)).all()
                   if (getattr(u, "profile_tier", "") or "").lower() == "admin"), None)
if _admin:
    async def _go():
        async with app.app_context():
            return await P.call("trust.agents", {}, _admin, app=app)
    r = asyncio.run(_go())
    recs = (r.get("result") or {}).get("agents") or []
    per = {}
    for a in recs:
        per[a.get("state")] = per.get(a.get("state"), 0) + 1
    check("through the real route, the counts are the records'",
          r.get("ok") and r["counts"]["total"] == len(recs) and r["counts"]["state"] == dict(
              sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))), (r.get("counts"), len(recs)))

# ── §E the playbook ─────────────────────────────────────────────────────────────
print("§E the playbook")
pb = (ROOT / "skills/builtin/platform-playbooks/library/trust/my-agents.md").read_text()
check("the trust overview reads the counts and never counts by hand",
      "`counts`" in pb and "never from counting the records yourself" in pb)
check("…and carries no example totals to copy", "Of your 36 agents" not in pb and "12 are trusted" not in pb)

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
