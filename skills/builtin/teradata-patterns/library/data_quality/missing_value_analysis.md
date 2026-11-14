# Missing value analysis and imputation

Count NULLs per column, find missing-value patterns by row, period and group, and fill them on a copy with mean, median, mode or LAST_VALUE IGNORE NULLS.

**Use for:** a completeness report on a table; deciding whether NULLs cluster in certain rows, periods or groups; preparing a column for a model (indicator flags, mean, median or mode fill); filling gaps in an ordered series with the last known value.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to analyse | `customers` |
| `id_column` | Column that identifies a row | `customer_id` |
| `column` | Numeric column with NULLs; the one that is filled | `income` |
| `column_b` | Second column to include in reports | `email` |
| `column_c` | Third column to include in reports | `phone` |
| `mode_column` | Discrete column to fill with its most frequent value | `age` |
| `compare_column` | Numeric column compared between rows with and without a value | `age` |
| `group_column` | Column that splits the rows into groups | `state` |
| `date_column` | DATE column that places a row in time | `signup_date` |
| `trunc_unit` | TRUNC unit for the period: `MM` month, `Q` quarter, `YYYY` year, `IW` ISO week | `MM` |
| `new_table` | Name for the copy that receives the filled values | `customers_imputed` |

## Statements

### nullable_columns — Which columns of the table can hold NULL

Only columns with Nullable = 'Y' need a completeness check. DatabaseName and TableName compare case-insensitively in a Teradata-mode session.

```sql
SELECT ColumnName, ColumnType, Nullable
FROM DBC.ColumnsV
WHERE DatabaseName = '{{database}}'
  AND TableName = '{{table}}'
  AND Nullable = 'Y'
ORDER BY ColumnId;
```

### completeness_report — NULL count, percentage and status per column

`COUNT(column)` counts the non-NULL values. Add one UNION ALL branch per column. The first branch fixes the type of the label column, so cast it; without the cast `'income'` after `'email'` comes back as `'incom'`.

```sql
SELECT
  column_name,
  total_rows,
  total_rows - non_null_count AS null_count,
  CAST((total_rows - non_null_count) * 100.0 / total_rows AS DECIMAL(5,2)) AS null_percentage,
  CASE
    WHEN total_rows = non_null_count THEN 'Complete'
    WHEN (total_rows - non_null_count) * 100.0 / total_rows < 5 THEN 'Mostly complete'
    WHEN (total_rows - non_null_count) * 100.0 / total_rows < 30 THEN 'Incomplete'
    ELSE 'Severely incomplete'
  END AS completeness_status
FROM (
  SELECT CAST('{{column}}' AS VARCHAR(128)) AS column_name,
         COUNT(*) AS total_rows, COUNT({{column}}) AS non_null_count
  FROM {{database}}.{{table}}
  UNION ALL
  SELECT '{{column_b}}', COUNT(*), COUNT({{column_b}}) FROM {{database}}.{{table}}
  UNION ALL
  SELECT '{{column_c}}', COUNT(*), COUNT({{column_c}}) FROM {{database}}.{{table}}
) AS c
ORDER BY null_percentage DESC;
```

### missing_pattern_analysis — Which columns are missing together

The first statement counts the rows for each combination of missing columns. The second gives the share of rows with any and with all of the columns missing.

```sql
SELECT
  CASE WHEN {{column}} IS NULL THEN 1 ELSE 0 END AS {{column}}_missing,
  CASE WHEN {{column_b}} IS NULL THEN 1 ELSE 0 END AS {{column_b}}_missing,
  CASE WHEN {{column_c}} IS NULL THEN 1 ELSE 0 END AS {{column_c}}_missing,
  COUNT(*) AS pattern_count,
  CAST(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS DECIMAL(5,2)) AS percentage
FROM {{database}}.{{table}}
GROUP BY 1, 2, 3
ORDER BY pattern_count DESC;

SELECT
  COUNT(*) AS total_rows,
  SUM(CASE WHEN {{column}} IS NULL OR {{column_b}} IS NULL OR {{column_c}} IS NULL THEN 1 ELSE 0 END) AS rows_any_missing,
  SUM(CASE WHEN {{column}} IS NULL AND {{column_b}} IS NULL AND {{column_c}} IS NULL THEN 1 ELSE 0 END) AS rows_all_missing,
  CAST(SUM(CASE WHEN {{column}} IS NULL OR {{column_b}} IS NULL OR {{column_c}} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS pct_any_missing
FROM {{database}}.{{table}};
```

