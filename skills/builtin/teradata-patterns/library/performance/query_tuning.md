# Query tuning

Plan-changing rewrites: predicates that keep primary index and partition access, matching join key types, pre-aggregation, volatile and NoPI tables, CTEs.

**Use for:** a query that scans all rows although it filters on the primary index or the partitioning column; a join that redistributes or duplicates a table unexpectedly; choosing a primary index; staging a load; breaking a long query into steps. Read the plan with EXPLAIN before and after each rewrite.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the tables | `retail_demo` |
| `table` | Table to tune | `orders` |
| `pi_columns` | Primary index columns, or candidate columns, comma-separated | `customer_id` |
| `index_column` | Column of the table's primary index | `order_id` |
| `index_value` | A value to look up by primary index | `100` |
| `date_column` | Date column used for filtering and partitioning | `order_date` |
| `from_date` | First day of the date range | `2025-06-01` |
| `to_date` | Last day of the date range | `2025-06-30` |
| `dim_table` | Table joined to `table` | `customers` |
| `join_column` | Join column, present in both tables | `customer_id` |
| `measure_column` | Numeric column to aggregate | `amount` |

## Statements

### pi_skew_check — Rows per AMP for a primary index or a candidate

`HASHAMP(HASHBUCKET(HASHROW(cols)))` is the AMP a row goes to when `cols` is the primary index. Grouping by `HASHAMP()` without an argument does not work: it is the constant "highest AMP number" and returns one row. A max_to_avg near 1.0 is an even distribution; see the pi_skew_detection section for the full analysis.

```sql
SELECT
  HASHAMP() + 1 AS total_amps,
  COUNT(*) AS amps_with_rows,
  MIN(row_count) AS min_rows,
  MAX(row_count) AS max_rows,
  CAST(CAST(SUM(row_count) AS FLOAT) / (HASHAMP() + 1) AS DECIMAL(18,1)) AS avg_rows,
  CAST(MAX(row_count) * (HASHAMP() + 1) / CAST(SUM(row_count) AS FLOAT) AS DECIMAL(9,2)) AS max_to_avg
FROM (
  SELECT HASHAMP(HASHBUCKET(HASHROW({{pi_columns}}))) AS amp_no, COUNT(*) AS row_count
  FROM {{database}}.{{table}}
  GROUP BY 1
) AS amp_rows;

-- The 20 fullest AMPs
SELECT TOP 20 HASHAMP(HASHBUCKET(HASHROW({{pi_columns}}))) AS amp_no, COUNT(*) AS row_count
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY row_count DESC;
```

### filter_before_join — Filter and aggregate one side before the join

For an inner join the optimizer applies a WHERE condition on one table before the join by itself; the first EXPLAIN shows the condition inside the retrieve step, and a derived table that only filters gives the same plan. What a derived table changes is aggregation: grouping inside it reduces the rows before they are redistributed for the join (EXPLAIN of the second statement shows the aggregation step before the join step).

```sql
-- Filter in WHERE: the condition is already applied in the retrieve step
EXPLAIN
SELECT o.order_id, o.{{measure_column}}, d.last_name
FROM {{database}}.{{table}} AS o
JOIN {{database}}.{{dim_table}} AS d
  ON o.{{join_column}} = d.{{join_column}}
WHERE o.{{date_column}} >= DATE '{{from_date}}';

-- Filter, keep only the needed columns and aggregate, then join
SELECT d.{{join_column}}, d.last_name, o.row_count, o.total
FROM (
  SELECT {{join_column}}, COUNT(*) AS row_count, SUM({{measure_column}}) AS total
  FROM {{database}}.{{table}}
  WHERE {{date_column}} >= DATE '{{from_date}}'
  GROUP BY {{join_column}}
) AS o
JOIN {{database}}.{{dim_table}} AS d
  ON o.{{join_column}} = d.{{join_column}}
ORDER BY o.total DESC;
```

### anti_pattern_function_on_index — Predicates that keep or lose primary index and partition access

Observed plans on this system. Primary index: `col = 100`, `col + 0 = 100` and `col = '100'` all give a single-AMP retrieve by the primary index; `CAST(col AS VARCHAR(10)) = '100'` gives an all-AMPs all-rows scan. Partitioned table: a range on the date column and `EXTRACT(YEAR/MONTH FROM col) = n` both read only the matching partitions; `TO_CHAR(col, 'YYYY-MM') = '2025-06'` and `ADD_MONTHS(col, 1) >= DATE '...'` scan all rows. A function on the literal side (`col >= ADD_MONTHS(DATE '...', -1)`) keeps partition elimination.

