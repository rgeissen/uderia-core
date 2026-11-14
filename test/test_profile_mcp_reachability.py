#!/usr/bin/env python3
"""A tool catalog is demanded only of a class that can actually reach one.

WHY THIS GATE EXISTS
Reported 2026-09-09: attesting `@PDOCS`, a Focus (`rag_focused`) agent, was refused
on production — *"the tool catalog is not classified on this instance — run the
profile's connection test (classification) first, then attest"* — and could never
succeed. The loop was closed:

    attest      -> 409 unresolved_surface   (catalog UNRESOLVED)
    reclassify  -> 200 "success"            (the remedy the message names)
    attest      -> 409 unresolved_surface   (byte-identical)

...because `POST /v1/profiles/<id>/reclassify` short-circuits for a class that does
not use MCP and CLEARS the snapshot, which is precisely the state that triggers the
refusal. The editor renders neither the MCP field nor the classification card for
that class, so the named remedy is not merely hidden — running it cannot help.

ROOT CAUSE: five sites already agreed on what "uses MCP" means (configuration_service,
rest_routes x2, agent_pack_manager, configurationHandler.js), and the trust layer
asked a SIXTH, weaker question — *"is an `mcpServerId` stored?"*. A Focus profile
answers yes routinely: a pack stamps the binding onto every member it ships, and a
class change leaves it behind. Storing an id is not reaching a catalog. 555 production
profiles were held in the trap.

THE RULE: reachability is a property of the CLASS, asked in ONE place
(`core/profile_capabilities.consumes_mcp_catalog`).

THE DANGEROUS DIRECTION, asserted here: this must not become "nobody needs a
catalog". The Sep-2026 protection — an Optimize agent may NOT be attested over a
snapshot the instance never took — has to survive intact, or the fix trades a
false refusal for a false assurance.

WHAT IS ASSERTED
  1. The predicate's truth table, incl. the strict default for a malformed profile.
  2. catalog_from_classification across class x binding x snapshot.
  3. The unblock: a Focus/Coordinate agent with an inert binding can be attested.
  4. The protection: an unclassified Optimize (and Ideate+tools) agent still cannot.
  5. Nothing else moves: the LLM and connector halves are untouched by the change.
  6. ONE definition — no site re-implements the predicate.
  7. agent_pack_manager's own question stays separate, deliberately.

Run: python3 test/test_profile_mcp_reachability.py      (no server, no network)
"""
import ast
import os
import re
import sys
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


from trusted_data_agent.core.profile_capabilities import consumes_mcp_catalog, profile_class  # noqa: E402
from trusted_data_agent.core.trust.deployment import (  # noqa: E402
    UNRESOLVED, attest_refusal, catalog_from_classification, unresolved_surfaces,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "trusted_data_agent"
DEPLOY = SRC / "core" / "trust" / "deployment.py"
CFGSVC = SRC / "core" / "configuration_service.py"
REST = SRC / "api" / "rest_routes.py"
PACKMGR = SRC / "core" / "agent_pack_manager.py"


def strip_py_comments(src: str) -> str:
    """Prose about the code is not the code — this fix's own comments name the old
    rule verbatim, and a bare substring scan would be satisfied by the explanation."""
    out = []
    for line in src.splitlines():
        stripped = re.sub(r"#.*$", "", line)
        out.append(stripped)
    txt = "\n".join(out)
    return re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "", txt)


CLASSIFIED = {"tools": {"g": [{"name": "base_readQuery",
                               "arguments": [{"name": "sql"}]}]},
              "prompts": {}, "last_classified": "2026-09-01T00:00:00Z"}
NEVER_TAKEN = {"tools": {}, "prompts": {}, "resources": {}}   # what _build_profile writes
EMPTY_BUT_CLASSIFIED = {"tools": {}, "prompts": {}, "last_classified": "2026-09-01T00:00:00Z"}

BOUND = "server-abc"


def prof(cls, **kw):
    p = {"profile_type": cls, "mcpServerId": BOUND}
    p.update(kw)
    return p


# ── 1 · the predicate ───────────────────────────────────────────────────────
check("Optimize reaches the catalog", consumes_mcp_catalog({"profile_type": "tool_enabled"}))
check("Ideate reaches it only with the capability on",
      consumes_mcp_catalog({"profile_type": "llm_only", "useMcpTools": True})
      and not consumes_mcp_catalog({"profile_type": "llm_only"}))
check("Focus never does", not consumes_mcp_catalog({"profile_type": "rag_focused"}))
check("Coordinate never does", not consumes_mcp_catalog({"profile_type": "genie"}))
check("a malformed profile keeps the STRICTER reading, never 'reaches nothing'",
      consumes_mcp_catalog({}) and consumes_mcp_catalog(None),
      "defaulting to non-consuming would silently drop a real catalog from the hash")
check("profile_class mirrors the runtime default",
      profile_class({}) == "tool_enabled" and profile_class({"profile_type": "genie"}) == "genie")

