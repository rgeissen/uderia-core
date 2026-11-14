"""New + Rename from the split-panel switchers, and the canvas saving what the user types.

Backend half (CI-pure, temp SQLite — never the deployment DB):
  1. canvas store: autosave without a version, a version on leave, rename refused on a
     title another canvas holds
  2. canvas REST: mode "create" never upserts (409 on a taken title, may start empty);
     PUT validates a title and maps a collision to 409; record_version is honoured
  3. flow / dashboard PUT refuse an empty name
  4. kg_rename refuses a graph the caller does not own (404) before any write
  5. the four panels wire New / Rename through the one shared switcher
"""
import ast
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name} {detail}")


def src(p):
    return (ROOT / p).read_text()


# ── 1. canvas store ──────────────────────────────────────────────────────────
from trusted_data_agent.core import canvas_db as cdb  # noqa: E402

tmp = tempfile.mkdtemp()
cdb._DB_PATH = Path(tmp) / "canvas.db"
cdb._SCHEMA_APPLIED = False
cdb._ensure_schema()

U = "user-1"
a = cdb.create_canvas(U, "Report", "select 1", "sql")
b = cdb.create_canvas(U, "Other", "select 2", "sql")
check("create records v1", len(cdb.list_versions(a["canvas_id"], U)) == 1)

cdb.update_canvas(a["canvas_id"], U, content="select 10", record_version=False)
cdb.update_canvas(a["canvas_id"], U, content="select 100", record_version=False)
check("autosave stores the content", cdb.get_canvas(a["canvas_id"], U)["content"] == "select 100")
check("autosave records no version", len(cdb.list_versions(a["canvas_id"], U)) == 1)

cdb.update_canvas(a["canvas_id"], U, content="select 100", record_version=True)
vs = cdb.list_versions(a["canvas_id"], U)
check("leave records the autosaved content as a version (unchanged content)", len(vs) == 2,
      str([v.get("version_num") for v in vs]))
cdb.update_canvas(a["canvas_id"], U, content="select 100", record_version=True)
check("leaving again adds nothing (de-duplicated)", len(cdb.list_versions(a["canvas_id"], U)) == 2)

cdb.update_canvas(a["canvas_id"], U, content="select 7")
check("default PUT still versions a content change", len(cdb.list_versions(a["canvas_id"], U)) == 3)

try:
    cdb.update_canvas(a["canvas_id"], U, title="other")
    check("rename onto another canvas's title is refused", False)
except cdb.CanvasTitleTaken:
    check("rename onto another canvas's title is refused", True)
check("refused rename changed nothing", cdb.get_canvas(a["canvas_id"], U)["title"] == "Report")
r = cdb.update_canvas(a["canvas_id"], U, title="REPORT ")
check("renaming to its own title (case/space) is allowed", r and r["title"] == "REPORT ")
r = cdb.update_canvas(a["canvas_id"], U, title="Quarterly")
check("rename stores the new title", r["title"] == "Quarterly")
check("the new title is the upsert identity", cdb.get_canvas_by_title(U, "quarterly")["canvas_id"] == a["canvas_id"])
check("another user's same title is not a collision",
      cdb.update_canvas(cdb.create_canvas("user-2", "X", "s", "sql")["canvas_id"], "user-2", title="Other")["title"] == "Other")

# ── 2. canvas REST ───────────────────────────────────────────────────────────
rt = src("src/trusted_data_agent/api/canvas_routes.py")
check("POST mode create is handled", 'body.get("mode") == "create"' in rt)
m = re.search(r'if body\.get\("mode"\) == "create":(.*?)\n    if not content', rt, re.S)
blk = m.group(1) if m else ""
check("mode create never upserts", "upsert_canvas" not in blk and "create_canvas(" in blk)
check("mode create refuses a taken title with 409", "get_canvas_by_title" in blk and "409" in blk)
check("mode create may start empty", 'content or ""' in blk)
check("PUT honours record_version", 'record_version=body.get("record_version", True) is not False' in rt)
check("PUT maps a title collision to 409", "except CanvasTitleTaken" in rt and '"title_taken"' in rt)

# ── 3. flow / dashboard names ────────────────────────────────────────────────
check("flow PUT refuses an empty name", "'name' must be 1-200 characters" in src("src/trusted_data_agent/api/flow_routes.py"))
check("dashboard PUT refuses an empty name", "'name' must be 1-200 characters" in src("src/trusted_data_agent/api/dashboard_routes.py"))

# ── 4. kg_rename ownership ───────────────────────────────────────────────────
rr = src("src/trusted_data_agent/api/rest_routes.py")
tree = ast.parse(rr)
fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "kg_rename")
body = ast.get_source_segment(rr, fn)
own = body.find("_kg_owner_profile_for_id(user_uuid, kg_id)")
write = min([i for i in (body.find("UPDATE kg_metadata"), body.find("set_kg_metadata"), body.find(".rename")) if i > 0] or [len(body)])
check("kg_rename checks ownership", own > 0)
check("kg_rename checks ownership before it writes", 0 < own < write, f"own={own} write={write}")
check("kg_rename answers 404 for a graph not owned", "Knowledge graph not found" in body and "404" in body)

# ── 5. the four panels ───────────────────────────────────────────────────────
for comp, has_new_prompt in (("canvas", True), ("flow", True), ("dashboard", True), ("knowledge_graph", False)):
    s = src(f"components/builtin/{comp}/renderer.js")
    i = s.find("mountSplitPanelSwitcher({")
    seg = s[i:i + 3000]
    check(f"{comp}: offers New", "newLabel:" in seg and "onNew:" in seg)
    check(f"{comp}: offers Rename", "onRename:" in seg)
    check(f"{comp}: New {'names inline' if has_new_prompt else 'routes to the constructors'}",
          ("newPrompt:" in seg) == has_new_prompt)
check("dashboard: the old modal create prompt is gone",
      "_promptCreateDashboard" not in src("components/builtin/dashboard/renderer.js"))

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
