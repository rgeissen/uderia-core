# Statistics collection

COLLECT STATISTICS in its accepted forms (column, multi-column, index, PARTITION, sample, summary), and DBC.StatsV queries for missing and stale statistics.

**Use for:** a plan whose row estimates say "no confidence"; statistics after a load; finding index and filter columns without statistics; listing what is collected on a table and how old it is; refreshing or dropping statistics.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to collect statistics on | `orders` |
| `column` | A join or filter column | `customer_id` |
| `column1` | First of two columns filtered together | `status` |
| `column2` | Second of two columns filtered together | `channel` |
| `pi_column` | Primary index column | `order_id` |
| `si_column` | Column to carry a secondary index | `customer_id` |
| `date_column` | Date column, used for partitioning | `order_date` |
| `sample_percent` | Percentage of rows to read for sampled statistics | `10` |
| `stale_days` | Age in days after which statistics count as stale | `7` |

## Statements

### statistics_analysis — Columns of a table, their role, and whether statistics exist

DBC.StatsV has one row per collected statistic; ColumnName is the column, or a comma-separated list for multi-column statistics, and NULL for the table-level summary row whose RowCount is the optimizer's row count. The view has no StatisticsType, CollectTimeStamp or SampleSize column, and DBC.TableSizeV has no row count.

```sql
SELECT
  c.ColumnName,
  c.ColumnType,
  CASE
    WHEN MAX(CASE WHEN i.IndexType IN ('P', 'Q') THEN 1 ELSE 0 END) = 1 THEN 'primary index'
    WHEN MAX(CASE WHEN i.IndexType IS NOT NULL THEN 1 ELSE 0 END) = 1 THEN 'secondary index'
    ELSE 'column'
  END AS column_role,
  CASE WHEN MAX(s.LastCollectTimeStamp) IS NULL THEN 'no statistics' ELSE 'collected' END AS stats_status,
  MAX(s.LastCollectTimeStamp) AS last_collected
FROM DBC.ColumnsV AS c
LEFT JOIN DBC.IndicesV AS i
  ON i.DatabaseName = c.DatabaseName AND i.TableName = c.TableName AND i.ColumnName = c.ColumnName
LEFT JOIN DBC.StatsV AS s
  ON s.DatabaseName = c.DatabaseName AND s.TableName = c.TableName AND s.ColumnName = c.ColumnName
WHERE c.DatabaseName = '{{database}}'
  AND c.TableName = '{{table}}'
GROUP BY c.ColumnName, c.ColumnType, c.ColumnId
ORDER BY c.ColumnId;

-- Size of the table, and the row count the optimizer holds (NULL until statistics exist)
SELECT
  t.DataBaseName,
  t.TableName,
  CAST(SUM(t.CurrentPerm) / 1048576.0 AS DECIMAL(18,2)) AS size_mb,
  MAX(s.RowCount) AS stats_row_count
FROM DBC.TableSizeV AS t
LEFT JOIN DBC.StatsV AS s
  ON s.DatabaseName = t.DataBaseName AND s.TableName = t.TableName AND s.ColumnName IS NULL
WHERE t.DataBaseName = '{{database}}'
  AND t.TableName = '{{table}}'
GROUP BY t.DataBaseName, t.TableName;
```

### statistics_recommendations — Generate COLLECT STATISTICS statements for what is missing

The first statement writes one COLLECT STATISTICS per index that has no statistics on its column list. The second uses the query log (DBC.QryLogObjectsV, filled when the DBQL rule has ObjFlag T): columns of the table that logged requests used in a WHERE or join condition and that have no statistics, most used first.

```sql
-- Indexes without statistics
SELECT x.index_type, x.collect_statement
FROM (
  SELECT
    MIN(i.IndexType) AS index_type,
    OREPLACE(TRIM(TRAILING ',' FROM (XMLAGG(TRIM(i.ColumnName) || ',' ORDER BY i.ColumnPosition) (VARCHAR(1000)))), ' ', '') AS index_columns,
    'COLLECT STATISTICS COLUMN (' || index_columns || ') ON {{database}}.{{table}};' AS collect_statement
  FROM DBC.IndicesV AS i
  WHERE i.DatabaseName = '{{database}}'
    AND i.TableName = '{{table}}'
  GROUP BY i.IndexNumber
) AS x
LEFT JOIN DBC.StatsV AS s
  ON s.DatabaseName = '{{database}}' AND s.TableName = '{{table}}' AND s.ColumnName = x.index_columns
WHERE s.ColumnName IS NULL
ORDER BY x.index_type;

-- Columns used in conditions by logged requests, without statistics
SELECT
  'COLLECT STATISTICS COLUMN (' || TRIM(u.ObjectColumnName) || ') ON {{database}}.{{table}};' AS collect_statement,
  COUNT(DISTINCT u.QueryID) AS requests
FROM DBC.QryLogObjectsV AS u
LEFT JOIN DBC.StatsV AS s
  ON s.DatabaseName = u.ObjectDatabaseName AND s.TableName = u.ObjectTableName AND s.ColumnName = u.ObjectColumnName
WHERE u.ObjectDatabaseName = '{{database}}'
  AND u.ObjectTableName = '{{table}}'
  AND u.ObjectType = 'Col'
  AND u.TypeOfUse LIKE '%conditional%'
  AND s.ColumnName IS NULL
GROUP BY 1
ORDER BY requests DESC;
```

