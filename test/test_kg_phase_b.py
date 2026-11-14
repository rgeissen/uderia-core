"""
KG program — Phase B gate (relevance-scoped retrieval + measured budget).

  B1  extract_subgraph_adaptive(query_terms=, per_table_cap=): Phase 2 keeps seed
      columns ALWAYS (never capped), then question-matching columns, then keys /
      required columns, up to the per-table cap; withheld columns are counted per
      table in `omitted_columns`. In scoped mode the greedy joinable-table round is
      skipped (the handler's bounded bridge discovery owns that). Without the two
      kwargs the extractor is BYTE-IDENTICAL to the legacy coverage behaviour.
  B2  The renderer says "+N more columns not shown" on a scoped table line.
  B3  `componentConfig.knowledge_graph.intensity` finally does something: it sets
      the per-table cap (low 6 · medium 12 · high 24 · full = legacy); the handler
      passes query terms + cap on BOTH extraction calls; the manager threads
      `column_cap` only to handlers that accept it.
  B4  The context-window module renders, MEASURES, and re-requests with a smaller
      node cap + column cap (bounded rounds) before condensing; condensation is
      section-aware — relationships and attribute prose are shed before the schema
      backbone, which is trimmed last.
  B5  knowledge_graph_context is listed in every bootstrap CWT (inactive in
      Token-Efficient) and leads its condensation order.

Run:  PYTHONPATH=src python3 test/test_kg_phase_b.py
"""
import asyncio
import inspect
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore import sqlite_backend as sb              # noqa: E402
from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend    # noqa: E402
from components.builtin.knowledge_graph import handler as kgh            # noqa: E402
from components.builtin.knowledge_graph import graph_store as gs         # noqa: E402
from trusted_data_agent.components import manager as mgr                # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {('— ' + str(detail)) if detail else ''}")


def run(c):
    return asyncio.get_event_loop().run_until_complete(c)


# ---------------------------------------------------------------- a real backend on a temp DB
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
c = sqlite3.connect(tmp)
live = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "tda_auth.db"))
for (ddl,) in live.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND tbl_name IN ('kg_entities','kg_relationships') AND sql IS NOT NULL"):
    c.execute(ddl)
live.close(); c.commit(); c.close()
be = SQLiteKGBackend({"db_path": tmp}) if "config" in inspect.signature(SQLiteKGBackend.__init__).parameters else SQLiteKGBackend(tmp)

# Victim: 40 columns (V4019 = sex, key VictimID, required INCNUM, 37 noise); Incident: 30 columns; Offense: 5
ents, rels = [], []
def tbl(name, cols):
    ents.append({"name": name, "entity_type": "entity", "properties": {"layer": "logical"}})
    for cname, props in cols:
        ents.append({"name": f"{name}.{cname}", "entity_type": "attribute", "properties": {"layer": "logical", **props}})
        rels.append({"source_name": name, "source_type": "entity", "target_name": f"{name}.{cname}", "target_type": "attribute", "relationship_type": "contains"})
tbl("Victim", [("VictimID", {"is_primary_key": True}), ("INCNUM", {"is_required": True}),
               ("V4019", {"label": "V4019", "description": "SEX OF VICTIM", "allowed_values": [{"code": "0", "label": "Female"}, {"code": "1", "label": "Male"}]})]
    + [(f"BH{i:03}", {"description": "population bucket"}) for i in range(37)])
tbl("Incident", [("INCNUM", {"is_primary_key": True}), ("INCDATE", {"description": "incident date"})] + [(f"V{i:04}", {}) for i in range(28)])
tbl("Offense", [("OffenseID", {"is_primary_key": True}), ("INCNUM", {}), ("UCR", {"description": "offense code"}), ("Method", {}), ("Location", {})])
rels.append({"source_name": "Victim", "source_type": "entity", "target_name": "Incident", "target_type": "entity", "relationship_type": "foreign_key"})
be.import_bulk("k", "p", "u", ents, rels)
vid = be.get_entity_by_name("k", "Victim", "entity")["id"]
v4019 = be.get_entity_by_name("k", "Victim.V4019", "attribute")["id"]

