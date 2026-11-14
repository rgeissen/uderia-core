#!/usr/bin/env python3
"""The Insight Builder — dashboards, canvases and the data product catalog described for agents.

  §A  the descriptors — reads; creating, arranging, refreshing, renaming, restoring and removing
      are writes; deleting is always reviewed; compiling a data product request is a read (SQL
      back, nothing runs)
  §B  what is NOT on offer — a tile's content, question and answering agent; a dashboard's
      auto-refresh; a canvas's content: refused by bind. And, catalog-wide: no write an agent can
      call takes raw markup or a raw graph (`html`, `spec`, `content`, `definition`) — content
      that renders in the user's page is written only by its own component, so a prompt can
      never plant script through the platform
  §C  data stays the agent's own access — running a data product query, running canvas SQL and
      canvas connections (credentials) are not offered at all
  §D  the role and the Guide

No server, no LLM, no writes. Run: python test/test_platform_insight.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "platform-insight-test")
logging.disable(logging.CRITICAL)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


from trusted_data_agent.main import create_app  # noqa: E402
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
from trusted_data_agent.core import platform_roles as R  # noqa: E402

cat = build_catalog(create_app())
by = cat.by_op

# ── §A descriptors ──────────────────────────────────────────────────────────────
print("§A descriptors")
EFFECTS = {
    "dashboards.list": "read", "dashboards.get": "read", "dashboards.tiles": "read",
    "dashboards.create": "write", "dashboards.update": "write", "dashboards.delete": "write",
    "dashboards.tile_update": "write", "dashboards.tile_delete": "write", "dashboards.tile_refresh": "write",
    "dashboards.layout": "write",
    "canvases.list": "read", "canvases.get": "read", "canvases.versions": "read",
    "canvases.update": "write", "canvases.delete": "write", "canvases.version_restore": "write",
    "data_products.catalog": "read", "data_products.get": "read", "data_products.compile": "read",
}
for op_id, eff in EFFECTS.items():
    check(f"{op_id} is described as {eff}", op_id in by and by[op_id].effect == eff, by.get(op_id) and by[op_id].effect)
for op_id in ("dashboards.delete", "dashboards.tile_delete", "canvases.delete"):
    check(f"{op_id} always needs a person (it deletes)", P.always_reviewed(by[op_id]) == "it deletes")
check("refreshing a tile follows the agent's write mode (it spends; its own agent answers)",
      P.always_reviewed(by["dashboards.tile_refresh"]) is None and "spends" in by["dashboards.tile_refresh"].notes)
check("every insight operation is the caller's own", all(by[o].scope == "own" for o in EFFECTS))
check("the data product catalog needs its graph (it answers nothing without one)",
      by["data_products.catalog"].query["kg_id"].required)

# ── §B not on offer ─────────────────────────────────────────────────────────────
print("§B not on offer")
NOT_OFFERED = {
    "dashboards.tile_update": ("html", "spec", "source_prompt", "profile_id", "component_id", "mcp_args_json"),
    "dashboards.tile_refresh": ("profile_id",),
    "dashboards.update": ("auto_refresh_s", "is_default"),
    "canvases.update": ("content", "sources", "language"),
}
for op_id, names in NOT_OFFERED.items():
    o = by[op_id]
    accepted = set(o.path_params) | set(o.query) | set(o.body)
    check(f"{op_id} does not take {names}", not (accepted & set(names)), sorted(accepted & set(names)))
    placed, err = P.bind(o, {**{k: "1" for k in o.path_params}, names[0]: "x"})
    check(f"bind refuses {op_id}({names[0]}=…)", placed is None and "does not take" in (err or ""), err)
RAW = {"html", "spec", "content", "definition"}
raw_writes = sorted(f"{o.op}({a})" for o in cat.operations if o.effect == "write"
                    for a in set(o.body) | set(o.query) if a in RAW)
check("no write an agent can call takes raw markup or a raw graph — catalog-wide", not raw_writes, raw_writes)
check("adding a tile is not offered (its body carries markup)", "dashboards.tile_add" not in by and not any(
    o.method == "POST" and o.path.endswith("/tiles") for o in cat.operations))
check("the direct tool replay of a tile is not offered (it runs outside an agent turn)",
      not any(o.path.endswith("/refresh-mcp") for o in cat.operations))

# ── §C data stays the agent's own access ────────────────────────────────────────
print("§C data")
for path in ("/api/v1/data-products/<product_name>/query", "/api/v1/canvas/execute",
             "/api/v1/canvas/connections", "/api/v1/canvas/connections/<connection_id>"):
    check(f"{path} is not offered to agents", not any(o.path == path for o in cat.operations))
check("compiling says so: it runs nothing, and running is the agent's own access",
      "nothing is run" in by["data_products.compile"].summary and "TDA_DataProduct" in by["data_products.compile"].notes)

# ── §D the role and the Guide ───────────────────────────────────────────────────
print("§D the role")
reg = R.registry()
ib, guide = reg.roles["insight-builder"], reg.roles["guide"]
check("the Insight Builder is active", ib.active and set(ib.effects) == {"read", "write"})
for op_id, eff in EFFECTS.items():
    check(f"the Insight Builder lists {op_id}", R.role_state([ib], op_id, eff) == (True, None))
ib_writes = sorted(o.op for o in cat.operations if o.effect == "write" and ib.lists(o.op))
check("its changes are dashboards and canvases only", all(w.split(".")[0] in ("dashboards", "canvases") for w in ib_writes), ib_writes)
for op_id in ("dashboards.list", "dashboards.tiles", "canvases.list", "data_products.catalog"):
    check(f"the Guide reads {op_id}", R.role_state([guide], op_id, "read") == (True, None))
check("the Guide changes no dashboard or canvas",
      not any(guide.lists(o.op) for o in cat.operations if o.effect == "write" and o.op.split(".")[0] in ("dashboards", "canvases")))
found = [f["op"] for f in P.find("refresh a tile on my dashboard", "user", cat=cat, roles=[ib], writes="review")["found"]]
check("find, scoped to the Insight Builder, finds the tile refresh among its first two", "dashboards.tile_refresh" in found[:2], found)

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
