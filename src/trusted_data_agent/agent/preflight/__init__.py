"""
Preflight SQL schema grounding — a deterministic, engine-agnostic guard at the MCP tool
seam that catches a hallucinated *database* reference BEFORE the query runs.

The failure it prevents: a weak planner emits ``SELECT * FROM airport.station`` where the
``airport`` database does not exist (a logical KG name mistaken for a physical one). Rather
than pay a DB round-trip to discover ``[Error 3802] Database 'airport' does not exist`` and
then recover, the guard proves non-existence up front against the session's AUTHORITATIVE
catalog (the set of databases from a ``base_databaseList(scope=all)`` result that already
flowed through the same seam).

Regression-safe by construction (the imperative):
  * validates against a COMPLETE catalog only → a valid database is always present, so there
    are NO false positives (unlike a KG-scope allowlist, which would flag valid non-KG dbs);
  * with no cached catalog it is a pure no-op (fail-open);
  * default mode ``observe`` logs only and executes anyway → byte-identical baseline;
  * only raw-SQL-carrying tool calls are inspected → Ideate/Focus (no SQL) never touched.

Universal: the seam is shared by Optimize, Conversation/ReAct and genie slaves, so the guard
covers every SQL-running engine from one place. Deterministic, 0-token, 0-LLM.
"""