```sql
-- Single-AMP access by the primary index
EXPLAIN SELECT * FROM {{database}}.{{table}} WHERE {{index_column}} = {{index_value}};

-- The same lookup with the column converted: all-rows scan
EXPLAIN SELECT * FROM {{database}}.{{table}} WHERE CAST({{index_column}} AS VARCHAR(10)) = '{{index_value}}';

-- A copy partitioned by month, to compare predicates on the partitioning column
CREATE MULTISET TABLE {{database}}.{{table}}_by_month AS (
  SELECT * FROM {{database}}.{{table}}
) WITH DATA
PRIMARY INDEX ({{index_column}})
PARTITION BY RANGE_N({{date_column}} BETWEEN DATE '2024-01-01' AND DATE '2026-12-31' EACH INTERVAL '1' MONTH);

-- Text conversion of the column: "all-rows scan"
EXPLAIN SELECT COUNT(*) FROM {{database}}.{{table}}_by_month
WHERE TO_CHAR({{date_column}}, 'YYYY-MM') = '2025-06';

-- Range on the bare column: "a single partition"
EXPLAIN SELECT COUNT(*) FROM {{database}}.{{table}}_by_month
WHERE {{date_column}} BETWEEN DATE '{{from_date}}' AND DATE '{{to_date}}';

SELECT COUNT(*) AS rows_in_range FROM {{database}}.{{table}}_by_month
WHERE {{date_column}} BETWEEN DATE '{{from_date}}' AND DATE '{{to_date}}';
```

### anti_pattern_type_mismatch — Join keys of different types

Rows are co-located for a join only when both keys produce the same row hash. The first statement shows which types do: INTEGER, BIGINT and DECIMAL(n,0) hash alike, CHAR and VARCHAR hash alike; DECIMAL with a scale, FLOAT and character text of a number each hash differently. A character key joined to an integer key is converted to FLOAT for every row, and the plan duplicates or redistributes a table instead of joining on the primary indexes (compare the two EXPLAINs).

```sql
-- Which types hash alike
SELECT
  HASHROW(CAST(5 AS INTEGER)) AS h_integer,
  HASHROW(CAST(5 AS BIGINT)) AS h_bigint,
  HASHROW(CAST(5 AS DECIMAL(10,0))) AS h_decimal_0,
  HASHROW(CAST(5 AS DECIMAL(10,2))) AS h_decimal_2,
  HASHROW(CAST(5 AS FLOAT)) AS h_float,
  HASHROW('5') AS h_text,
  HASHROW(CAST('ab' AS CHAR(10))) AS h_char,
  HASHROW(CAST('ab' AS VARCHAR(50))) AS h_varchar;

-- Declared types of the join column in both tables
SELECT TableName, ColumnName, ColumnType, ColumnLength, DecimalTotalDigits, DecimalFractionalDigits
FROM DBC.ColumnsV
WHERE DatabaseName = '{{database}}'
  AND TableName IN ('{{table}}', '{{dim_table}}')
  AND ColumnName = '{{join_column}}'
ORDER BY TableName;

-- A copy whose key is stored as text, with that key as primary index
CREATE VOLATILE TABLE tmp_text_keys AS (
  SELECT {{index_column}}, TRIM(CAST({{join_column}} AS VARCHAR(20))) AS text_key
  FROM {{database}}.{{table}}
) WITH DATA
PRIMARY INDEX (text_key)
ON COMMIT PRESERVE ROWS;

-- Text key joined to an integer primary index: conversion to FLOAT, duplicated spool, hash join
EXPLAIN
SELECT t.{{index_column}}, d.last_name
FROM tmp_text_keys AS t
JOIN {{database}}.{{dim_table}} AS d
  ON t.text_key = d.{{join_column}};

-- Keys of the same type on both primary indexes: merge join without redistribution
EXPLAIN
SELECT e.event_id, d.last_name
FROM {{database}}.events AS e
JOIN {{database}}.{{dim_table}} AS d
  ON e.{{join_column}} = d.{{join_column}};
```

### volatile_table — Keep an intermediate result for the session

Choose the primary index for the joins that follow. Statistics can be collected on a volatile table; they are listed by HELP STATISTICS, not in DBC.StatsV.