### statistics_collection — Collect on single columns, and recollect everything defined

The form is `COLLECT STATISTICS COLUMN (col) ON table`; several COLUMN clauses can share one statement. `COLLECT STATISTICS ON table` without a column recollects every statistic already defined and fails with Error 3624 when none is. After collection, EXPLAIN of a filter on the column changes from "no confidence" to "high confidence" with the true row count.

```sql
COLLECT STATISTICS COLUMN ({{column}}) ON {{database}}.{{table}};

-- Several columns in one statement
COLLECT STATISTICS
  COLUMN ({{pi_column}}),
  COLUMN ({{column1}}),
  COLUMN ({{date_column}})
ON {{database}}.{{table}};

-- Table-level row count only
COLLECT SUMMARY STATISTICS ON {{database}}.{{table}};

-- Recollect all statistics defined on the table (use after a load)
COLLECT STATISTICS ON {{database}}.{{table}};

EXPLAIN SELECT * FROM {{database}}.{{table}} WHERE {{column1}} = 'returned';
```

### composite_statistics — Multi-column statistics for columns filtered or joined together

A multi-column statistic records how many distinct combinations occur (UniqueValueCount); its ColumnName in DBC.StatsV is the list without spaces, `status,channel`.

```sql
COLLECT STATISTICS
  COLUMN ({{column1}}, {{column2}}),
  COLUMN ({{column2}})
ON {{database}}.{{table}};

SELECT ColumnName, RowCount, UniqueValueCount, HighModeFreq
FROM DBC.StatsV
WHERE DatabaseName = '{{database}}'
  AND TableName = '{{table}}'
  AND ColumnName IN ('{{column1}}', '{{column2}}', '{{column1}},{{column2}}')
ORDER BY ColumnName;
```

### sampled_statistics — Read only a percentage of the rows

USING comes before the COLUMN clause; `COLUMN (col) USING SAMPLE ON table` is a syntax error (3707). USING SYSTEM SAMPLE lets the system choose the percentage. SampleSizePct shows the percentage read for an explicit sample (9.80 for a request of 10).

```sql
COLLECT STATISTICS USING SAMPLE {{sample_percent}} PERCENT COLUMN (amount) ON {{database}}.{{table}};

COLLECT STATISTICS USING SYSTEM SAMPLE COLUMN (order_ts) ON {{database}}.{{table}};

SELECT ColumnName, SampleSignature, SampleSizePct, RowCount, UniqueValueCount
FROM DBC.StatsV
WHERE DatabaseName = '{{database}}'
  AND TableName = '{{table}}'
  AND ColumnName IN ('amount', 'order_ts');
```

### index_statistics — Statistics on an index and on PARTITION

INDEX (cols) must name the columns of an existing index (Error 3526 otherwise); COLUMN (cols) on the same columns gives the same statistic. On a partitioned table also collect COLUMN (PARTITION), which records the number of populated partitions. The block builds a partitioned copy with a secondary index to show both.

```sql
CREATE MULTISET TABLE {{database}}.{{table}}_indexed AS (
  SELECT * FROM {{database}}.{{table}}
) WITH DATA
PRIMARY INDEX ({{pi_column}})
PARTITION BY RANGE_N({{date_column}} BETWEEN DATE '2024-01-01' AND DATE '2026-12-31' EACH INTERVAL '1' MONTH)
INDEX ({{si_column}});

COLLECT STATISTICS
  INDEX ({{pi_column}}),
  INDEX ({{si_column}}),
  COLUMN (PARTITION),
  COLUMN ({{date_column}})
ON {{database}}.{{table}}_indexed;

SELECT ColumnName, IndexNumber, RowCount, UniqueValueCount
FROM DBC.StatsV
WHERE DatabaseName = '{{database}}'
  AND TableName = '{{table}}_indexed'
ORDER BY StatsId;
```

### check_existing_statistics — What is collected, its values and its age

