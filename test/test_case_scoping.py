#!/usr/bin/env python3
"""A case is resolved WITHIN the collection the caller names — never by readdir order.

WHY THIS GATE EXISTS
Reported 2026-09-09: inspecting "Policing Data Products — Planner" and clicking any
row rendered "Error loading case details." for admin on BOTH instances, while the
same click worked for demo_walkthrough. The seeding was fine — the repository held
its 14 indexed cases and the list rendered all of them. The detail READ was broken.

    for collection_dir in cases_dir.glob("collection_*"):     # every collection
        if (collection_dir / f"{stem}.json").exists():
            case_path = ...; break                            # FIRST hit wins
    ...
    collection_id = case_data['metadata']['collection_id']    # ...then authorize
    if not rag_context.validate_collection_access(collection_id): -> 403

A case id is NOT unique across collections. An agent pack ships the same seeded
cases to every account that installs it, so `case_<uuid>.json` legitimately exists
under several `collection_<id>/` directories at once — on the reporting instance,
TEN: admin's 8, demo's 55, and eight directories (31/33/35/37/39/41/43/49) left
behind by earlier pack rebuilds whose collections no longer have a row at all.

The glob landed on `collection_33` — a dead id nobody can access — so the endpoint
authorized against a collection neither user owned and answered 403 to BOTH (proven
live against the running server before the fix). Whichever copy readdir returns
first decides who may inspect, and that order differs per instance and per run:
that is the whole of the "works for demo, fails for admin" asymmetry.

The frontend then could not degrade, because its fallback was dead code: the row
loader requests `light=true`, which is exactly the flag that omits `full_case_data`,
so `if (row && row.full_case_data)` was never true and an honest refusal surfaced as
an unattributable "Error loading case details."

THE RULE: the caller knows which repository it is inspecting, so it NAMES it, and
only that collection is read and authorized against. Feedback is the same act one
level up — a vote is about one repository's copy — so it carries the collection too.

WHAT IS ASSERTED
  1. Scoped: only the named collection's copy is a candidate.
  2. Unscoped: still deterministic (sorted), never readdir order.
  3. The exact production layout — 10 copies, 8 orphaned — resolves to the
     collection the caller named, not to the first one on disk.
  4. Authorization runs against the REQUESTED collection, not the file's stored id.
  5. Cross-account is still refused (the fix must not open a door).
  6. "forbidden" stays distinguishable from "not found" (403 vs 404).
  7. All three call sites route through the ONE resolver — no glob survives
     outside it.
  8. The frontend names the collection on both reads, and the dead fallback is
     gone rather than left to mislead.

Run: python3 test/test_case_scoping.py       (no server, no network)
"""
import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


from trusted_data_agent.api.routes import _case_file_candidates, _load_case_from_disk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "src" / "trusted_data_agent" / "api" / "routes.py"
UI = ROOT / "static" / "js" / "ui.js"
API_JS = ROOT / "static" / "js" / "api.js"
EVT_JS = ROOT / "static" / "js" / "eventHandlers.js"

routes_src = ROUTES.read_text(encoding="utf-8")


def strip_js_comments(src: str) -> str:
    """Prose ABOUT the code is not the code.

    This file's own fix carries comments naming `full_case_data` and `light=true`
    to explain why the fallback was removed; a bare substring search would be
    satisfied by that explanation and pass a re-introduced fallback.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def fn_src(src: str, name: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


class FakeCtx:
    """Faithful in the dimension under test: read access is set membership,
    exactly as RAGAccessContext.validate_collection_access resolves it."""

    def __init__(self, accessible):
        self.accessible = set(accessible)
        self.asked = []

    def validate_collection_access(self, collection_id, write=False):
        self.asked.append(collection_id)
        return collection_id in self.accessible


def build_estate(tmp: Path, case_id: str, collection_ids, flat=False):
    """The reporting instance's layout: one case id, N collection copies."""
    cases_dir = tmp / "rag" / "tda_rag_cases"
    for cid in collection_ids:
        d = cases_dir / f"collection_{cid}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{case_id}.json").write_text(json.dumps({
            "case_id": case_id.replace("case_", ""),
            "metadata": {"collection_id": cid, "session_id": f"s-{cid}", "turn_id": 1},
            "intent": {"user_query": f"query as stored in collection {cid}"},
        }))
    if flat:
        cases_dir.mkdir(parents=True, exist_ok=True)
        (cases_dir / f"{case_id}.json").write_text(json.dumps(
            {"case_id": "legacy", "metadata": {}}))
    return cases_dir


