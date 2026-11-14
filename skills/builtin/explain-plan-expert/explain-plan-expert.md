# Reading a Teradata plan

TDA_ExplainPlan gives you the steps and findings of an EXPLAIN you ran this turn. Use them like this.

**Lead with the finding that changes the cost most.** In order: a product join (every row against every row), a large spool duplicated to all AMPs or redistributed, an all-rows scan of a large table, then estimates made with no confidence. Name the step and the table, not the spool number alone.

**Say what each finding means for this query and what would change it:**
- *Product join* — usually an inequality or a missing join condition. Check the ON clause; an equality on the key the tables share turns it into a hash or merge join.
- *No confidence* — the optimizer had no statistics. Recommend `COLLECT STATISTICS` on the columns the step filters, joins or groups on; the estimates (and often the plan) change once they exist.
- *Duplicated / redistributed* — rows move between AMPs because the join column is not both tables' primary index. Normal for a small input; worth attention when the estimate is large.
- *All-rows scan* — no index serves the filter. Fine for a small table or a filter that keeps most rows.

**Estimates are estimates.** Row counts and times come from the optimizer, not from running the query; say "estimated" and do not present them as measurements. A low or no-confidence figure can be far off.

**Compare, do not guess.** To show that a rewrite or new statistics help, EXPLAIN both versions and call the tool for each statement, then compare the findings and the estimated total.

**Keep it short.** Two or three findings with their remedy are more useful than a step-by-step retelling; the user can open any step in the graph.

For the SQL around plans (catalog views, statistics, query rewrites), the `teradata-patterns` library sections under `performance` have statements that were executed on Teradata.
