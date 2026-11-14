# Data profiling

Profile a table: row and duplicate counts, nulls and statistics per column (TD_ColumnSummary, TD_UnivariateStatistics), value frequencies, text patterns, CORR.

**Use for:** a first look at an unknown table; checking completeness, uniqueness and value ranges before modelling or after a load; comparing a source and a target table by their profile; recording a profile to compare against later.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to profile | `customers` |
| `numeric_column` | Numeric column to describe | `income` |
| `second_numeric_column` | Second numeric column, for correlation | `age` |
| `text_column` | Character column to describe; expected to be unique | `email` |
| `category_column` | Column with few distinct values | `city` |
| `sample_size` | Number of rows to profile instead of the whole table | `100` |
| `top_n` | Number of most frequent values to return | `10` |
| `history_table` | Name for a table that keeps profile snapshots | `profile_history` |

## Statements

### table_overview — Row count, duplicate rows, column list and size

`COUNT(DISTINCT *)` is refused (Error 3706). Count the rows of a `SELECT DISTINCT *` derived table instead. DBC.TableSizeV has one row per AMP, so the size is a SUM.

```sql
-- Rows, distinct rows, and rows that repeat another row in every column
SELECT
  COUNT(*) AS total_rows,
  d.distinct_rows,
  COUNT(*) - d.distinct_rows AS duplicate_rows
FROM {{database}}.{{table}}
CROSS JOIN (
  SELECT COUNT(*) AS distinct_rows
  FROM (SELECT DISTINCT * FROM {{database}}.{{table}}) AS x
) AS d
GROUP BY d.distinct_rows;

-- Columns in table order. ColumnType is a code: I integer, D decimal, CV varchar, CF char,
-- DA date, TS timestamp. For a view it is NULL; TD_ColumnSummary below reports a view's types.
SELECT ColumnName, ColumnType, ColumnLength, DecimalTotalDigits, DecimalFractionalDigits, Nullable
FROM DBC.ColumnsV
WHERE DatabaseName = '{{database}}' AND TableName = '{{table}}'
ORDER BY ColumnId;

-- Size on disk in bytes
SELECT SUM(CurrentPerm) AS current_perm_bytes
FROM DBC.TableSizeV
WHERE DatabaseName = '{{database}}' AND TableName = '{{table}}';
```

### all_columns_profile — Null, blank, zero, positive and negative counts for every column

TD_ColumnSummary returns one row per column: ColumnName, Datatype, NonNullCount, NullCount, BlankCount, ZeroCount, PositiveCount, NegativeCount, NullPercentage, NonNullPercentage. `'[:]'` means all columns; a list such as `'age','income'` limits it. It does not count distinct values; the second statement writes the statement that does.

```sql
SELECT s.*
FROM TD_ColumnSummary(
  ON {{database}}.{{table}} AS InputTable
  USING TargetColumns('[:]')
) AS s
ORDER BY s.NullPercentage DESC, s.ColumnName;

-- Generates one SELECT per column (joined by UNION ALL) that counts its distinct values.
-- Run the returned lines, in order, as one statement.
SELECT
  'SELECT ''' || TRIM(ColumnName) || ''' AS column_name, COUNT(*) AS total_rows, COUNT("'
  || TRIM(ColumnName) || '") AS non_null_rows, COUNT(DISTINCT "' || TRIM(ColumnName)
  || '") AS distinct_values FROM ' || TRIM(DatabaseName) || '.' || TRIM(TableName)
  || CASE WHEN ROW_NUMBER() OVER (ORDER BY ColumnId DESC) = 1 THEN ';' ELSE ' UNION ALL' END AS profile_sql
FROM DBC.ColumnsV
WHERE DatabaseName = '{{database}}' AND TableName = '{{table}}'
ORDER BY ColumnId;
```

### column_statistics — Completeness, uniqueness and distribution of one numeric column

AVG and STDDEV_POP on a character column fail with Error 2621; MIN and MAX also work on character columns. A percentage written `n * 100.0 / d` keeps one decimal place, whatever the CAST around it says; write `100.00` for two. TD_UnivariateStatistics returns one row per column and statistic (ATTRIBUTE, StatName, StatValue): mean, median, mode, percentiles, standard deviation, skewness, kurtosis and more.