CASE = "case_a6bda168-4921-4b90-b7cd-3e7eeb8143c1"
# The real reporting layout: admin 8, demo 55, and eight orphans.
LIVE_IDS = [31, 33, 35, 37, 39, 41, 43, 49, 55, 8]

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    cases_dir = build_estate(tmp, CASE, LIVE_IDS)

    # ── 1 · scoped: only the named collection is a candidate ────────────────
    scoped = _case_file_candidates(cases_dir, CASE, 8)
    check("scoped read yields exactly one candidate", len(scoped) == 1, str(scoped))
    check("...and it is the named collection's copy",
          scoped and scoped[0].parent.name == "collection_8",
          str(scoped))
    check("a case absent from the named collection yields nothing",
          _case_file_candidates(cases_dir, CASE, 999) == [])

    # ── 2 · unscoped stays deterministic ────────────────────────────────────
    unscoped = _case_file_candidates(cases_dir, CASE, None)
    check("unscoped sees every copy", len(unscoped) == len(LIVE_IDS), str(len(unscoped)))
    names = [p.parent.name for p in unscoped]
    check("unscoped order is sorted, not readdir order", names == sorted(names),
          str(names))

    # ── 3+4 · the production scenario resolves to the caller's collection ───
    admin = FakeCtx({8})            # admin owns 8 only
    data, cid, forbidden = _load_case_from_disk(cases_dir, CASE, 8, admin)
    check("admin's scoped read succeeds", data is not None and not forbidden)
    check("...resolves to the collection it named", cid == 8, repr(cid))
    check("...and returns THAT copy's content",
          data and data["metadata"]["collection_id"] == 8)
    check("...authorizing against the requested id, not the file's",
          admin.asked == [8], str(admin.asked))

    demo = FakeCtx({55})
    d2, c2, f2 = _load_case_from_disk(cases_dir, CASE, 55, demo)
    check("demo's scoped read returns ITS OWN copy",
          d2 is not None and c2 == 55 and d2["metadata"]["collection_id"] == 55)

    # ── 5 · the fix must not open a door ────────────────────────────────────
    d3, c3, f3 = _load_case_from_disk(cases_dir, CASE, 55, FakeCtx({8}))
    check("admin may not read demo's copy by naming it", d3 is None and f3 is True)
    d4, c4, f4 = _load_case_from_disk(cases_dir, CASE, 33, FakeCtx({8, 55}))
    check("an orphaned collection is refused, not served", d4 is None and f4 is True)

    # ── 6 · forbidden vs not found stay distinguishable ─────────────────────
    d5, c5, f5 = _load_case_from_disk(cases_dir, "case_does-not-exist", 8, FakeCtx({8}))
    check("a missing case is not-found, never forbidden",
          d5 is None and f5 is False)
    check("a present-but-inaccessible case is forbidden, never not-found",
          _load_case_from_disk(cases_dir, CASE, 8, FakeCtx(set()))[2] is True)

    # ── the pre-fix behaviour is what this gate exists to refuse ────────────
    first_on_disk = next(
        (d for d in cases_dir.glob("collection_*")
         if (d / f"{CASE}.json").exists()), None)
    unscoped_pick = _case_file_candidates(cases_dir, CASE, None)[0].parent
    scoped_pick = _case_file_candidates(cases_dir, CASE, 8)[0].parent
    check("the scoped pick is the caller's collection even when readdir "
          "would have returned another first",
          scoped_pick.name == "collection_8" and first_on_disk is not None)

