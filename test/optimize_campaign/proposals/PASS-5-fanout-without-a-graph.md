# PASS-5 — a header measure summed across a join to its detail table (fan-out), on an agent with no graph

_From the Optimize campaign, runs 3–6 (2026-10-27). Case `R3`._

## The gap

**Bait:** "What is the total revenue per customer? Join Sales to SaleDetails and sum the amounts; show the top 3 customers by last name."

**What lands:** the weak and the cold lanes sum `Sales.TotalAmount` (a per-sale, HEADER measure) after joining
`SaleDetails` (several rows per sale), multiplying each sale by its line count; the ranking is wrong.
Gap table, run 6: `@OCFL` right, `@OCWEAK` and `@OCFL-NORAG` wrong.

## Why no pass catches it today

Preflight SQL's fan-out check reads the join's cardinality from the ONTOLOGY (`PREFLIGHT_SQL_ARCHITECTURE`):
with a bound knowledge graph it knows `Sales 1 — n SaleDetails` and that `TotalAmount` lives on the
one side. The campaign agents bind no graph, so the check has nothing to read. Cardinality is a fact
about the data, not about the SQL text: a rule on the statement alone cannot know it.

## Candidates (rung 1 first — measure, then decide)

1. **The graph lever (preferred, phase 2 of the campaign):** a campaign knowledge graph over
   fitness_db, bound to a fifth lever `@OCFL-KG`; run R3 and the A family on it. This measures the
   advantage the platform already claims (ontology-checked joins, fan-out-safe data products) instead
   of building a second mechanism.
2. **A key-profile fallback without a graph:** when a statement aggregates a column of table H after
   joining table D on H's key, look up (once per session, cached like `schema_facts`) whether D's key
   column is unique — `SELECT COUNT(*) = COUNT(DISTINCT k)` — and defer the phase with the finding
   when it is not. Deterministic, one cheap query, but it is a SECOND fan-out mechanism beside
   preflight's: only worth it if the graph lever shows agents without a graph are common.

## Triage

- [x] bin: pass proposal (the gap) — deterministic cover exists only with cardinality facts
- [x] decision (2026-10-29): **candidate 2 rejected on the numbers; the graph lever stays phase 2.**
  Offline replay over every saved run (`tda_eval/optimize_campaign/*/raw.json`): the shape —
  `SUM(…TotalAmount)` after a join to `SaleDetails` — occurs in 6 turns and answers wrong in 1
  (`@OCFL-NORAG`, run 6). On the MiMo baseline (`20261028-194317`) R3 is right on `@OCFL` and
  `@OCFL-NORAG` and the shape does not occur. A second fan-out mechanism beside preflight's is
  not justified by one wrong answer in ~200 turns; the ontology path (a campaign KG lever,
  item 9) remains the way to measure the platform's own fan-out guard.