```sql
SELECT
  COUNT(*) AS total_rows,
  COUNT({{numeric_column}}) AS non_null_rows,
  COUNT(*) - COUNT({{numeric_column}}) AS null_rows,
  CAST((COUNT(*) - COUNT({{numeric_column}})) * 100.00 / COUNT(*) AS DECIMAL(5,2)) AS null_pct,
  COUNT(DISTINCT {{numeric_column}}) AS distinct_values,
  CAST(COUNT(DISTINCT {{numeric_column}}) * 100.00 / NULLIF(COUNT({{numeric_column}}), 0) AS DECIMAL(5,2)) AS distinct_pct,
  MIN({{numeric_column}}) AS min_value,
  MAX({{numeric_column}}) AS max_value,
  AVG({{numeric_column}}) AS avg_value,
  STDDEV_POP({{numeric_column}}) AS stddev_value,
  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {{numeric_column}}) AS p25,
  MEDIAN({{numeric_column}}) AS median_value,
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {{numeric_column}}) AS p75
FROM {{database}}.{{table}};

-- Every statistic for several numeric columns at once
SELECT u.*
FROM TD_UnivariateStatistics(
  ON {{database}}.{{table}} AS InputTable
  USING TargetColumns('{{numeric_column}}', '{{second_numeric_column}}') Stats('ALL')
) AS u
ORDER BY u."ATTRIBUTE", u.StatName;
```

### sampled_statistics — Profile a random sample of a large table

SAMPLE is applied after aggregation: `SELECT COUNT(*) FROM t SAMPLE 100` still counts every row. Take the sample in a derived table and aggregate outside it. The rows are chosen at random, so the figures differ from run to run; a sample size above the row count returns every row.

```sql
SELECT
  COUNT(*) AS sampled_rows,
  COUNT({{numeric_column}}) AS non_null_rows,
  AVG({{numeric_column}}) AS avg_value,
  MEDIAN({{numeric_column}}) AS median_value,
  COUNT(DISTINCT {{category_column}}) AS distinct_categories
FROM (
  SELECT * FROM {{database}}.{{table}} SAMPLE {{sample_size}}
) AS s;
```

### value_frequency — Most frequent values of a column with share and running share

The second ORDER BY column makes the running total repeatable when two values have the same count.

```sql
SELECT TOP {{top_n}}
  {{category_column}},
  COUNT(*) AS frequency,
  CAST(COUNT(*) * 100.00 / SUM(COUNT(*)) OVER () AS DECIMAL(5,2)) AS pct,
  CAST(SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC, {{category_column}} ROWS UNBOUNDED PRECEDING) * 100.00
       / SUM(COUNT(*)) OVER () AS DECIMAL(5,2)) AS cumulative_pct
FROM {{database}}.{{table}}
WHERE {{category_column}} IS NOT NULL
GROUP BY {{category_column}}
ORDER BY frequency DESC, {{category_column}};
```

### text_patterns — What a character column contains: integers, decimals, dates, emails, lengths

REGEXP_SIMILAR returns 1 or 0, so compare it with 1. Without the third argument `'i'` the match is case sensitive.

```sql
SELECT
  COUNT(*) AS total_rows,
  COUNT(*) - COUNT({{text_column}}) AS null_rows,
  SUM(CASE WHEN TRIM({{text_column}}) = '' THEN 1 ELSE 0 END) AS blank_rows,
  SUM(CASE WHEN REGEXP_SIMILAR({{text_column}}, '^-?[0-9]+$') = 1 THEN 1 ELSE 0 END) AS integer_rows,
  SUM(CASE WHEN REGEXP_SIMILAR({{text_column}}, '^-?[0-9]+\.[0-9]+$') = 1 THEN 1 ELSE 0 END) AS decimal_rows,
  SUM(CASE WHEN REGEXP_SIMILAR({{text_column}}, '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') = 1 THEN 1 ELSE 0 END) AS iso_date_rows,
  SUM(CASE WHEN REGEXP_SIMILAR({{text_column}}, '^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$', 'i') = 1 THEN 1 ELSE 0 END) AS email_rows,
  MIN(CHARACTER_LENGTH({{text_column}})) AS min_length,
  MAX(CHARACTER_LENGTH({{text_column}})) AS max_length,
  CAST(AVG(CHARACTER_LENGTH({{text_column}})) AS DECIMAL(8,1)) AS avg_length
FROM {{database}}.{{table}};
```

### correlation — Pearson correlation between two numeric columns

CORR ignores a row in which either column is NULL; `pairs` shows how many rows were used.

```sql
SELECT
  c.correlation,
  c.pairs,
  CASE
    WHEN ABS(c.correlation) > 0.8 THEN 'strong'
    WHEN ABS(c.correlation) > 0.5 THEN 'moderate'
    WHEN ABS(c.correlation) > 0.3 THEN 'weak'
    ELSE 'very weak'
  END AS strength
FROM (
  SELECT
    CORR({{numeric_column}}, {{second_numeric_column}}) AS correlation,
    COUNT(*) AS pairs
  FROM {{database}}.{{table}}
  WHERE {{numeric_column}} IS NOT NULL AND {{second_numeric_column}} IS NOT NULL
) AS c;
```

