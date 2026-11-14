# EXPLAIN plan analysis

EXPLAIN and DYNAMIC EXPLAIN, reading access paths, join methods and confidence phrases, COLLECT STATISTICS to raise confidence, HASHAMP skew checks.

**Use for:** checking a statement's syntax and plan without running it; finding a full-table scan, a product join or a redistribution before it costs time; seeing whether the optimizer has statistics for a condition; checking how evenly a primary index spreads rows over the AMPs.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `sql_query` | The statement to explain, without a trailing semicolon | `SELECT order_id, amount FROM retail_demo.orders WHERE customer_id = 7` |
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to analyse | `orders` |
| `pi_column` | Primary index column of the table | `order_id` |
| `column` | A column used in conditions or joins | `customer_id` |
| `stats_table` | Name for a table copy the statement creates and collects statistics on | `orders_explain_copy` |

## Statements

### explain — The plan of a statement, without running it

EXPLAIN returns the plan as rows of text and executes nothing: an EXPLAIN of a DELETE deletes no rows. A statement that does not parse, or names a missing object, fails with the same error it would raise when run (3706, 3807). Steps are numbered in execution order; each step states its access path, where its result goes, an estimated row count with a confidence phrase, and an estimated time.

```sql
EXPLAIN {{sql_query}};
```

The plan of the example, shortened: a scan of every row on every AMP, with a row estimate the optimizer has no statistics for.

```text
  3) We do an all-AMPs RETRIEVE step in TD_MAP1 from retail_demo.orders by
     way of an all-rows scan with a condition of (
     "retail_demo.orders.customer_id = 7") into Spool 1 (group_amps), which
     is built locally on the AMPs.  The size of Spool 1 is estimated
     with no confidence to be 102 rows (3,366 bytes).  The estimated
     time for this step is 0.00 seconds.
  -> The contents of Spool 1, are sent back to the user as the result
     of statement 1.  The total estimated time is 0.00 seconds.
```

### dynamic_explain — The plan under incremental planning and execution

DYNAMIC EXPLAIN asks for the dynamic plan, in which the optimizer runs parts of the request (such as a scalar subquery) and plans the rest with their results. On this system both examples return the static plan; the second states the reason in its first lines: "This request is eligible for incremental planning and execution (IPE) but does not meet cost thresholds."

```sql
DYNAMIC EXPLAIN {{sql_query}};

DYNAMIC EXPLAIN
SELECT order_id, amount
FROM {{database}}.{{table}}
WHERE amount > (SELECT AVG(amount) FROM {{database}}.{{table}});
```

### access_paths — Primary index access against a full scan

The three plans read, in this order: "single-AMP RETRIEVE step ... by way of the unique primary index" (one row, one AMP), "single-AMP RETRIEVE step ... by way of the primary index" (a non-unique primary index: all rows of one value, one AMP), and "all-AMPs RETRIEVE step ... by way of an all-rows scan" (the condition is on a column without an index).

```sql
EXPLAIN SELECT * FROM {{database}}.{{table}} WHERE {{pi_column}} = 123;

EXPLAIN SELECT * FROM {{database}}.events WHERE customer_id = 7;

EXPLAIN SELECT * FROM {{database}}.{{table}} WHERE {{column}} = 7;
```

### join_plans — Join methods and the row movement before them

What the four plans show:

- Tables with the same primary index (events and customers on customer_id) are joined "using a merge join" by "RowHash match scan", with no step that moves rows.
- A join column that is not the primary index of one table: that table's rows go into a spool "which is redistributed by the hash code of (customer_id) to all AMPs", followed by a SORT, then a merge join.
- A small table joined on a column that is no primary index is "duplicated on all AMPs" and joined "using a dynamic hash join".
- A join without a join condition is "joined using a product join, with a join condition of ("(1=1)")"; the estimate is the product of both row counts.

```sql
EXPLAIN
SELECT e.event_id, c.last_name
FROM {{database}}.events AS e
JOIN {{database}}.customers AS c ON c.customer_id = e.customer_id;

EXPLAIN
SELECT c.segment, COUNT(*) AS order_count, SUM(o.amount) AS revenue
FROM {{database}}.{{table}} AS o
JOIN {{database}}.customers AS c ON c.customer_id = o.{{column}}
WHERE o.order_date >= DATE '2025-01-01'
GROUP BY c.segment;

EXPLAIN
SELECT i.order_id, p.product_name
FROM {{database}}.order_items AS i
JOIN {{database}}.products AS p ON p.product_id = i.product_id;

EXPLAIN
SELECT o.order_id, c.last_name
FROM {{database}}.{{table}} AS o
CROSS JOIN {{database}}.customers AS c;
```

