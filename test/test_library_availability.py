"""A library section is offered for what the agent's own database can run.

Loom adoption, integration (b). Each executed statement of ``teradata-patterns`` records the
database functions it calls (``index[].requires``); the agent's own database is asked which of
them it has, through the catalog provider the Database Navigator uses; the root page drops a
section most of whose statements cannot run there, ``list_sections`` and ``read`` name what is
missing. Never a refusal, never filtered without a successful probe.

  [1] the decision per section (pure)
  [2] the root page filter reads only the cache and fails open
  [3] the probe: one provider call, cached; a source that cannot tell changes nothing
  [4] the catalog contract: Teradata (direct and MCP) answers from DBC.TablesV, others cannot tell
  [5] the library tool marks and warns through the real handler
  [6] wiring: the injection seam, the shared agent source, the build records requirements
  [7] (opt-in) live: the dictionary read on a real Teradata — TDA_LIVE_TERADATA=1 + TDP_*
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from trusted_data_agent.skills import library_availability as la  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f": {detail}" if detail not in (None, "", [], {}) else ""))


NPATH = {"anchor": "analytics/npath", "statements": 8,
         "requires": {f"s{i}": ["NPATH"] for i in range(5)}}
CAST = {"anchor": "core/data_types_casting", "statements": 12,
        "requires": {"vector_table": ["AIVECTOR"], "try_cast": ["TRYCAST"]}}
PLAIN = {"anchor": "core/sql_basics", "statements": 8}
INDEX = [NPATH, CAST, PLAIN]

print("[1] the decision per section")
st = la.section_status(NPATH, {"TRYCAST"})
check("5 of 8 statements cannot run → hidden", st["hidden"] and len(st["blocked"]) == 5, st)
st = la.section_status(CAST, {"TRYCAST"})
check("1 of 12 cannot run → shown, the statement named", not st["hidden"] and st["blocked"] == {"vector_table": ["AIVECTOR"]}, st)
check("a section without requirements is never blocked", la.section_status(PLAIN, set())["blocked"] == {})
check("unknown availability blocks nothing", la.section_status(NPATH, None) == {"total": 8, "blocked": {}, "hidden": False})
check("exactly half is not 'most' — shown", not la.section_status(
    {"statements": 4, "requires": {"a": ["X"], "b": ["X"]}}, set())["hidden"])
check("names compare case-insensitively", la.section_status(
    {"statements": 1, "requires": {"a": ["npath"]}}, {"NPATH"})["blocked"] == {})
check("the functions to ask for are the union over the index",
      la.required_functions(INDEX) == {"NPATH", "AIVECTOR", "TRYCAST"})

print("[2] the root page filter")
ROOT_PAGE = ("## Sections\n\n<!-- index:begin -->\n**analytics**\n- `analytics/npath` — nPath sequence analysis\n\n"
             "**core**\n- `core/data_types_casting` — Teradata data types and casting\n"
             "- `core/sql_basics` — Teradata SQL basics\n<!-- index:end -->\n")
PROF = {"id": "p-1", "mcpServerId": "srv-1"}
la._CACHE.clear()
check("no probe yet → byte-identical (fail open)", la.filter_root(ROOT_PAGE, INDEX, PROF) == ROOT_PAGE)
la.remember(PROF, {"TRYCAST"}, "the Teradata MCP server")
out = la.filter_root(ROOT_PAGE, INDEX, PROF)
check("the section most of whose statements cannot run is gone", "analytics/npath" not in out, out)
check("…a partly runnable section stays", "core/data_types_casting" in out and "core/sql_basics" in out)
check("…and the page says what was left out and where to see it",
      "1 section(s) are not listed because the Teradata MCP server lacks" in out and "list_sections" in out
      and out.index("not listed") < out.index("<!-- index:end -->"), out)
check("another agent's source is not affected by this cache",
      la.filter_root(ROOT_PAGE, INDEX, {"id": "p-2", "mcpServerId": "srv-9"}) == ROOT_PAGE)
la.remember(PROF, {"TRYCAST", "NPATH", "AIVECTOR"}, "x")
check("a database with everything → unchanged", la.filter_root(ROOT_PAGE, INDEX, PROF) == ROOT_PAGE)
la._CACHE[la.source_key(PROF)] = (0.0, set(), "old")
check("an expired probe is not trusted (fail open)", la.filter_root(ROOT_PAGE, INDEX, PROF) == ROOT_PAGE)

print("[3] the probe")
import trusted_data_agent.dbcatalog.agent_source as asrc  # noqa: E402


class FakeProvider:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def existing_functions(self, names):
        self.calls.append(set(names))
        return self.answer


class FakeSvc:
    pass


def fake_agent_provider(provider):
    async def _f(context, source_id=None):
        return FakeSvc(), provider, {"default": "mcp:srv-1", "sources": [{"id": "mcp:srv-1", "label": "Teradata MCP"}]}
    return _f


orig_ap, orig_prof = asrc.agent_provider, asrc.agent_profile
try:
    asrc.agent_profile = lambda ctx: ctx.get("profile_config") or {}
    la._CACHE.clear()
    prov = FakeProvider({"TRYCAST"})
    asrc.agent_provider = fake_agent_provider(prov)
    ctx = {"profile_config": PROF, "user_uuid": "u"}
    res = asyncio.run(la.probe(ctx, INDEX))
    check("the probe asks the agent's provider for exactly the required functions",
          prov.calls == [{"NPATH", "AIVECTOR", "TRYCAST"}] and res == ({"TRYCAST"}, "Teradata MCP"), (prov.calls, res))
    asyncio.run(la.probe(ctx, INDEX))
    check("…and caches: a second call does not ask again", len(prov.calls) == 1)
    check("…which the root filter then reads", "analytics/npath" not in la.filter_root(ROOT_PAGE, INDEX, PROF))
    la._CACHE.clear()
    asrc.agent_provider = fake_agent_provider(FakeProvider(None))
    check("a source that cannot tell → None, nothing cached",
          asyncio.run(la.probe(ctx, INDEX)) is None and la.filter_root(ROOT_PAGE, INDEX, PROF) == ROOT_PAGE)
    asrc.agent_provider = fake_agent_provider(None)
    check("no source → None", asyncio.run(la.probe(ctx, INDEX)) is None)

    async def boom(context, source_id=None):
        raise RuntimeError("network down")
    asrc.agent_provider = boom
    check("a failing probe → None, never raises", asyncio.run(la.probe(ctx, INDEX)) is None)
    check("an index without requirements never probes", asyncio.run(la.probe(ctx, [PLAIN])) is None)
finally:
    asrc.agent_provider, asrc.agent_profile = orig_ap, orig_prof

print("[4] the catalog contract")
from trusted_data_agent.dbcatalog.teradata_types import FUNCTION_DATABASES, existing_functions_sql  # noqa: E402
from trusted_data_agent.dbcatalog.teradata_provider import TeradataCatalogProvider  # noqa: E402
from trusted_data_agent.dbcatalog.mcp_provider import McpCatalogProvider  # noqa: E402
from trusted_data_agent.dbcatalog.base import CatalogProvider  # noqa: E402

sql = existing_functions_sql(["npath", "td_dfft", "bad name;drop", "ATTRIBUTION"])
check("the read is DBC.TablesV over the function databases (UAF functions are not in FunctionsV)",
      "DBC.TablesV" in sql and "FunctionsV" not in sql and all(d in sql for d in FUNCTION_DATABASES))
check("…names are upper-cased and anything not an identifier is dropped",
      "'NPATH'" in sql and "'TD_DFFT'" in sql and "drop" not in sql.lower().split("in (")[-1], sql)
check("no valid name → no read", existing_functions_sql(["a b", ""]) == "")
seen = []


async def runner(creds, s, max_rows=0, **kw):
    seen.append(s)
    return ["fn"], [["NPATH"], ["TD_DFFT "]]
tp = TeradataCatalogProvider("conn:1", "Teradata", {"host": "h"}, runner=runner)
check("Teradata direct: answers from the dictionary read",
      asyncio.run(tp.existing_functions({"NPATH", "TD_DFFT", "X"})) == {"NPATH", "TD_DFFT"} and "DBC.TablesV" in seen[0])


async def mcp_invoke(tool, args):
    return {"status": "success", "results": [{"fn": "NPATH"}]}
roles = {"run_sql": {"tool": "base_readQuery", "sql_arg": "sql"}}
mp = McpCatalogProvider("mcp:x", "MCP", mcp_invoke, roles, dialect_hint="teradata")
check("Teradata over MCP: answers through the server's read tool", asyncio.run(mp.existing_functions({"NPATH"})) == {"NPATH"})
mp2 = McpCatalogProvider("mcp:y", "MCP", mcp_invoke, roles, dialect_hint="postgres")
check("a non-Teradata MCP server cannot tell (None, not 'nothing exists')", asyncio.run(mp2.existing_functions({"NPATH"})) is None)
check("the base contract cannot tell", "return None" in Path(sys.modules[CatalogProvider.__module__].__file__).read_text()
      .split("async def existing_functions")[1].split("async def ")[0])

print("[5] the library tool marks and warns")
from components.builtin.skill_library.handler import SkillLibraryHandler  # noqa: E402
h = SkillLibraryHandler()
idx = json.loads((ROOT / "skills/builtin/teradata-patterns/skill.json").read_text())["uderia"]["progressive"]["index"]
npath = next(e for e in idx if e["anchor"] == "analytics/npath")
mark = h._availability_mark(npath, (set(), "the Teradata MCP server"))
check("list_sections names what the database lacks and how much of the section it affects",
      "the Teradata MCP server lacks" in mark and "NPATH" in mark and "does not apply" in mark, mark)
check("…nothing when availability is unknown", h._availability_mark(npath, None) == "")
check("…nothing when the database has it all", h._availability_mark(npath, ({"NPATH", "SESSIONIZE"}, "x")) == "")
prof = {"id": "p", "skillsConfig": {"skills": [{"id": "teradata-patterns", "active": True}]}}
try:
    payload = h._read("teradata-patterns", "analytics/npath", prof, (set(), "the Teradata MCP server"))
    text = payload.spec.get("text") if hasattr(payload, "spec") and isinstance(payload.spec, dict) else ""
    text = text or (payload.metadata or {}).get("text", "")
    check("read still returns the section (never refused on an inference), with the warning first",
          "does not have the functions" in text and "# nPath" in text
          and text.index("does not have the functions") < text.index("# nPath"), text[:300])
except Exception as exc:  # noqa: BLE001
    check("read returns the section with the warning", False, repr(exc))

orig_probe = la.probe
async def _no_probe(context, index):
    return None
la.probe = _no_probe
try:
    out = asyncio.run(h.process({"action": "list_sections", "skill_id": "teradata-sql-expert"}, {"profile": prof}))
    txt = (out.metadata or {}).get("text", "")
    check("a skill named without a library, ONE library active → read from it, and the note says so",
          (out.metadata or {}).get("skill_id") == "teradata-patterns" and txt.startswith("['teradata-sql-expert' has no section library"), txt[:160])
    h2 = SkillLibraryHandler()
    h2._progressive_candidates = lambda profile: ["teradata-patterns", "other-lib"]
    out2 = asyncio.run(h2.process({"action": "list_sections", "skill_id": "teradata-sql-expert"}, {"profile": prof}))
    check("…two libraries active → still asks which (never guesses between them)",
          (out2.metadata or {}).get("error_code") == "skill_not_active", out2.metadata)
finally:
    la.probe = orig_probe

print("[6] wiring")
mgr = (ROOT / "src/trusted_data_agent/skills/manager.py").read_text()
check("skill injection filters the root page after the gates", "filter_root(content.content, _idx, profile_config)" in mgr
      and mgr.index("gate_skill_text(content.content, profile_config)") < mgr.index("filter_root(content.content"))
nav = (ROOT / "components/builtin/database_navigator/handler.py").read_text()
check("the Navigator and the library resolve the agent's database through ONE module",
      "from trusted_data_agent.dbcatalog.agent_source import agent_provider" in nav
      and "available_tools_live" not in nav, "the navigator still carries its own copy")
lib = (ROOT / "components/builtin/skill_library/handler.py").read_text()
check("the library tool probes before list/read", "_la.probe(" in lib)
check("the build records requirements per statement from what the system has",
      all("requires" in e for e in idx if e["anchor"] in ("analytics/npath", "timeseries/arima")))
check("…and a section with none carries none", "requires" not in next(e for e in idx if e["anchor"] == "core/sql_basics"))
script = (ROOT / "scripts/teradata_patterns.py").read_text()
check("the build and the runtime use the same dictionary read", "existing_functions_sql" in script)

print("[7] live")
if os.environ.get("TDA_LIVE_TERADATA") == "1" and os.environ.get("TDP_HOST"):
    sys.path.insert(0, str(ROOT / "scripts"))
    import teradata_patterns as tpm
    cur = tpm.connect().cursor()
    cur.execute(existing_functions_sql(["NPATH", "TD_DFFT", "ATTRIBUTION", "NO_SUCH_FUNCTION_X"]))
    got = {str(r[0]).strip() for r in cur.fetchall()}
    check("live: present functions found (incl. a UAF one), an absent one not", got == {"NPATH", "TD_DFFT", "ATTRIBUTION"}, got)
else:
    print("  skipped — set TDA_LIVE_TERADATA=1 and TDP_HOST/TDP_USER/TDP_PASSWORD")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
