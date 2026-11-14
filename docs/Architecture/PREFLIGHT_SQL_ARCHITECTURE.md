# Preflight SQL Guards — Architecture

**Wrong SQL is stopped before the database sees it.** Every raw-SQL tool call an agent makes is
checked against what the platform already knows: the knowledge graph's declared keys, subject
areas, value domains and columns, plus the facts the turn's own results have shown. The checks
are deterministic, cost no tokens, and never break a turn.

This document is the reference for the whole `agent/preflight/` package. For the knowledge-graph
enrichment that feeds it, see [KNOWLEDGE_GRAPH_ARCHITECTURE.md](KNOWLEDGE_GRAPH_ARCHITECTURE.md).
For the enforcement chokepoints it shares with Agent Authority, see
[AGENT_AUTHORITY_ARCHITECTURE.md](AGENT_AUTHORITY_ARCHITECTURE.md).

---

## 1. Where it runs

One entry point, `preflight.guard.check_sql(tool_name, arguments, session_id)`, is called at the
two chokepoints every data-running engine passes through:

| Chokepoint | Engines |
|---|---|
| `mcp_adapter/adapter.py` → `invoke_mcp_tool` | Optimize, genie experts, FASTPATH |
| `agent/tool_enforcement.py` (the LangChain tool wrapper) | Ideate + tools, Focus + tools, Coordinate experts of those classes |

It returns either `None` (run the statement) or a **recovery-shaped** error that names the fix, so
the agent can correct itself in the same turn. Any internal failure returns `None`: the guard
fails open and never breaks a turn.

## 2. The checks

The checks run in this order. The first refusal wins.

| Check | Module | What it catches | Evidence it needs |
|---|---|---|---|
| **Join key** | `joins.py` | A join between two tables the graph declares a key for, whose `ON` does not cover every key column (rows multiply). Each side of the `ON` is checked. | `join_keys` from the enrichment |
| **Fan-out** | `joins.py` | Two tables joined to each other on a column that both hold as a foreign key to a third table (fact-to-fact through a dimension key). | `fanout_keys` from the enrichment |
| **Cross-domain** | `joins.py` | A join across two subject areas of the model with no declared per-record key. The refusal names the class-level correspondence to read instead. | `subject_areas` + `class_bridges` |
| **Text-stored dates** | `textdates.py` | A date deployed as text that is compared, ordered or bucketed as a string; a `CAST` whose `FORMAT` does not match the stored shape; a year filter outside the column's measured span. | `text_date_columns` + the stored shape |
| **Value existence** | `values.py` | A literal the column can never hold: a code that is not in a closed value domain, a label used on the code column (or the reverse). | `value_domains` |
| **Column existence** | `columns.py` | A column that does not exist on the table it is attributed to. The refusal names the real columns and the closest matches. | Complete column lists (only for column-complete graphs) |
| **Database existence** | `catalog.py` | A reference to a database that is not in the session's authoritative catalog. The refusal is shaped like Teradata Error 3802, so the correction strategy for that error engages. | A cached `base_databaseList` result, or a direct source's own schema list (`guard.record_source_dictionary`, recorded by the Database Navigator for non-Teradata sources) |

**No evidence, no verdict.** Each check judges only what it can prove. It stays silent when:

- nothing was recorded for the session,
- the statement does not parse,
- the table pair is not declared,
- a column name is ambiguous, or
- a value domain is open (a range or only missing-value codes).

This is why a false positive would need a wrong declaration in the graph, not a guess by the
guard.

### 2.1 A decimal division truncated to a literal's scale (a warning)

Teradata gives a decimal division the scale of its decimal operands: `7/100.0` returns 0.1 (the
true value is 0.07), `1.0/3` returns 0.3, `7*1.0/3` returns 2.3 — no error, a coarse figure.
`decimals.py` finds, on the parse tree, a division whose operands carry a decimal literal of at
most two places, with no FLOAT anywhere in it and no explicit decimal cast with more places. Two
idioms are left alone: a multiplier of 100 or more in the numerator (`x*100.0/total`, a percentage
at 0.1-point resolution) and a division wrapped in `ROUND`/`TRUNC`/a decimal `CAST` to no more
places than the literal gives.

It is **not a check in the order above**: the fix changes the result, so nothing is refused and
nothing is rewritten. After the statement **ran**, the one SQL gate (`sql_gate_after`, every lane)
attaches a note naming the expression and both remedies — a FLOAT operand, or a literal with more
places — so the model can rerun before reporting the figure. A statement that failed carries no
figure and gets no note. Teradata statements only.

### 2.2 Unverified values

A literal with **no** declared domain is never refused before execution; a city name is a
legitimate first filter. If such a filter returns **zero rows**, the result is stamped after
execution with a note that names the distinct-values probe to run.

The honesty gate reads that stamp at answer time (kind `unverified_literal`). A zero caused by a
guessed filter value is therefore never reported as "there are none".

## 3. Where the evidence comes from

- **From the knowledge graph.** The enrichment event records the declared structure for the
  session at the trusted-scope seam (`executor._apply_trusted_scope` →
  `guard.maybe_record_*`): join keys, fan-out keys, subject areas, class bridges, text-date
  columns, value domains and column lists.