### data_quality_score — Completeness over all cells and uniqueness of a key column

Completeness is the share of non-null cells over every column. Uniqueness is distinct values over non-null values of the column that should be unique.

```sql
SELECT
  n.total_cells,
  n.null_cells,
  CAST(100.00 - n.null_cells * 100.00 / n.total_cells AS DECIMAL(5,2)) AS completeness_score,
  CAST(k.distinct_keys * 100.00 / NULLIF(k.non_null_keys, 0) AS DECIMAL(5,2)) AS uniqueness_score,
  CAST(((100.00 - n.null_cells * 100.00 / n.total_cells)
        + k.distinct_keys * 100.00 / NULLIF(k.non_null_keys, 0)) / 2 AS DECIMAL(5,2)) AS overall_score
FROM (
  SELECT SUM(NullCount) AS null_cells, SUM(NullCount + NonNullCount) AS total_cells
  FROM TD_ColumnSummary(
    ON {{database}}.{{table}} AS InputTable
    USING TargetColumns('[:]')
  ) AS s
) AS n
CROSS JOIN (
  SELECT COUNT(DISTINCT {{text_column}}) AS distinct_keys, COUNT({{text_column}}) AS non_null_keys
  FROM {{database}}.{{table}}
) AS k;
```

### profile_snapshot — Keep each profile to compare runs over time

The table is created once; the INSERT is the statement to repeat after each load.

```sql
CREATE MULTISET TABLE {{database}}.{{history_table}} AS (
  SELECT
    CURRENT_DATE AS profile_date,
    CAST('{{database}}.{{table}}' AS VARCHAR(260)) AS table_name,
    s.*
  FROM TD_ColumnSummary(
    ON {{database}}.{{table}} AS InputTable
    USING TargetColumns('[:]')
  ) AS s
) WITH NO DATA
PRIMARY INDEX (table_name, ColumnName);

INSERT INTO {{database}}.{{history_table}}
SELECT CURRENT_DATE, '{{database}}.{{table}}', s.*
FROM TD_ColumnSummary(
  ON {{database}}.{{table}} AS InputTable
  USING TargetColumns('[:]')
) AS s;

-- Null share of each column per snapshot, for the columns that have nulls
SELECT profile_date, ColumnName, NullCount, NullPercentage
FROM {{database}}.{{history_table}}
WHERE table_name = '{{database}}.{{table}}' AND NullCount > 0
ORDER BY ColumnName, profile_date;
```

## Common errors

- **3706 "expected something between the 'DISTINCT' keyword and '*'"** — `COUNT(DISTINCT *)`. Count the rows of `(SELECT DISTINCT * FROM t)`.
- **3707 "expected something like a name ... between the 'AS' keyword and the 'value' keyword"** — a reserved word as an alias (observed for value, count, user, type, date). Double-quote it or choose another name.
- **2621 "Bad character in format or data"** — AVG or STDDEV on a character column. Use them on numeric columns only; MIN and MAX work on character columns.
- **3706 on `SAMPLE 0.1 PERCENT`** — there is no PERCENT keyword. `SAMPLE 0.1` is a tenth of the rows, `SAMPLE 100` is 100 rows.
- **A percentage shows one decimal place (96.30 for 96.28)** — `n * 100.0 / d` has scale 1 before the CAST. Multiply by `100.00`.
- **A sampled aggregate returns the full-table figure** — `SELECT COUNT(*) FROM t SAMPLE 100` samples the one result row. Sample in a derived table.
- **3707 on `WHERE NOT REGEXP_SIMILAR(...)`** — the function returns a number, not a condition. Write `REGEXP_SIMILAR(...) = 0`.
- **A pattern count is 0 on mixed-case data** — REGEXP_SIMILAR is case sensitive unless the third argument is `'i'`.

## Practices

- Start with table_overview and all_columns_profile, then look closer at the columns that stand out.
- For each column ask: how much is NULL, how many distinct values, does the content match the declared type, is the range plausible.
- A view is profiled like a table (TD_ColumnSummary accepts one), but a problem found in it has to be corrected in the base table it reads.
- Sample for a first look at a large table. Use the whole table when a count has to be exact or when rare nulls matter.
- Profile the source before a load and the target after it, and compare row count, null share and value range per column.
- Store snapshots and compare them; a null share or distinct count that moves between loads is a finding.
- Read a correlation together with `pairs`: rows with a NULL in either column are left out.
