"""
Pane Visibility speaks the sidebar's language — gate for core/pane_catalog.py.

The admin's Pane Visibility table once listed Setup / Conversations / Executions long
after the sidebar had become Anatomy / Reasoning / Awareness, and it had no row for
Trust at all: the names lived in the stored rows, written once at first boot. Now the
catalogue owns every name and is synced onto the rows on read. This gate holds the
catalogue to the sidebar (templates/index.html) and the sync to its contract.

    PYTHONPATH=$(pwd)/src python test/test_pane_catalog.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from trusted_data_agent.auth.models import PaneVisibility  # noqa: E402
from trusted_data_agent.core import pane_catalog  # noqa: E402
from trusted_data_agent.core.config import APP_STATE  # noqa: E402

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {label}")


index = (ROOT / "templates" / "index.html").read_text()
partial = (ROOT / "templates" / "partials" / "ontology_nav.html").read_text()
nav_html = index[index.index('<nav id="app-side-nav"'):index.index("</nav>", index.index('<nav id="app-side-nav"'))]
nav_html = nav_html.replace("{% if ontology_enabled %}{% include 'partials/ontology_nav.html' ignore missing %}{% endif %}", partial)

# Sidebar: (element id, label, group) in order.
sidebar, group = [], None
for m in re.finditer(r'nav-group-label"><span class="nav-link-text">([^<]+)</span>|id="(view-switch-[a-z-]+)"[\s\S]*?<span class="nav-link-text[^"]*">([^<]+)</span>', nav_html):
    if m.group(1):
        group = m.group(1)
    else:
        sidebar.append((m.group(2), m.group(3).strip(), group))

pane_map = dict(re.findall(r"'([a-z-]+)': '(view-switch-[a-z-]+)'",
                           index[index.index("const paneMap = {"):index.index("};", index.index("const paneMap = {"))]))

# 1 — every sidebar view has a catalogue entry, and the catalogue invents none.
by_elem = {pane_map.get(e["pane_id"]): e for e in pane_catalog.PANE_CATALOG}
check("every catalogue pane_id is mapped to a sidebar element (paneMap)",
      all(e["pane_id"] in pane_map for e in pane_catalog.PANE_CATALOG))
for elem, label, grp in sidebar:
    entry = by_elem.get(elem)
    check(f"sidebar view {elem} ({label}) has a catalogue entry", entry is not None)
    if entry:
        check(f"{elem}: catalogue name '{entry['pane_name']}' == sidebar label '{label}'", entry["pane_name"] == label)
        check(f"{elem}: catalogue group {entry['group']!r} == sidebar group {grp!r}", entry["group"] == grp)
check("catalogue lists no view the sidebar lacks",
      {pane_map[e["pane_id"]] for e in pane_catalog.PANE_CATALOG} == {s[0] for s in sidebar})

# 2 — catalogue order is sidebar order.
order = [pane_map[e["pane_id"]] for e in pane_catalog.PANE_CATALOG]
check("catalogue order == sidebar order", order == [s[0] for s in sidebar])

# 3 — the retired names are gone from the catalogue.
names = {e["pane_name"] for e in pane_catalog.PANE_CATALOG}
check("no retired name (Setup / Conversations / Executions)", not names & {"Setup", "Conversations", "Executions"})

# 4 — sync: renames stored rows, keeps the admin's flags, inserts missing views.
engine = create_engine("sqlite://")
PaneVisibility.__table__.create(engine)
Session = sessionmaker(bind=engine)
s = Session()
s.add(PaneVisibility(pane_id="credentials", pane_name="Setup", description="Configure LLM and MCP credentials",
                     display_order=1, visible_to_user=False, visible_to_developer=True, visible_to_admin=True))
s.add(PaneVisibility(pane_id="executions", pane_name="Executions", description="old", display_order=3,
                     visible_to_user=True, visible_to_developer=False, visible_to_admin=True))
s.add(PaneVisibility(pane_id="admin", pane_name="Administration", description="old", display_order=7,
                     visible_to_user=False, visible_to_developer=False, visible_to_admin=False))
s.commit()

APP_STATE["ontology_enabled"] = False
out = pane_catalog.sync_panes(s)
ids = [p["pane_id"] for p in out]
check("ontology is absent when the feature is off", "ontology" not in ids)
cred = next(p for p in out if p["pane_id"] == "credentials")
check("stored 'Setup' row now reads 'Anatomy'", cred["pane_name"] == "Anatomy")
check("…and kept the admin's user=off choice", cred["visible_to_user"] is False)
ex = next(p for p in out if p["pane_id"] == "executions")
check("stored 'Executions' row now reads 'Awareness' with its flags kept",
      ex["pane_name"] == "Awareness" and ex["visible_to_user"] is True and ex["visible_to_developer"] is False)
check("Trust row inserted, visible to every tier",
      any(p["pane_id"] == "agents" and p["visible_to_user"] and p["visible_to_developer"] for p in out))
check("Administration is forced visible to admins", next(p for p in out if p["pane_id"] == "admin")["visible_to_admin"])
check("rows carry their sidebar group", cred["group"] == "The Agent")
check("display_order is consecutive sidebar order", [p["display_order"] for p in out] == list(range(1, len(out) + 1)))

APP_STATE["ontology_enabled"] = True
check("ontology appears when the feature is on", "ontology" in [p["pane_id"] for p in pane_catalog.sync_panes(s)])

reset = pane_catalog.reset_panes(s)
check("reset restores catalogue defaults (Awareness hidden from users)",
      next(p for p in reset if p["pane_id"] == "executions")["visible_to_user"] is False)
APP_STATE.pop("ontology_enabled", None)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