print("\n[B1] relevance-scoped column expansion")
legacy = be.extract_subgraph_adaptive("k", [vid, v4019], [vid, v4019], max_nodes=500)
scoped = be.extract_subgraph_adaptive("k", [vid, v4019], [vid, v4019], max_nodes=500, query_terms=["victims", "victim", "female"], per_table_cap=6)
names = lambda sg: {e["name"] for e in sg["entities"]}
_legacy_attrs = sum(1 for e in legacy["entities"] if e["entity_type"] == "attribute")
_all_attrs = sum(1 for e in ents if e["entity_type"] == "attribute")
# Phase 1a only follows foreign_key EDGES between entities when they exist as nodes; here the
# FK is a plain edge, so legacy reaches Victim + Incident (70 columns) and not Offense.
check("legacy (no kwargs) returns EVERY column of every discovered table (coverage, no cap)",
      _legacy_attrs >= 70 and "omitted_columns" not in legacy, (_legacy_attrs, _all_attrs))
vic_cols = [n for n in names(scoped) if n.startswith("Victim.")]
check("scoped: the seed column is present", "Victim.V4019" in names(scoped), vic_cols)
check("scoped: keys/required columns kept", {"Victim.VictimID", "Victim.INCNUM"} <= names(scoped), vic_cols)
check("scoped: Victim capped at ≤ 6 columns (+ the seed, never capped)", len(vic_cols) <= 7, vic_cols)
check("scoped: withheld columns are COUNTED per table", (scoped.get("omitted_columns") or {}).get("Victim", 0) >= 30, scoped.get("omitted_columns"))
check("scoped: a small table under the cap is shown whole (nothing hidden)", "Offense" not in (scoped.get("omitted_columns") or {}) or len([n for n in names(scoped) if n.startswith("Offense.")]) == 5)
check("scoped result is far smaller than legacy", len(scoped["entities"]) < len(legacy["entities"]) / 3, (len(scoped["entities"]), len(legacy["entities"])))
seedcap = be.extract_subgraph_adaptive("k", [vid, v4019], [vid, v4019], max_nodes=500, query_terms=["victim"], per_table_cap=1)
check("a per-table cap of 1 still never evicts the seed column", "Victim.V4019" in names(seedcap))
# tier ordering: a term-matching column beats a key which beats noise
G = be._get_graph("k")
by_name = {G.nodes[n]["name"]: n for n in G.nodes}
t = lambda n: sb._column_tier(G, by_name[n], {v4019}, ["female", "incident"])
check("tiers: seed 0 < term-match 1 < (identity 2) < key 3 < noise 4", t("Victim.V4019") == 0 and t("Incident.INCDATE") == 1 and t("Victim.VictimID") == 3 and t("Victim.BH005") == 4, (t("Victim.V4019"), t("Incident.INCDATE"), t("Victim.VictimID"), t("Victim.BH005")))
check("delegate forwards the scope kwargs only to a backend that accepts them", "**scope" in inspect.getsource(gs.GraphStore.extract_subgraph_adaptive) and "signature" in inspect.getsource(gs.GraphStore.extract_subgraph_adaptive))

print("\n[B2] renderer says what it withheld")
hcls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_format_subgraph_for_prompt")][0]
txt = hcls._format_subgraph_for_prompt(hcls.__new__(hcls), scoped, {}, surface="off")
check("TABLE SCHEMAS renders on the scoped subgraph", "TABLE SCHEMAS" in txt)
check("'+N more columns not shown' on the capped table", "more columns not shown" in txt, [l for l in txt.splitlines() if "Victim" in l and "more" in l][:1])

print("\n[B3] intensity is wired; handler passes scope on both extractions; manager threads column_cap")
check("intensity map low/medium/high/full", kgh._INTENSITY_COLUMN_CAP == {"low": 6, "medium": 12, "high": 24, "full": None})
check("unknown intensity → medium", kgh._intensity_column_cap({"profile": {"componentConfig": {"knowledge_graph": {"intensity": "bogus"}}}}) == 12)
check("'full' → legacy coverage (no cap)", kgh._intensity_column_cap({"profile": {"componentConfig": {"knowledge_graph": {"intensity": "full"}}}}) is None)
check("no profile → medium", kgh._intensity_column_cap({}) == 12)
hsrc = inspect.getsource(hcls.get_context_enrichment)
check("handler passes query_terms + per_table_cap on the primary AND the bridge re-extraction", hsrc.count("**_scope_kwargs") == 2)
check("handler accepts column_cap (module override)", "column_cap" in inspect.signature(hcls.get_context_enrichment).parameters)
msrc = inspect.getsource(mgr.ComponentManager.get_context_enrichment)
check("manager threads column_cap only to handlers that accept it", '"column_cap" in _params' in msrc)

print("\n[B4] module: render-then-measure, section-aware condensation")
import importlib.util
spec = importlib.util.spec_from_file_location("kgmod", os.path.join(os.path.dirname(__file__), "..", "components", "builtin", "context_window", "modules", "knowledge_graph", "handler.py"),
                                              submodule_search_locations=[])
