# PASS-6 — a SQL runner looped over a table's columns is one statement, not N tactical calls

_From the Optimize campaign, run `20261029-075541` (2026-10-29). Case `R5`. Opened by the OC-F17 fix._

## The gap

**Question:** "Show me summary statistics for every numeric column of fitness_db.Products."

**The plan every lever writes:** phase 1 `base_columnDescription(fitness_db, Products)`, phase 2 a
`loop_over: result_of_phase_1` of `base_readQuery` with NO statement ("calculate MIN, MAX, AVG,
COUNT for each"), phase 3 the report.

**Before OC-F17:** the loop's FASTPATH ran `base_readQuery({})` once per column; FastMCP 4 answered
"No SQL provided" as success six times, the repeated-call guard stopped it, the answer had no
statistics. Cheap and wrong.

**After OC-F17:** the loop is no longer FASTPATHed, so each item goes to the tactical step, which
writes one statement per column — including the VARCHAR columns the question excludes. R5 is now
answered with real statistics on all three levers, at 8 (MiMo warm), 10 (cold) and 13 (Nova) LLM
calls, 74K–133K input tokens; invariant O9 (≤ 4 tactical calls) fails on every lever. Right and
expensive.

## Candidate (rung 1 — replay before building)

A deterministic rewrite of the PLAN, before execution:

- **Trigger:** a `type: loop` phase whose single tool takes `sql` (`phase_executor._takes_sql`),
  carries no statement, and loops over the result of a `base_columnDescription` phase on ONE table.
- **Rewrite:** replace the loop with ONE `base_readQuery` phase whose SQL the platform builds from
  the described columns — `SELECT COUNT(c), MIN(c), MAX(c), AVG(c) … FROM db.table` over the
  columns whose `CType` is numeric (the same numeric test the column-expansion path already uses:
  INT · NUMERIC · DECIMAL · FLOAT · BYTEINT · SMALLINT · BIGINT). 0 LLM calls for the phase.
- **Stands down when:** the goal names an aggregate the platform cannot map to SQL, the loop reads
  more than one table, or the column set is unknown at plan time (the description phase has not
  run — the rewrite then runs at execution time, after phase 1, like column expansion).

## What the replay must show

Over every saved plan (`raw_llm_plan` in `tda_eval/optimize_campaign/*/raw.json` and real
Optimize sessions): how many plans have this shape, and on how many the generated statement's
figures match the oracle (`oracle.py` per column). One wrong figure = not ready.

## Triage

- [ ] replay over saved plans
- [ ] decide: build the pass / close