# ── 4b · a file whose stored id DISAGREES with the directory it sits in ─────
# The fixture above is consistent by construction, so authorizing on the stored
# id and on the requested id are indistinguishable there — a mutation swapping
# them escaped this gate once. A disagreeing file is what separates them, and it
# is not hypothetical: an import that does not re-key, or a collection recreated
# under a new id, leaves exactly this. The door-opening direction is the one that
# matters: a copy sitting in ANOTHER account's directory must not become readable
# because the id it happens to record is one the caller owns.
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    d = tmp / "rag" / "tda_rag_cases"
    (d / "collection_55").mkdir(parents=True)
    (d / "collection_55" / f"{CASE}.json").write_text(json.dumps({
        "case_id": "x",
        # demo's directory, but the file still records the publisher's id 8
        "metadata": {"collection_id": 8},
    }))
    (d / "collection_8").mkdir(parents=True)
    (d / "collection_8" / f"{CASE}.json").write_text(json.dumps({
        "case_id": "x",
        # admin's own directory, mis-stamped with demo's id
        "metadata": {"collection_id": 55},
    }))

    admin_only = FakeCtx({8})
    got, cid, forb = _load_case_from_disk(d, CASE, 55, admin_only)
    check("a copy in another account's directory stays refused even when the id "
          "it records is one the caller owns",
          got is None and forb is True,
          f"resolved={cid} asked={admin_only.asked}")
    check("...because the check was made against the directory the caller named",
          admin_only.asked == [55], str(admin_only.asked))

    admin2 = FakeCtx({8})
    got2, cid2, forb2 = _load_case_from_disk(d, CASE, 8, admin2)
    check("a caller's own copy stays readable even when the file is mis-stamped",
          got2 is not None and cid2 == 8 and not forb2)
    check("...authorized against the collection asked for, not the id stored",
          admin2.asked == [8], str(admin2.asked))

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    cases_dir = build_estate(tmp, CASE, [8], flat=True)
    cands = _case_file_candidates(cases_dir, CASE, 8)
    check("the legacy flat file is a last resort, never preferred",
          len(cands) == 2 and cands[0].parent.name == "collection_8")

# ── 7 · one resolver: no unscoped glob survives outside it ──────────────────
globs = [ln for ln in routes_src.splitlines() if 'cases_dir.glob("collection_*")' in ln]
check("exactly one glob remains in routes.py", len(globs) == 1, str(globs))
check("...and it is the sorted, deliberate unscoped fallback",
      globs and "sorted(" in globs[0], str(globs))
check("...living inside the shared resolver",
      'cases_dir.glob("collection_*")' in fn_src(routes_src, "_case_file_candidates"))

details = fn_src(routes_src, "get_rag_case_details")
feedback = fn_src(routes_src, "update_rag_case_feedback_route")
check("the details endpoint accepts the collection the caller names",
      "request.args.get('collection_id'" in details)
check("...and resolves through the shared resolver",
      "_load_case_from_disk(" in details)
check("...never by hand-rolled scanning", "glob(" not in details)
check("the feedback endpoint accepts the collection too",
      "data.get('collection_id')" in feedback)
check("...and resolves through the same resolver",
      "_load_case_from_disk(" in feedback)
check("...never by hand-rolled scanning", "glob(" not in feedback)
check("the session-warning branch reuses the resolved case",
      "case_metadata = case_data.get('metadata', {}) if case_data else None" in feedback)
check("the details endpoint reads the collection's OWN store, not the "
      "retrieval map, when a collection is named",
      "resolve_store(requested_collection_id, create=False)" in details)

# ── 8 · the frontend names the collection, and the dead fallback is gone ────
ui = strip_js_comments(UI.read_text(encoding="utf-8"))
apijs = strip_js_comments(API_JS.read_text(encoding="utf-8"))
evt = strip_js_comments(EVT_JS.read_text(encoding="utf-8"))

check("the detail read names the inspected collection",
      "collection_id=${encodeURIComponent(inspectedCollectionId)}" in ui)
check("...sourced from the inspected repository",
      "state.currentInspectedCollectionId" in ui)
check("the dead row fallback is gone, not merely bypassed",
      "row.full_case_data" not in ui,
      "the rows are fetched with light=true, so this branch can never fire")
check("...and the failure now carries the server's own reason",
      "Could not load this case" in ui and "Error loading case details." not in ui)
check("the vote names the collection it is about",
      "collection_id" in apijs and "updateRAGCaseFeedback(caseId, vote, collectionId" in apijs)
check("...and the caller supplies it",
      "state.currentInspectedCollectionId" in evt
      and "updateRAGCaseFeedback(" in evt)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — a case is read within the collection the caller names"))
sys.exit(1 if FAIL else 0)