# ── 2 · the catalog, across class x binding x snapshot ──────────────────────
matrix = [
    # (profile,                                                   expected)
    (prof("tool_enabled", classification_results=CLASSIFIED),      "names"),
    (prof("tool_enabled", classification_results=NEVER_TAKEN),     "UNRESOLVED"),
    (prof("tool_enabled", classification_results=None),            "UNRESOLVED"),
    (prof("tool_enabled", classification_results=EMPTY_BUT_CLASSIFIED), "names"),
    ({"profile_type": "tool_enabled"},                             "None"),   # no binding
    (prof("llm_only", useMcpTools=True, classification_results=NEVER_TAKEN), "UNRESOLVED"),
    (prof("llm_only", useMcpTools=True, classification_results=CLASSIFIED),  "names"),
    (prof("llm_only", classification_results=NEVER_TAKEN),         "None"),   # toggle off
    (prof("llm_only", classification_results=CLASSIFIED),          "None"),
    (prof("rag_focused", classification_results=NEVER_TAKEN),      "None"),   # the report
    (prof("rag_focused", classification_results=CLASSIFIED),       "None"),
    (prof("rag_focused", classification_results=None),             "None"),
    (prof("genie", classification_results=NEVER_TAKEN),            "None"),
]
for p, want in matrix:
    got = catalog_from_classification(p)
    got_s = "UNRESOLVED" if got == UNRESOLVED else ("None" if got is None else "names")
    check(f"catalog[{p['profile_type']}"
          f"{'+tools' if p.get('useMcpTools') else ''}"
          f"{'' if p.get('mcpServerId') else ' (unbound)'}"
          f", {'classified' if (p.get('classification_results') or {}).get('last_classified') else 'never taken'}]"
          f" == {want}", got_s == want, f"got {got_s}")

# ── 3 · the unblock ─────────────────────────────────────────────────────────
focus_sig = {"llm": {"provider": "x", "model": "y"},
             "catalog": catalog_from_classification(prof("rag_focused",
                                                         classification_results=NEVER_TAKEN)),
             "connectors": []}
check("a Focus agent with an inert binding CAN be attested",
      attest_refusal(focus_sig) is None, str(attest_refusal(focus_sig)))
check("...and its signature names no unresolved surface",
      unresolved_surfaces(focus_sig) == [])

# ── 4 · the protection that must NOT be traded away ─────────────────────────
opt_sig = {"llm": {"provider": "x", "model": "y"},
           "catalog": catalog_from_classification(prof("tool_enabled",
                                                       classification_results=NEVER_TAKEN)),
           "connectors": []}
ref = attest_refusal(opt_sig)
check("an unclassified Optimize agent STILL cannot be attested",
      ref is not None, "the Sep-2026 protection would be traded for a false assurance")
check("...and the refusal still names the catalog",
      ref and "catalog" in (ref.get("unresolved") or []), str(ref))
ide_sig = {"llm": {"provider": "x", "model": "y"},
           "catalog": catalog_from_classification(
               prof("llm_only", useMcpTools=True, classification_results=NEVER_TAKEN)),
           "connectors": []}
check("an unclassified Ideate+tools agent still cannot be attested either",
      attest_refusal(ide_sig) is not None)

# ── 5 · nothing else moves ──────────────────────────────────────────────────
dep_src = DEPLOY.read_text(encoding="utf-8")
tree = ast.parse(dep_src)
touched = [n.name for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef)
           and "consumes_mcp_catalog" in (ast.get_source_segment(dep_src, n) or "")]
# Only CATALOG resolvers consult reachability — the primary's, and (PROP-006 phase 2) the one that
# reads each ADDITIONAL MCP server's catalog: both answer "which MCP surface can this agent reach",
# which is exactly what the predicate decides. The LLM and connector halves never do (below).
CATALOG_RESOLVERS = {"catalog_from_classification", "additional_catalogs"}
check("only the catalog resolvers consult reachability",
      set(touched) == CATALOG_RESOLVERS, str(touched))
check("the LLM half is untouched by the predicate",
      "consumes_mcp_catalog" not in (ast.get_source_segment(
          dep_src, next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == "_llm_identity")) or ""))

# ── 6 · ONE definition ──────────────────────────────────────────────────────
INLINE = re.compile(r"""profile_type.{0,40}==.{0,10}['"]tool_enabled['"].{0,120}"""
                    r"""['"]llm_only['"]""", re.S)
for path, label in ((CFGSVC, "configuration_service"), (REST, "rest_routes"),
                    (DEPLOY, "trust/deployment")):
    body = strip_py_comments(path.read_text(encoding="utf-8"))
    check(f"{label} re-implements the predicate nowhere",
          not INLINE.search(body),
          "an inline copy is how the sixth definition appeared in the first place")
    check(f"{label} calls the shared predicate", "consumes_mcp_catalog" in body)
check("the runtime wires it at its MCP-initialisation decision",
      "needs_mcp = consumes_mcp_catalog(profile)" in CFGSVC.read_text(encoding="utf-8"))
check("reclassify wires it too",
      REST.read_text(encoding="utf-8").count("consumes_mcp_catalog") >= 2)

# ── 7 · the pack question stays separate, on purpose ────────────────────────
pack_src = PACKMGR.read_text(encoding="utf-8")
check("agent_pack_manager keeps its own import-time question",
      "def profile_needs_mcp" in pack_src)
check("...and it still honours the manifest-only signals the runtime has no view of",
      "requires_mcp" in pack_src and "mcpServerName" in pack_src,
      "folding it into class reachability would change pack import behaviour")

# The profile TEST asks a THIRD question — "should this profile have an MCP server
# configured?" — and answers it with the looser `tool_enabled or useMcpTools`. Folding
# it in would change an activation outcome for a real production shape (a genie with
# useMcpTools set, measured 2026-09-09), so it stays its own rule. Pinned so a later
# refactor collapses it deliberately rather than by tidiness.
rest_src = REST.read_text(encoding="utf-8")
check("the profile test keeps its own, looser question",
      'profile_type == "tool_enabled" or profile.get("useMcpTools")' in rest_src,
      "folding this changes activation for a genie/rag_focused profile with useMcpTools set")
check("...and says why in place",
      "Only a profile that actually drives MCP tools needs a server." in rest_src)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — a catalog is demanded only where one can be reached"))
sys.exit(1 if FAIL else 0)