- **From results.** The adapter and the tool wrapper learn from rows after each successful call:
  date shapes, observed values, and the database catalog.
- **At build time (optional).** A knowledge graph built with source validation carries sampled
  date shapes, row counts and key uniqueness. That seeds the guard, so the probe round never
  happens.

### 3.1 Whose facts — one agent, several MCP servers

Facts are kept per source (`preflight/dialect.ns`). On Teradata the session's facts are shared,
because the ontology and the primary MCP server describe the same warehouse. An agent with
ADDITIONAL MCP servers (PROP-006 phase 2) reads several warehouses, and one server's database list
must never answer for another's: a call to one of the turn's additional servers keeps its facts
under `<session>::mcp:<server>` even on Teradata. The scope is set by both lanes through
`additional_mcp_scope(server_id)` (the Optimize lane in `invoke_mcp_tool` →
`_invoke_mcp_tool_impl`, the ReAct lane in its `_scoped` wrapper). The primary server and every
single-server agent are unchanged — they share the session's facts as before. Every rule that
recognises the SQL tool reads the tool's base name (`tool_identity.base_name`), so
`crm__base_readQuery` gets exactly the checks `base_readQuery` gets.

## 4. Mechanical fixes cost no LLM round

When a refusal names its own remedy, the platform applies it instead of asking the model:

| Refusal | Fix | Where |
|---|---|---|
| Fan-out join | Aggregate each side at its own grain, then join the aggregates. A `TOP n` is pushed into the ranked side, together with the inner join's membership. Anything needing judgement declines to the refusal. | `joins.rewrite_fanout_sql`, used by `FanoutJoinStrategy` (Optimize) and the tool wrapper (ReAct) |
| Text-stored date | Wrap the column in `CAST(… AS DATE FORMAT '<sampled shape>')` and turn the adjacent literal into a date literal. | `textdates.rewrite_sql`, used by `TextDateStrategy`, the tool wrapper, and the planner pass `text_date_cast` |

Every rewrite re-runs the checks on its own output. A rewrite that another check would refuse
hands back the original refusal. On the ReAct lane a rewrite is recorded as a
`conversation_tool_rewrite` step and sealed into the turn's provenance chain.

### 4.1 The planner reads the same facts before a phase runs

The Optimize planner's refinement pipeline uses the guard's evidence so a statement that would be
refused is not written in the first place (see
[FUSION_OPTIMIZER_ARCHITECTURE.md §3.2](FUSION_OPTIMIZER_ARCHITECTURE.md)):

| Pass | Fact it reads |
|---|---|
| `database_before_sql` | `catalog.has_catalog` / `catalog.nonexistent_db_refs` — with a session catalog, a statement naming a database the source does not have is deferred to the tactical step instead of being run and refused |
| `text_date_cast` | the learned date shapes — `textdates.rewrite_sql` is applied to the plan's literal SQL before it runs |
| `schema_before_sql` | the session's column lists (`agent/schema_facts.py`) — a describe phase is inserted for a table the turn has none for |

These passes are deterministic and independent of the modes below: they defer or rewrite a plan,
they never refuse a call. A statement they defer is written later by the tactical step and then
passes the guard at execution like any other.

## 5. Modes

Each family has its own mode: `observe` (log and record, run anyway), `enforce` (refuse and
return the fix), or `off`.

| Setting | Env override | Governs |
|---|---|---|
| `PREFLIGHT_SQL_MODE` | — | Master for the guard, plus the database-existence check |
| `PREFLIGHT_JOIN_MODE` | `TDA_PREFLIGHT_JOIN_MODE` | Join key, fan-out, cross-domain |
| `PREFLIGHT_TEXT_DATE_MODE` | `TDA_PREFLIGHT_TEXT_DATE_MODE` | Text-stored dates |
| `PREFLIGHT_VALUE_MODE` | `TDA_PREFLIGHT_VALUE_MODE` | Value existence |
| `PREFLIGHT_COLUMN_MODE` | `TDA_PREFLIGHT_COLUMN_MODE` | Column existence |
| `PREFLIGHT_DECIMAL_MODE` | `TDA_PREFLIGHT_DECIMAL_MODE` | Decimal-division warning — `enforce` attaches the note, it never refuses |

- **Defaults.** All modes default to `observe`, so the baseline is unchanged. `docker-compose.yml`
  forwards the five environment overrides.
- **Where rewrites happen.** Rewrites apply only where a refusal happens, which means `enforce`.
- **The assurance dial.** It reads the guard's per-turn tally at the **GROUNDED** station (mechanism `preflight_sql`).

## 6. Tests

`test/test_preflight_schema.py` · `test_preflight_joins.py` · `test_preflight_fanout_rewrite.py`
· `test_preflight_text_dates.py` · `test_preflight_values.py` · `test_preflight_columns.py`
· `test_preflight_decimals.py` · `test_preflight_dialect.py` (per-source fact namespaces, §3.1).
Planner-side: `test_database_before_sql_pass.py`, `test_schema_facts.py`.
All are CI-pure (no server, no database); all but `test_preflight_schema.py` run in `test/trust_battery.sh`.
