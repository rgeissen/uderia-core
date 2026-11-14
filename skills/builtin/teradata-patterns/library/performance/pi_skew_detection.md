# Primary index skew detection

Measure how a primary index, or a candidate column list, spreads rows across AMPs with HASHAMP/HASHBUCKET/HASHROW, DBC.IndicesV and DBC.TableSizeV.

**Use for:** finding out whether a slow or spool-heavy table is unevenly distributed; testing a candidate primary index before creating the table; finding the value (often NULL or a default) that causes the skew; ranking the tables of a database by space skew after a load.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to analyze | `orders` |
| `pi_columns` | Primary index columns, or candidate columns, comma-separated | `customer_id` |
| `threshold` | Skew percentage above which a distribution is reported as skewed | `10` |

## Statements

### primary_index_columns — The columns of the table's primary index

IndexType is `P` for a primary index and `Q` for a partitioned one; UniqueFlag `Y` marks a unique index, which cannot skew. A table created with NO PRIMARY INDEX has no row here. Use the columns returned as `pi_columns` in the statements below, or pass a candidate list instead.

```sql
SELECT TableName, IndexNumber, IndexType, UniqueFlag, ColumnPosition, ColumnName
FROM DBC.IndicesV
WHERE DatabaseName = '{{database}}'
  AND TableName = '{{table}}'
  AND IndexType IN ('P', 'Q')
ORDER BY IndexNumber, ColumnPosition;
```

### pi_skew_analysis — Rows per AMP and the skew percentage for a column list

`HASHAMP(HASHBUCKET(HASHROW(cols)))` is the AMP a row goes to when `cols` is the primary index, so the statements work for the real index and for a candidate, on a table or a view. `HASHAMP()` without an argument is the highest AMP number; add 1 for the number of AMPs. The average divides by all AMPs, so an AMP that receives no row counts as empty rather than being left out. skew_pct is how far the fullest AMP lies above the average.

```sql
-- Rows per AMP
SELECT
  HASHAMP(HASHBUCKET(HASHROW({{pi_columns}}))) AS amp_no,
  COUNT(*) AS row_count,
  CAST(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS DECIMAL(5,2)) AS pct_of_rows
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY row_count DESC;

-- One row of skew metrics
WITH amp_rows AS (
  SELECT HASHAMP(HASHBUCKET(HASHROW({{pi_columns}}))) AS amp_no, COUNT(*) AS row_count
  FROM {{database}}.{{table}}
  GROUP BY 1
),
metrics AS (
  SELECT
    HASHAMP() + 1 AS total_amps,
    COUNT(*) AS amps_with_rows,
    SUM(row_count) AS total_rows,
    MAX(row_count) AS max_rows,
    MIN(row_count) AS min_rows_on_used_amps,
    CAST(SUM(row_count) AS FLOAT) / (HASHAMP() + 1) AS avg_rows
  FROM amp_rows
)
SELECT
  total_amps,
  amps_with_rows,
  total_rows,
  max_rows,
  min_rows_on_used_amps,
  CAST(avg_rows AS DECIMAL(18,1)) AS avg_rows,
  CAST((max_rows - avg_rows) * 100 / NULLIFZERO(avg_rows) AS DECIMAL(9,2)) AS skew_pct,
  CAST(max_rows / NULLIFZERO(avg_rows) AS DECIMAL(9,2)) AS max_to_avg,
  CASE WHEN skew_pct > {{threshold}} THEN 'skewed' ELSE 'even' END AS rating
FROM metrics;
```

### hot_amp_identification — AMPs that hold more than their share

Returns the AMPs whose row count lies above the average by more than the threshold percentage. No row means the distribution is within the threshold.

```sql
WITH amp_rows AS (
  SELECT HASHAMP(HASHBUCKET(HASHROW({{pi_columns}}))) AS amp_no, COUNT(*) AS row_count
  FROM {{database}}.{{table}}
  GROUP BY 1
),
totals AS (
  SELECT CAST(SUM(row_count) AS FLOAT) / (HASHAMP() + 1) AS avg_rows
  FROM amp_rows
)
SELECT
  a.amp_no,
  a.row_count,
  CAST(a.row_count - t.avg_rows AS DECIMAL(18,1)) AS rows_above_avg,
  CAST((a.row_count - t.avg_rows) * 100 / NULLIFZERO(t.avg_rows) AS DECIMAL(9,2)) AS pct_above_avg
FROM amp_rows AS a
CROSS JOIN totals AS t
WHERE a.row_count > t.avg_rows * (1 + {{threshold}} / 100.0)
ORDER BY a.row_count DESC;
```

### pi_column_analysis — Distinct values and the values that hold the most rows

All rows with the same index value go to one AMP, so a value that holds a large share of the rows overloads its AMP. NULL is one value like any other: every row with a NULL index lands on the same AMP, and it appears in the second result as a NULL row if it is frequent.

```sql
-- How many distinct values, and how large the largest one is
SELECT
  COUNT(*) AS distinct_values,
  SUM(row_count) AS total_rows,
  CAST(COUNT(*) * 100.0 / NULLIFZERO(SUM(row_count)) AS DECIMAL(7,2)) AS uniqueness_pct,
  MAX(row_count) AS rows_in_largest_value,
  CAST(MAX(row_count) * 100.0 / NULLIFZERO(SUM(row_count)) AS DECIMAL(7,2)) AS largest_value_pct
FROM (
  SELECT {{pi_columns}}, COUNT(*) AS row_count
  FROM {{database}}.{{table}}
  GROUP BY {{pi_columns}}
) AS v;

-- The ten most frequent values and the AMP each one lands on
SELECT TOP 10
  {{pi_columns}},
  COUNT(*) AS row_count,
  CAST(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS DECIMAL(7,2)) AS pct_of_rows,
  HASHAMP(HASHBUCKET(HASHROW({{pi_columns}}))) AS amp_no
FROM {{database}}.{{table}}
GROUP BY {{pi_columns}}
ORDER BY row_count DESC;
```