### collect_statistics — Statistics that turn "no confidence" into "high confidence"

Without statistics on the condition column the plan says "estimated with no confidence" and a row count from a sample (98 to 102 in different runs here); after COLLECT STATISTICS on that column it says "estimated with high confidence to be 106 rows", the true count. The example works on a copy so that the fixture table keeps no statistics; on a real table run only the COLLECT and EXPLAIN statements.

```sql
CREATE MULTISET TABLE {{database}}.{{stats_table}} AS (
  SELECT * FROM {{database}}.{{table}}
) WITH DATA
UNIQUE PRIMARY INDEX ({{pi_column}});

EXPLAIN SELECT * FROM {{database}}.{{stats_table}} WHERE {{column}} = 7;

-- One column
COLLECT STATISTICS COLUMN ({{column}}) ON {{database}}.{{stats_table}};

-- Columns that are filtered together
COLLECT STATISTICS COLUMN (status, channel) ON {{database}}.{{stats_table}};

-- The primary index
COLLECT STATISTICS INDEX ({{pi_column}}) ON {{database}}.{{stats_table}};

EXPLAIN SELECT * FROM {{database}}.{{stats_table}} WHERE {{column}} = 7;

-- What was collected
HELP STATISTICS {{database}}.{{stats_table}};
```

### skew_check — Rows per AMP for the primary index or a candidate column

HASHAMP(HASHBUCKET(HASHROW(col))) is the AMP a value hashes to, so grouping by it shows how a primary index on that column would spread the rows. HASHAMP() without an argument is the highest AMP number (3 on this 4-AMP system), not a per-row value. `amp` is a reserved word and needs double quotes as an alias (Error 3707).

```sql
-- The current primary index: even
SELECT HASHAMP(HASHBUCKET(HASHROW({{pi_column}}))) AS "amp", COUNT(*) AS row_count
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY row_count DESC;

-- A candidate column: one AMP gets more rows, because one value holds a tenth of the table
SELECT HASHAMP(HASHBUCKET(HASHROW({{column}}))) AS "amp", COUNT(*) AS row_count
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY row_count DESC;

-- The values behind the skew
SELECT TOP 5 {{column}}, COUNT(*) AS row_count
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY row_count DESC;

-- Number of AMPs
SELECT HASHAMP() + 1 AS amp_count;
```

## Common errors

- **"estimated with no confidence"** — no statistics on the columns of the condition. Collect statistics on them and explain again (see collect_statistics).
- **"product join ... (1=1)"** — the join has no condition, or the condition does not connect the two tables. Check the ON clause.
- **"all-rows scan" where one row is wanted** — the condition is not on the primary index. Filter on the primary index column, or accept the scan for a small table.
- **3707 near `amp`** — reserved word used as an alias. Write `"amp"`.
- **3706 / 3807 from EXPLAIN** — the statement itself is wrong; EXPLAIN reports the parser's error.
- **One row from a skew query** — it groups by `HASHAMP()` without an argument, which is a constant. Use HASHAMP(HASHBUCKET(HASHROW(column))).

## Practices

- Explain a statement before running it against large tables; read the steps in order and note each step's access path, row movement and confidence.
- Confidence phrases seen here: "no confidence" for a condition on a column without statistics; "low confidence" for a whole-table read or primary-index access without statistics; "high confidence" once statistics exist on the condition column; "index join confidence" for the result of a join on the primary index of one of the tables.
- A second condition on a column without statistics brings the estimate back to "no confidence"; collect statistics on every column that is filtered or joined, and on columns filtered together as one multi-column statistic.
- "(Last Use)" after a spool means the spool is released after that step.
- "eliminating duplicate rows" appears for UNION; a DISTINCT on one column is planned as a SUM step that groups by the column.
- "nested join" appears when one side is reduced to a single row by its unique primary index and the other is reached through its primary index.
- Compare a step's estimated row count with the real count (a COUNT(*) with the same condition). In the cross join above the customers table is estimated at 768 rows and holds 200.
- Choose a primary index whose HASHAMP distribution is even; a column with a dominant value puts that value's rows on one AMP.
