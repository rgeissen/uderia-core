# Teradata catalog views (DBC)

Metadata queries on DBC.DatabasesV, TablesV, ColumnsV, IndicesV, TableSizeV, DiskSpaceV, StatsV, SessionInfoV and UserRightsV, with the columns each view has.

**Use for:** listing databases, tables and views; describing the columns of a table; finding a table or column by name across databases; table sizes and database space; reading collected statistics and their age; listing indexes; the text of a view; the current session and the current user's rights.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database to describe | `retail_demo` |
| `table` | Table to describe | `orders` |
| `column` | Column name to look for | `customer_id` |
| `search_pattern` | LIKE pattern for a table or column name | `%order%` |
| `view_name` | Name for a view the statement creates | `completed_orders` |
| `stats_table` | Name for a table copy the statement creates and collects statistics on | `orders_stats_copy` |

## Statements

### list_databases — Databases and users

DBKind is `D` for a database and `U` for a user. PermSpace and SpoolSpace are the limits in bytes.

```sql
SELECT DatabaseName, DBKind, OwnerName, PermSpace, SpoolSpace, CreateTimeStamp, CommentString
FROM DBC.DatabasesV
ORDER BY DatabaseName;
```

### list_tables — Objects in a database

TablesV lists every object kind, not only tables. TableKind values seen on this system: `T` table, `O` table without a primary index, `V` view, `I` join index, `M` macro, `P` stored procedure, `F` function, `E` external stored procedure, `U` user-defined type. A queue table is listed as `T` with QueueFlag `Y`.

```sql
SELECT TableName, TableKind, CreatorName, CreateTimeStamp, LastAlterTimeStamp
FROM DBC.TablesV
WHERE DatabaseName = '{{database}}'
  AND TableKind IN ('T', 'O', 'V')
ORDER BY TableKind, TableName;
```

### search_tables — Tables and views whose name matches a pattern, in any database

Without a database filter the result includes system objects (functions in TD_SYSFNLIB, tables in DBC); the TableKind filter removes most of them.

```sql
SELECT DatabaseName, TableName, TableKind
FROM DBC.TablesV
WHERE TableName LIKE '{{search_pattern}}'
  AND TableKind IN ('T', 'O', 'V')
ORDER BY DatabaseName, TableName;
```

### describe_columns — Columns of a table with type, length and nullability

ColumnType is a code: `I1` BYTEINT, `I2` SMALLINT, `I` INTEGER, `I8` BIGINT, `D` DECIMAL (digits in DecimalTotalDigits and DecimalFractionalDigits), `F` FLOAT, `N` NUMBER, `CF` CHAR, `CV` VARCHAR, `DA` DATE, `AT` TIME, `TS` TIMESTAMP, `SZ` TIMESTAMP WITH TIME ZONE, `CO` CLOB, `BO` BLOB, `JN` JSON. ColumnLength is in bytes (a VARCHAR(20) UNICODE column shows 40). ColumnId gives the column order.

```sql
SELECT ColumnId, ColumnName, ColumnType, ColumnLength,
       DecimalTotalDigits, DecimalFractionalDigits,
       Nullable, DefaultValue, ColumnFormat, CommentString
FROM DBC.ColumnsV
WHERE DatabaseName = '{{database}}' AND TableName = '{{table}}'
ORDER BY ColumnId;
```

### search_columns — Tables that contain a column, by exact name or by pattern

```sql
SELECT DatabaseName, TableName, ColumnName, ColumnType
FROM DBC.ColumnsV
WHERE ColumnName = '{{column}}'
ORDER BY DatabaseName, TableName;

SELECT DatabaseName, TableName, ColumnName, ColumnType
FROM DBC.ColumnsV
WHERE ColumnName LIKE '{{search_pattern}}'
  AND DatabaseName = '{{database}}'
ORDER BY TableName, ColumnId;
```

### full_table_info — Every column of every table and view in a database

```sql
SELECT c.TableName, t.TableKind, c.ColumnId, c.ColumnName, c.ColumnType, c.ColumnLength, c.Nullable
FROM DBC.ColumnsV AS c
JOIN DBC.TablesV AS t
  ON t.DatabaseName = c.DatabaseName AND t.TableName = c.TableName
WHERE c.DatabaseName = '{{database}}'
  AND t.TableKind IN ('T', 'O', 'V')
ORDER BY c.TableName, c.ColumnId;
```