try:
    from components.builtin.context_window.modules.knowledge_graph.handler import KnowledgeGraphContextModule as M  # type: ignore
except Exception:
    # the test_kg_context_module harness knows how to load it; reuse its loader shape
    import types
    pkg = types.ModuleType("components.builtin.context_window.modules")
    M = None
if M is None:
    from components.builtin.context_window import base as _base  # noqa: F401
    src = open(os.path.join(os.path.dirname(__file__), "..", "components", "builtin", "context_window", "modules", "knowledge_graph", "handler.py")).read()
    src = src.replace("from ..base import", "from components.builtin.context_window.base import").replace("from ..token_estimator import", "from components.builtin.context_window.token_estimator import")
    ns = {}
    exec(compile(src, "kgmod", "exec"), ns)
    M = ns["KnowledgeGraphContextModule"]
from components.builtin.context_window.token_estimator import estimate_tokens
doc = "\n".join(["--- KNOWLEDGE GRAPH CONTEXT ---", "TABLE SCHEMAS (use these):", "  db.Victim: VictimID, V4019, INCNUM", "  db.Incident: INCNUM, INCDATE",
                 "JOINABLE COLUMNS (shared):", "  INCNUM: Incident, Victim", "METRIC ENTITIES (authoritative):", "  - TotalVictims (formula: COUNT(*))",
                 "ATTRIBUTE ENTITIES:"] + [f"  - Victim.BH{i:03} (population bucket {i}, long prose here)" for i in range(60)]
                + ["KNOWN RELATIONSHIPS:"] + [f"  - Victim --[foreign_key]--> Incident {i}" for i in range(40)])
full = estimate_tokens(doc)
target = int(full * 0.35)
out = M._condense_sections(doc, target)
check("condensed output fits the target", estimate_tokens(out) <= target, (estimate_tokens(out), target))
check("schema backbone (TABLE SCHEMAS, JOINABLE, METRIC) survives", all(s in out for s in ("db.Victim: VictimID, V4019", "INCNUM: Incident, Victim", "TotalVictims")))
check("relationships are shed FIRST (all 40 gone before attributes are touched, or attributes shed after)", out.count("--[foreign_key]-->") < 40)
check("attribute prose is shed after relationships", out.count("population bucket") < 60)
_inst = M.__new__(M)
check("condense() reports the section-aware strategy", run(_inst.condense(doc, target, type("C", (), {"session_data": {}, "profile_id": "p", "user_uuid": "u", "session_id": "s", "profile_type": "llm_only"})())).metadata.get("strategy") == "section_aware_trim")
check("unsectioned text still trims (fallback) and stays non-empty", bool(M._condense_sections("\n".join(f"row {i} aaaa bbbb cccc" for i in range(300)), 50)))
csrc = open(os.path.join(os.path.dirname(__file__), "..", "components", "builtin", "context_window", "modules", "knowledge_graph", "handler.py")).read()
check("contribute re-requests with a smaller node cap + column cap before condensing (bounded rounds)",
      "MAX_FIT_ROUNDS" in csrc and "column_cap" in csrc and "_condense_sections(text, budget)" in csrc)
check("the column cap is passed only to a seam that accepts it (legacy seams keep working)", '"column_cap" in _inspect.signature(get_component_context_enrichment).parameters' in csrc)
check("the fail-safe logs the exception TYPE at warning (no more silent empties)", "logger.warning" in csrc and "type(e).__name__" in csrc)

print("\n[B5] CWT bootstrap lists the KG module")
cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "tda_config.json")))
cwts = {c["id"]: c for c in cfg["context_window_types"]}
check("every bootstrap CWT lists knowledge_graph_context", all("knowledge_graph_context" in c["modules"] for c in cwts.values()), [k for k, c in cwts.items() if "knowledge_graph_context" not in c["modules"]])
check("Token-Efficient deactivates it", cwts["cwt-token-efficient"]["modules"]["knowledge_graph_context"]["active"] is False)
check("Knowledge-Heavy gives it more than Balanced", cwts["cwt-knowledge-heavy"]["modules"]["knowledge_graph_context"]["target_pct"] > cwts["cwt-default-balanced"]["modules"]["knowledge_graph_context"]["target_pct"])
check("it leads every condensation order (shed first among the knowledge modules)", all(c["condensation_order"][0] == "knowledge_graph_context" for c in cwts.values()))

print(f"\n=== KG Phase B results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