### pi_skew_system_view — Space skew from DBC.TableSizeV, without reading the table

DBC.TableSizeV has one row per table and AMP (Vproc) with the permanent space the table uses there. It measures bytes of the table as it is stored, so it only describes the existing primary index. A table with FALLBACK keeps a second copy of each row on another AMP, and that copy is counted in the other AMP's CurrentPerm; the space skew is then lower than the row skew that pi_skew_analysis reports. Small tables show no skew because space is allocated in whole blocks.

```sql
-- One table
SELECT
  DataBaseName,
  TableName,
  COUNT(*) AS amps,
  SUM(CurrentPerm) AS total_perm,
  MAX(CurrentPerm) AS max_amp_perm,
  MIN(CurrentPerm) AS min_amp_perm,
  CAST(AVG(CurrentPerm) AS BIGINT) AS avg_amp_perm,
  CAST((MAX(CurrentPerm) - AVG(CurrentPerm)) * 100 / NULLIFZERO(AVG(CurrentPerm)) AS DECIMAL(9,2)) AS skew_pct
FROM DBC.TableSizeV
WHERE DataBaseName = '{{database}}'
  AND TableName = '{{table}}'
GROUP BY DataBaseName, TableName;

-- Every table of the database, most skewed first; add HAVING skew_pct > {{threshold}} to keep only the skewed ones
SELECT
  TableName,
  SUM(CurrentPerm) AS total_perm,
  MAX(CurrentPerm) AS max_amp_perm,
  CAST((MAX(CurrentPerm) - AVG(CurrentPerm)) * 100 / NULLIFZERO(AVG(CurrentPerm)) AS DECIMAL(9,2)) AS skew_pct
FROM DBC.TableSizeV
WHERE DataBaseName = '{{database}}'
GROUP BY TableName
ORDER BY skew_pct DESC, total_perm DESC;
```

### skew_recommendations — One row that names the cause of the skew and what to do

Combines the skew percentage with the number of distinct values and the share of the most frequent value, and turns them into a finding.

```sql
WITH value_rows AS (
  SELECT
    HASHAMP(HASHBUCKET(HASHROW({{pi_columns}}))) AS amp_no,
    {{pi_columns}},
    COUNT(*) AS row_count
  FROM {{database}}.{{table}}
  GROUP BY 1, {{pi_columns}}
),
amp_rows AS (
  SELECT amp_no, SUM(row_count) AS row_count
  FROM value_rows
  GROUP BY amp_no
),
amp_metrics AS (
  SELECT
    HASHAMP() + 1 AS total_amps,
    MAX(row_count) AS max_rows,
    CAST(SUM(row_count) AS FLOAT) / (HASHAMP() + 1) AS avg_rows
  FROM amp_rows
),
value_metrics AS (
  SELECT
    COUNT(*) AS distinct_values,
    CAST(MAX(row_count) * 100.0 / NULLIFZERO(SUM(row_count)) AS DECIMAL(7,2)) AS largest_value_pct
  FROM value_rows
)
SELECT
  '{{database}}.{{table}}' AS table_name,
  '{{pi_columns}}' AS index_columns,
  a.total_amps,
  v.distinct_values,
  v.largest_value_pct,
  CAST((a.max_rows - a.avg_rows) * 100 / NULLIFZERO(a.avg_rows) AS DECIMAL(9,2)) AS skew_pct,
  CASE
    WHEN skew_pct <= {{threshold}}
      THEN 'Even distribution. Keep these columns as the primary index.'
    WHEN v.distinct_values < a.total_amps * 10
      THEN 'Too few distinct values for the number of AMPs. Choose a column with more distinct values.'
    WHEN v.largest_value_pct >= 5
      THEN 'One value holds a large share of the rows (see pi_column_analysis). Add a column to the index or choose another column.'
    ELSE 'Many values hash to the same AMP. Add a column to the index and measure again.'
  END AS finding
FROM amp_metrics AS a
CROSS JOIN value_metrics AS v;
```

## Common errors

- **Skew is reported for a table whose index is unique** — the column list passed as `pi_columns` is not the table's primary index. Read the real index with primary_index_columns.
- **primary_index_columns returns no row** — the table has NO PRIMARY INDEX, or the name is a view. The hash statements still run on it, because they only evaluate a column list.
- **DBC.TableSizeV shows no skew although the row counts per AMP differ** — the table is small, or it has FALLBACK and the fallback copies even out the space. On this system a table requested with NO FALLBACK is still created with FALLBACK (SHOW TABLE shows it), and AMPs 0/1 and 2/3 report the same CurrentPerm. Trust the row counts.

## Practices

- Check the distribution of a candidate primary index with pi_skew_analysis before creating the table; it needs no copy of the data.
- Treat a skew above about 10 percent as a reason to look at the values, and above about 20 percent as a reason to choose a different index.
- A column with few distinct values (a status, a flag, a region) cannot spread rows over many AMPs. With four status values at most four AMPs receive rows.
- NULL, 0 and the empty string have the same row hash, so they all go to the same AMP. A column that is often NULL or defaulted is a poor primary index.
- When one column is skewed, add a second column to the index and measure the pair, for example `customer_id, order_id` instead of `customer_id`. All index columns must then appear in an equality condition for single-AMP access.
- Measure again after large loads. A distribution that was even can become skewed when one customer, date or default value grows.
- A non-unique primary index allows duplicates but does not reduce skew; only the choice of columns does.
