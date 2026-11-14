"""The tool surface's second ranker — meaning — against the REAL catalog, with the real model.

`test_tool_surface.py` proves the rule with an injected embedder. This gate proves the
calibration: over the platform's own 62-tool catalog (test/fixtures/tool_catalog_calibration.json,
the three local Optimize agents' catalogs merged, Oct 2026) and the local embedding model, the
paraphrases the identifier scorer is blind to are OFFERED by meaning, a question that names no
capability adds nothing, the cap holds, and a warm query encode is milliseconds. A changed model,
a changed description text, or a changed constant that breaks one of these fails here by name.

Runs offline (HF_HUB_OFFLINE): the model is the platform's RAG embedder, cached in this process
by the same class-level cache the retriever uses.
"""
import json, logging, os, pathlib, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.setdefault("HF_HUB_OFFLINE", "1"); os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:400]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]

from trusted_data_agent.agent import tool_surface as TS, tool_semantics as SEM

raw = json.loads((ROOT / "test/fixtures/tool_catalog_calibration.json").read_text())
CAT = [TS.ToolInfo(t["name"], t.get("category", ""), t.get("description", "")) for t in raw]
CAT.append(TS.ToolInfo("TDA_FindTools", "Component Tools", "find a held tool by describing the capability"))
NAMES = [t.name for t in CAT]

print("[1] the model resolves off the turn path and is then ready")
t0 = time.time(); SEM._resolve_sync(); load_s = time.time() - t0
check("the platform's embedder resolved (the RAG model, cached per process)", SEM.ready() and SEM.state() == "ready", SEM.state())
check("the stamp will name the model", SEM.model_name() == "all-MiniLM-L6-v2", SEM.model_name())

print("[2] calibration — paraphrases the words are blind to are offered by meaning")
# (question, the tool that answers it) — every one of these has ZERO identifier overlap with
# its tool (table/column/database are generic words the lexical scorer drops on purpose)
CASES = [("How much disk is each table consuming?", "dba_tableSpace"),
         ("Are there any blanks in the customer records?", "qlty_missingValues"),
         ("How busy was the box over the last week?", "dba_resusageSummary"),
         ("Which tables get hit the most?", "dba_tableUsageImpact"),
         ("Any values below zero in the price column?", "qlty_negativeValues")]
lat = []
for q, exp in CASES:
    terms = TS.question_terms(q)
    lex = TS.score(next(t for t in CAT if t.name == exp), terms)
    t0 = time.time(); sims = SEM.similarity(q, CAT); lat.append(time.time() - t0)
    s = TS.select(q, CAT, semantic=sims)
    check(f"'{q}' → {exp}: the words miss (lexical 0) and meaning offers it", lex == 0 and exp in s.offered and exp in s.ranker["semantic_added"],
          {"lexical": lex, "added": s.ranker["semantic_added"], "top": sorted(sims.items(), key=lambda x: -x[1])[:4] if sims else None})
    check(f"'{q}': the reason carries the score", s.reasons.get(exp, "").startswith("near in meaning to the question (") , s.reasons.get(exp))
    check(f"'{q}': at most {SEM.CAP} semantic additions", len(s.ranker["semantic_added"]) <= SEM.CAP, s.ranker["semantic_added"])

print("[3] restraint — a question about nothing adds nothing; a precise lexical hit sets the bar")
sims_j = SEM.similarity("Tell me a joke", CAT); s_j = TS.select("Tell me a joke", CAT, semantic=sims_j)
check("'Tell me a joke' adds no tool by meaning (every score under the floor)", s_j.ranker["semantic_added"] == [] and max(sims_j.values()) < SEM.FLOOR, (s_j.ranker["semantic_added"], max(sims_j.values())))
sims_v = SEM.similarity("What version of Teradata is this?", CAT); s_v = TS.select("What version of Teradata is this?", CAT, semantic=sims_v)
check("'What version of Teradata is this?' — dba_databaseVersion is the clear best (0.68, a word hit); the next tools (0.46 and below) do NOT ride along — the ratio pins this", "dba_databaseVersion" in s_v.offered and s_v.ranker["semantic_added"] == [] and max(sims_v.values()) > 0.6, (s_v.ranker["semantic_added"], sorted(sims_v.items(), key=lambda x: -x[1])[:3]))
sims_p = SEM.similarity("Draw a pie of sales by region", CAT); s_p = TS.select("Draw a pie of sales by region", CAT, semantic=sims_p)
best_p = max(sims_p.values())
check("'Draw a pie of sales by region' — plot_pie_chart by its word; plot_polar_chart sits above the floor but under 80% of the best and is NOT added (the ratio, pinned)", "plot_pie_chart" in s_p.offered and s_p.ranker["semantic_added"] == [] and SEM.FLOOR < sims_p["plot_polar_chart"] < 0.8 * best_p, (s_p.ranker["semantic_added"], round(sims_p["plot_polar_chart"], 3), round(best_p, 3)))
sims_c = SEM.similarity("How many customers are in the fitness database?", CAT); s_c = TS.select("How many customers are in the fitness database?", CAT, semantic=sims_c)
check("a plain data question: the backbone answers it; meaning adds at most the cap", "base_readQuery" in s_c.offered and len(s_c.ranker["semantic_added"]) <= SEM.CAP, s_c.ranker["semantic_added"])

print("[4] the finder search by meaning — the model's words, not the tool's")
s4 = TS.select("x", CAT); r4 = TS.find(s4, CAT, "storage footprint per table", semantic=SEM.similarity("storage footprint per table", CAT))
check("'storage footprint per table' finds dba_tableSpace among the held (words: none)", "dba_tableSpace" in [t["name"] for t in r4["found"]], r4["found"])

print("[5] cost — one catalog embedding per content signature; a warm query is milliseconds")
calls = {"n": 0}; _orig = SEM._embed
def _count(texts): calls["n"] += 1; return _orig(texts)
SEM._embed = _count
SEM.similarity("a", CAT); SEM.similarity("b", CAT)
SEM._embed = _orig
check("two questions over a cached catalog = two query encodes, no catalog re-embed", calls["n"] == 2, calls)
cat2 = list(CAT); cat2[0] = TS.ToolInfo(cat2[0].name, cat2[0].category, cat2[0].description + " (changed)")
calls["n"] = 0; SEM._embed = _count; SEM.similarity("a", cat2); SEM._embed = _orig
check("a changed description changes the signature → the catalog is re-embedded (never stale)", calls["n"] == 2, calls)
warm_ms = 1000 * sorted(lat)[len(lat) // 2]
check(f"median warm query encode under 150 ms (measured {warm_ms:.0f} ms; model load {load_s:.1f}s was off the turn path)", warm_ms < 150, warm_ms)

print(f"\n=== tool semantics (real model, real catalog): {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