### table_sizes — Size of each table in a database

TableSizeV has one row per table and AMP (Vproc), from the space accounting, not from statistics; it costs no table scan. CurrentPerm is a BIGINT in bytes, so divide by 1024.0: an integer division shows 0 MB for a small table. An empty table still occupies its table header (65,536 bytes on this 4-AMP system). The skew column is 0 when all AMPs hold the same amount.

```sql
SELECT TableName,
       SUM(CurrentPerm) AS current_bytes,
       CAST(SUM(CurrentPerm) / 1024.0 / 1024.0 AS DECIMAL(15,3)) AS size_mb,
       CAST(SUM(PeakPerm) / 1024.0 / 1024.0 AS DECIMAL(15,3)) AS peak_mb,
       COUNT(*) AS amps,
       CAST(100 * (1 - AVG(CurrentPerm) / NULLIFZERO(MAX(CurrentPerm))) AS DECIMAL(5,1)) AS skew_pct
FROM DBC.TableSizeV
WHERE DatabaseName = '{{database}}'
GROUP BY TableName
ORDER BY current_bytes DESC;
```

### database_space — Perm space limit, use and free space per database

DiskSpaceV has one row per database and AMP. MaxPerm is 0 for a database without perm space of its own, hence NULLIFZERO.

```sql
SELECT DatabaseName,
       CAST(SUM(MaxPerm) / 1024.0 / 1024 / 1024 AS DECIMAL(15,3)) AS max_gb,
       CAST(SUM(CurrentPerm) / 1024.0 / 1024 / 1024 AS DECIMAL(15,3)) AS used_gb,
       CAST((SUM(MaxPerm) - SUM(CurrentPerm)) / 1024.0 / 1024 / 1024 AS DECIMAL(15,3)) AS free_gb,
       CAST(SUM(CurrentPerm) * 100.0 / NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(7,2)) AS pct_used
FROM DBC.DiskSpaceV
GROUP BY DatabaseName
ORDER BY used_gb DESC;

-- Databases that are more than 80 percent full
SELECT DatabaseName,
       CAST(SUM(CurrentPerm) * 100.0 / NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(7,2)) AS pct_used
FROM DBC.DiskSpaceV
GROUP BY DatabaseName
HAVING pct_used > 80
ORDER BY pct_used DESC;
```

### view_statistics — Collected statistics on a table and their age

StatsV returns no rows for a table without collected statistics. The example therefore copies a table and collects statistics on the copy; on a real table run only the last statement. The row with a NULL ColumnName is the table-level summary. For multi-column statistics ColumnName is the comma-separated list. There is no SampleSize column; SampleSizePct is NULL for a full collection.

```sql
CREATE MULTISET TABLE {{database}}.{{stats_table}} AS (
  SELECT * FROM {{database}}.{{table}}
) WITH DATA
PRIMARY INDEX (order_id);

COLLECT STATISTICS COLUMN (order_id), COLUMN ({{column}}), COLUMN (status, channel)
ON {{database}}.{{stats_table}};

SELECT TRIM(ColumnName) AS column_name, RowCount, UniqueValueCount, NullCount, HighModeFreq,
       SampleSizePct, LastCollectTimeStamp,
       CURRENT_DATE - CAST(LastCollectTimeStamp AS DATE) AS age_days
FROM DBC.StatsV
WHERE DatabaseName = '{{database}}' AND TableName = '{{stats_table}}'
ORDER BY column_name;
```

### table_indexes — Indexes of a table, one row per index column

IndexType values seen on this system: `P` primary index, `Q` primary index of a partitioned table, `K` primary key constraint, `U` unique constraint, `S` secondary index. UniqueFlag is `Y` or `N`. IndexNumber groups the columns of one index; ColumnPosition orders them.

```sql
SELECT IndexNumber, IndexType, UniqueFlag, IndexName, ColumnName, ColumnPosition
FROM DBC.IndicesV
WHERE DatabaseName = '{{database}}' AND TableName = '{{table}}'
ORDER BY IndexNumber, ColumnPosition;
```