### temporal_missing_analysis — NULL rate per period

There is no DATE_TRUNC (Error 3706). `TRUNC(date, unit)` returns the first day of the period.

```sql
SELECT
  TRUNC({{date_column}}, '{{trunc_unit}}') AS period_start,
  COUNT(*) AS total_rows,
  COUNT(*) - COUNT({{column}}) AS null_count,
  CAST((COUNT(*) - COUNT({{column}})) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS null_percentage
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY 1;
```

### missing_value_correlation — Does missingness depend on another column

The first statement compares a numeric column between the rows with and without a value. The second gives the NULL rate per group. Clear differences mean the values are not missing at random, and a single fill value for the whole table will bias the groups.

```sql
SELECT
  CASE WHEN {{column}} IS NULL THEN 1 ELSE 0 END AS is_missing,
  COUNT(*) AS row_count,
  AVG({{compare_column}}) AS avg_compare_value,
  MEDIAN({{compare_column}}) AS median_compare_value
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY 1;

SELECT
  {{group_column}},
  COUNT(*) AS total_rows,
  COUNT(*) - COUNT({{column}}) AS null_count,
  CAST((COUNT(*) - COUNT({{column}})) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS null_percentage
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY null_percentage DESC;
```

### create_missing_indicators — Flag column and zero-filled column for a model

```sql
SELECT
  {{id_column}},
  {{column}},
  CASE WHEN {{column}} IS NULL THEN 1 ELSE 0 END AS {{column}}_is_missing,
  COALESCE({{column}}, 0) AS {{column}}_filled_zero
FROM {{database}}.{{table}}
ORDER BY {{id_column}};
```

### impute_with_mean — Fill NULLs with the column average, on a copy

Each imputation statement builds its own copy with a flag that records which rows were filled; drop the copy before running another one. A plain aggregate such as AVG is accepted as a scalar subquery in SET.

```sql
CREATE MULTISET TABLE {{database}}.{{new_table}} AS (
  SELECT t.*, CASE WHEN {{column}} IS NULL THEN 1 ELSE 0 END AS {{column}}_was_missing
  FROM {{database}}.{{table}} AS t
) WITH DATA
PRIMARY INDEX ({{id_column}});

UPDATE {{database}}.{{new_table}}
SET {{column}} = (SELECT AVG({{column}}) FROM {{database}}.{{table}})
WHERE {{column}} IS NULL;

SELECT {{column}}_was_missing, COUNT(*) AS row_count,
       MIN({{column}}) AS min_value, MAX({{column}}) AS max_value
FROM {{database}}.{{new_table}}
GROUP BY 1
ORDER BY 1;
```

### impute_with_median — Fill NULLs with the column median, on a copy

MEDIAN and PERCENTILE_CONT are refused in a subquery (Error 3706), so the fill value comes from a derived table in a joined UPDATE. `MEDIAN(x)` and `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x)` return the same value.

```sql
CREATE MULTISET TABLE {{database}}.{{new_table}} AS (
  SELECT t.*, CASE WHEN {{column}} IS NULL THEN 1 ELSE 0 END AS {{column}}_was_missing
  FROM {{database}}.{{table}} AS t
) WITH DATA
PRIMARY INDEX ({{id_column}});

UPDATE c
FROM {{database}}.{{new_table}} AS c,
     (SELECT MEDIAN({{column}}) AS fill_value FROM {{database}}.{{table}}) AS s
SET {{column}} = s.fill_value
WHERE c.{{column}} IS NULL;

SELECT {{column}}_was_missing, COUNT(*) AS row_count,
       MIN({{column}}) AS min_value, MAX({{column}}) AS max_value
FROM {{database}}.{{new_table}}
GROUP BY 1
ORDER BY 1;
```

### impute_with_mode — Fill NULLs with the most frequent value, on a copy

TOP is refused in a subquery (Error 6916) but accepted in a derived table. The second ORDER BY key decides between values that are equally frequent, so the result is repeatable.