```sql
CREATE VOLATILE TABLE tmp_{{table}}_filtered AS (
  SELECT {{index_column}}, {{join_column}}, {{date_column}}, {{measure_column}}
  FROM {{database}}.{{table}}
  WHERE {{date_column}} >= DATE '{{from_date}}'
) WITH DATA
PRIMARY INDEX ({{join_column}})
ON COMMIT PRESERVE ROWS;

COLLECT STATISTICS COLUMN ({{join_column}}) ON tmp_{{table}}_filtered;

HELP STATISTICS tmp_{{table}}_filtered;

-- Later statements of the session read the volatile table
SELECT d.state, COUNT(*) AS row_count, SUM(f.{{measure_column}}) AS total
FROM tmp_{{table}}_filtered AS f
JOIN {{database}}.{{dim_table}} AS d
  ON f.{{join_column}} = d.{{join_column}}
GROUP BY d.state
ORDER BY total DESC;
```

### nopi_staging — Load into a table without a primary index, then move to the indexed table

NO PRIMARY INDEX goes after WITH [NO] DATA; written after the table name it is a syntax error (3707). A NoPI table is not hashed on any column, so every lookup is an all-rows scan; use it only to land data.

```sql
-- Empty staging table with the columns of the target
CREATE MULTISET TABLE {{database}}.stg_{{table}} AS (
  SELECT * FROM {{database}}.{{table}}
) WITH NO DATA
NO PRIMARY INDEX;

-- The target with its primary index
CREATE MULTISET TABLE {{database}}.{{table}}_final AS (
  SELECT * FROM {{database}}.{{table}}
) WITH NO DATA
PRIMARY INDEX ({{index_column}});

-- Land the rows (a load utility or INSERT ... SELECT)
INSERT INTO {{database}}.stg_{{table}}
SELECT * FROM {{database}}.{{table}};

-- Redistribute into the indexed table
INSERT INTO {{database}}.{{table}}_final
SELECT * FROM {{database}}.stg_{{table}};

SELECT COUNT(*) AS rows_loaded FROM {{database}}.{{table}}_final;

DROP TABLE {{database}}.stg_{{table}};
```

### cte_refactor — Nested derived tables written as a WITH clause

This query written as nested derived tables (`FROM (SELECT ... FROM (SELECT ...) AS filtered GROUP BY ...) AS aggregated`) produces the same EXPLAIN steps. A CTE is not stored: a CTE referenced twice in one statement was aggregated twice in the plan. When a result must be computed once and read several times, put it in a volatile table.

```sql
WITH filtered AS (
  SELECT {{join_column}}, {{measure_column}}
  FROM {{database}}.{{table}}
  WHERE {{date_column}} >= DATE '{{from_date}}'
),
aggregated AS (
  SELECT {{join_column}}, SUM({{measure_column}}) AS total
  FROM filtered
  GROUP BY {{join_column}}
)
SELECT *
FROM aggregated
WHERE total > 500
ORDER BY total DESC;
```

## Common errors

- **5735 "The primary index columns may not be altered for a nonempty table"** — ALTER TABLE ... MODIFY PRIMARY INDEX works only on an empty table. Create a new table with the new index, INSERT ... SELECT into it, and rename.
- **3707 on `CREATE TABLE t, NO PRIMARY INDEX AS (...)`** — put NO PRIMARY INDEX after WITH DATA or WITH NO DATA.
- **3706 on `FETCH FIRST n ROWS ONLY`** — not accepted here. Use `SELECT TOP n`.
- **`GROUP BY HASHAMP()` returns one row** — HASHAMP() without an argument is a constant. Group by `HASHAMP(HASHBUCKET(HASHROW(cols)))`.
- **All-rows scan although the WHERE names the primary index column** — the column is wrapped in a conversion (CAST to text, TO_CHAR). Compare the bare column with a value of the column's type.

## Practices

- Choose a primary index with many distinct values that is used in equality conditions and joins. With a multi-column index, single-AMP access needs an equality condition on every index column.
- Compare a partitioning date column with a range of DATE literals. Put functions on the literal side, not on the column.
- Give join keys the same type in both tables. When the types differ, fix the table definition; a CAST in the join condition still converts every row.
- Select only the columns the query needs; every selected column is carried through each spool.
- Aggregate in a derived table before joining when the grouped result is much smaller than the join input.
- Use a volatile table when several statements of a session read the same intermediate result, and collect statistics on its join column.
- Stage loads in a NoPI table and INSERT ... SELECT into the indexed target.
- Use WITH for readability; it plans like a derived table and is evaluated at each reference.
- Run EXPLAIN before and after a rewrite and compare the retrieve and join steps. Collect statistics on the columns in WHERE, JOIN and GROUP BY before judging a plan (see the statistics_collection section).