### session_info — The current session and the user's space limits

SessionInfoV has no spool columns; the limits of the user are in DatabasesV. Transaction_Mode is `T` for Teradata mode and `A` for ANSI mode.

```sql
SELECT UserName, SessionNo, DefaultDataBase, LogonDate, LogonTime, Transaction_Mode,
       CurrentRole, ProfileName, ClientIpAddress, ClientProgramName
FROM DBC.SessionInfoV
WHERE SessionNo = SESSION;

SELECT DatabaseName, PermSpace, SpoolSpace, TempSpace
FROM DBC.DatabasesV
WHERE DatabaseName = USER;
```

### user_rights — Rights granted directly to the current user

UserRightsV shows only the current user's rights and has no UserName column; rights that come through a role are in DBC.UserRoleRightsV. AccessRight is a code: on a table `R`, `I`, `U`, `D` are SELECT, INSERT, UPDATE, DELETE and `DT` is DROP TABLE; a view has `DV`, a macro `E` (EXECUTE) and `DM`. The TableName `All` stands for every object of the database.

```sql
SELECT DatabaseName, TableName, AccessRight, GrantAuthority, GrantorName
FROM DBC.UserRightsV
WHERE DatabaseName = '{{database}}' AND TableName IN ('{{table}}', 'All')
ORDER BY TableName, AccessRight;

-- How many rights the user holds per database
SELECT DatabaseName, COUNT(*) AS right_count
FROM DBC.UserRightsV
GROUP BY DatabaseName
ORDER BY right_count DESC;
```

### view_definition — The text and the column types of a view

RequestText holds the statement that created the object; SHOW VIEW returns the same text. ColumnsV has NULL in ColumnType, ColumnLength and Nullable for the columns of a view; HELP COLUMN resolves them.

```sql
REPLACE VIEW {{database}}.{{view_name}} AS
SELECT order_id, customer_id, order_date, amount
FROM {{database}}.{{table}}
WHERE status = 'completed';

SELECT RequestText
FROM DBC.TablesV
WHERE DatabaseName = '{{database}}' AND TableName = '{{view_name}}';

SHOW VIEW {{database}}.{{view_name}};

HELP COLUMN {{database}}.{{view_name}}.*;
```

## Common errors

- **5628 "Column ... not found in DBC.StatsV"** — a column the view does not have. Seen with `SampleSize` on StatsV (the column is SampleSizePct) and `UserName` on UserRightsV (the view has none). List the columns with `HELP VIEW DBC.StatsV` before writing the query.
- **Names cut at 30 characters** — the views without the V suffix (DBC.Tables, DBC.Columns) return names as CHAR(30). The V views return VARCHAR(128) UNICODE. Use the V views.
- **9719 "QVCI feature is disabled"** — DBC.ColumnsQV, which would resolve view column types, is switched off here. Use HELP COLUMN for a view.
- **NULL column types** — the object is a view; ColumnsV does not resolve them (see view_definition).
- **Size 0 MB for a table that has rows** — integer division of the BIGINT CurrentPerm. Divide by 1024.0.
- **No rows from StatsV** — no statistics were collected on the table.
- **No rows for a table that exists** — an ANSI-mode session (Transaction_Mode `A`) compares names case-sensitively, and names are stored as they were written at creation, not in upper case. Match the stored case.

## Practices

- In a Teradata-mode session name comparisons ignore case: `TableName = 'ORDERS'` finds `orders`. In an ANSI-mode session they do not.
- The name columns of the V views are VARCHAR and need no TRIM; the code columns (TableKind, ColumnType, IndexType) are CHAR and come back blank-padded.
- Filter on DatabaseName whenever the database is known; a search across all databases also returns system objects.
- Sum TableSizeV and DiskSpaceV over the AMPs (GROUP BY the table or database); a row is one AMP's share.
- Check TableKind before an operation that needs a base table: COLLECT STATISTICS on a view is refused (Error 3706).
- Read statistics age from LastCollectTimeStamp; compare dates (`CAST(LastCollectTimeStamp AS DATE) < CURRENT_DATE - 7`), since a timestamp difference needs an interval unit.