`CURRENT_TIMESTAMP - LastCollectTimeStamp` without an interval type is refused (Error 5407); subtract dates for days, or write `(a - b) DAY(4) TO SECOND(0)`. HELP STATISTICS lists the same statistics in short form, and SHOW STATISTICS returns the COLLECT statements that would recreate them.

```sql
SELECT
  COALESCE(TRIM(ColumnName), '(table summary)') AS stat_columns,
  IndexNumber,
  SampleSizePct,
  RowCount,
  UniqueValueCount,
  NullCount,
  HighModeFreq,
  LastCollectTimeStamp,
  CURRENT_DATE - CAST(LastCollectTimeStamp AS DATE) AS days_old,
  CASE WHEN days_old > {{stale_days}} THEN 'stale' ELSE 'current' END AS status
FROM DBC.StatsV
WHERE DatabaseName = '{{database}}'
  AND TableName = '{{table}}'
ORDER BY StatsId;

-- Row count in the statistics against the real row count
SELECT 'statistics' AS source, CAST(MAX(RowCount) AS BIGINT) AS row_count
FROM DBC.StatsV
WHERE DatabaseName = '{{database}}' AND TableName = '{{table}}'
UNION ALL
SELECT 'table' AS source, CAST(COUNT(*) AS BIGINT) AS row_count
FROM {{database}}.{{table}};

HELP STATISTICS {{database}}.{{table}};

SHOW STATISTICS ON {{database}}.{{table}};
```

### statistics_refresh_strategy — Statistics of a whole database by age, then recollect

Lists every statistic in the database with its age and the size of its table, oldest first. The statement at the end recollects the statistics already defined on one table.

```sql
SELECT
  s.TableName,
  COALESCE(TRIM(s.ColumnName), '(table summary)') AS stat_columns,
  s.LastCollectTimeStamp,
  CURRENT_DATE - CAST(s.LastCollectTimeStamp AS DATE) AS days_old,
  ts.size_mb,
  CASE
    WHEN days_old > {{stale_days}} * 4 THEN 'very old'
    WHEN days_old > {{stale_days}} THEN 'stale'
    ELSE 'current'
  END AS freshness
FROM DBC.StatsV AS s
JOIN (
  SELECT TableName, CAST(SUM(CurrentPerm) / 1048576.0 AS DECIMAL(18,2)) AS size_mb
  FROM DBC.TableSizeV
  WHERE DataBaseName = '{{database}}'
  GROUP BY TableName
) AS ts
  ON ts.TableName = s.TableName
WHERE s.DatabaseName = '{{database}}'
ORDER BY days_old DESC, ts.size_mb DESC, s.TableName, s.StatsId;

COLLECT STATISTICS ON {{database}}.{{table}};
```

### drop_statistics — Remove one statistic, or all statistics of a table

```sql
DROP STATISTICS COLUMN ({{column2}}) ON {{database}}.{{table}};

-- All statistics of the table
DROP STATISTICS ON {{database}}.{{table}};
```

## Common errors

- **3624 "There are no statistics defined for the table"** — `COLLECT STATISTICS ON table` or HELP STATISTICS on a table without statistics. Name the columns the first time.
- **3706 "Statistics operations are not supported on the object"** — the name is a view. Collect on the base table.
- **3706 "Column/StatsName x does not exist"** — a misspelled column. Check DBC.ColumnsV.
- **3707 near USING** — `COLUMN (col) USING SAMPLE ON table`. Write `COLLECT STATISTICS USING SAMPLE n PERCENT COLUMN (col) ON table`.
- **3526 "The specified index does not exist"** — INDEX (cols) names columns that are not an index. Use COLUMN (cols).
- **5628 "Column ... not found in DBC.StatsV"** — StatisticsType, CollectTimeStamp and SampleSize do not exist; the columns are StatsType, LastCollectTimeStamp and SampleSizePct.
- **5407 "Invalid operation for DateTime or Interval"** — a timestamp difference without an interval type. See check_existing_statistics.

## Practices

- Collect on the primary index columns, on join columns, and on columns used in WHERE and GROUP BY.
- Collect multi-column statistics when two columns are filtered together and their values depend on each other (city and state, status and channel).
- Define the statistics once by column, then refresh with `COLLECT STATISTICS ON table` after each large load.
- Use sampled statistics where a full collection on a very large table takes too long; compare UniqueValueCount with a full collection once before relying on the sample.
- Statistics on a volatile table are collected the same way and listed by HELP STATISTICS, not in DBC.StatsV.
- Drop statistics on columns that queries no longer use; every statistic is recollected on each refresh.
- Check a plan with EXPLAIN after collecting; the estimate for a filtered column should be close to the real row count.