```sql
CREATE MULTISET TABLE {{database}}.{{new_table}} AS (
  SELECT t.*, CASE WHEN {{mode_column}} IS NULL THEN 1 ELSE 0 END AS {{mode_column}}_was_missing
  FROM {{database}}.{{table}} AS t
) WITH DATA
PRIMARY INDEX ({{id_column}});

UPDATE c
FROM {{database}}.{{new_table}} AS c,
     (SELECT TOP 1 {{mode_column}} AS fill_value
      FROM {{database}}.{{table}}
      WHERE {{mode_column}} IS NOT NULL
      GROUP BY {{mode_column}}
      ORDER BY COUNT(*) DESC, {{mode_column}}) AS s
SET {{mode_column}} = s.fill_value
WHERE c.{{mode_column}} IS NULL;

SELECT {{mode_column}}, COUNT(*) AS row_count, SUM({{mode_column}}_was_missing) AS filled_rows
FROM {{database}}.{{new_table}}
GROUP BY 1
HAVING SUM({{mode_column}}_was_missing) > 0;
```

### forward_fill — Fill NULLs with the last known value in order, on a copy

`LAST_VALUE(x IGNORE NULLS)` over the rows up to the current one carries the last non-NULL value forward within each group. A row with no earlier value in its group stays NULL, as the last statement shows. For a backward fill use `FIRST_VALUE(x IGNORE NULLS)` with `ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING`.

```sql
CREATE MULTISET TABLE {{database}}.{{new_table}} AS (
  SELECT t.*, CASE WHEN {{column}} IS NULL THEN 1 ELSE 0 END AS {{column}}_was_missing
  FROM {{database}}.{{table}} AS t
) WITH DATA
PRIMARY INDEX ({{id_column}});

UPDATE c
FROM {{database}}.{{new_table}} AS c,
     (SELECT {{id_column}},
             LAST_VALUE({{column}} IGNORE NULLS) OVER (
               PARTITION BY {{group_column}}
               ORDER BY {{date_column}}, {{id_column}}
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
             ) AS fill_value
      FROM {{database}}.{{table}}) AS f
SET {{column}} = f.fill_value
WHERE c.{{id_column}} = f.{{id_column}}
  AND c.{{column}} IS NULL;

SELECT COUNT(*) AS total_rows,
       SUM({{column}}_was_missing) AS rows_missing_before,
       COUNT(*) - COUNT({{column}}) AS rows_still_missing
FROM {{database}}.{{new_table}};
```

## Common errors

- **3706 "Order-based Aggregate and Ordered Analytical Functions are not allowed in subqueries"** — MEDIAN, PERCENTILE_CONT or a QUALIFY window inside a scalar subquery, for example in `SET x = (SELECT MEDIAN(x) ...)`. Move it to a derived table and use `UPDATE t FROM table AS t, (SELECT ...) AS s SET ...`.
- **6916 "Top N option is not supported in subquery"** — `(SELECT TOP 1 ...)` as a scalar subquery. Put it in a derived table.
- **3706 near LIMIT** — the most frequent value is `SELECT TOP 1 ... ORDER BY COUNT(*) DESC`, not `LIMIT 1`.
- **3706 on DATE_TRUNC** — use `TRUNC(date_column, 'MM')`.
- **3707 on `AS count` or `AS value`** — reserved words. Use another alias or double-quote it.
- **3803 "Table already exists"** — the copy from an earlier imputation statement is still there. Drop it or pick another name.
- **A label cut short in a UNION ALL report** — the first SELECT fixes the column type. Cast the first label to a VARCHAR long enough for all of them.

## Practices

- Look at the patterns first (by row combination, period and group) and choose the fill afterwards. A column whose NULL rate differs by group or period is not missing at random.
- Fill on a copy or into a new column and keep a flag for the filled rows, so the original values and the filled rows stay identifiable.
- Prefer the median to the mean for a column with extreme values; the mean is pulled toward them.
- Every filled row gets the same value with mean, median and mode, which lowers the variance of the column. Keep the flag column as a model feature when missingness may carry information.
- For model training compute the fill value from the training rows only: add the WHERE condition to the derived table that computes it.
- A forward fill needs a deterministic order. Add the row identifier as the last ORDER BY key, and PARTITION BY the entity the series belongs to.
- Do not fill a column that is mostly NULL, or one where NULL has a meaning of its own (not applicable, no purchase); report it instead.
