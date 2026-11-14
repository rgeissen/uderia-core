# Spool space analysis

Find the requests and users that consume spool: DBC.QryLogV.SpoolUsage, current and peak spool per user in DBC.DiskSpaceV, live sessions via MonitorSession.

**Use for:** finding the queries that used the most spool in the last hours; diagnosing "No more spool space" (Error 2646); seeing how close a user is to the spool limit; spool per user and per hour; reading the stored plan of a spool-heavy query; estimating spool before running a query.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `timeframe_hours` | Hours of query log history to read (up to 9999) | `24` |
| `spool_threshold_mb` | Spool per request, in megabytes, above which a request counts as heavy | `1` |
| `username` | User whose requests to list | `ETL_USER` |
| `database` | Database of the tables in the EXPLAIN example | `retail_demo` |

## Statements

### dbql_rules — Whether query logging is on and what it records

The spool figures per request come from the query log (DBQL). One row with UserName `All` and TypeOfRule `Logging enabled` means every user is logged. TextSizeLimit is the number of characters of SQL kept in DBC.QryLogV.QueryText (200 here); the full text is in DBC.QryLogSQLV when SqlFlag is T, the plan in DBC.QryLogExplainV when ExplainFlag is T. With StepFlag F, DBC.QryLogStepsV is empty and there are no per-step spool figures.

```sql
SELECT UserName, TypeOfRule, SqlFlag, ExplainFlag, ObjFlag, StepFlag, TextSizeLimit
FROM DBC.DBQLRulesV
ORDER BY UserName;
```

### top_spool_queries — The requests that used the most spool

SpoolUsage is the spool the request used, in bytes; it is NULL for requests that used none (DDL, single-row inserts, requests rejected by the parser). There is no column MaxSpoolUsage and no LogDate; filter on StartTime. Log rows are written from a cache, so a request appears a few minutes after it ran.

```sql
SELECT TOP 20
  UserName,
  QueryID,
  StartTime,
  CAST(SpoolUsage / 1048576.0 AS DECIMAL(18,2)) AS spool_mb,
  AMPCPUTime,
  MaxAMPCPUTime,
  TotalIOCount,
  NumResultRows,
  NumSteps,
  StatementType,
  ErrorCode,
  QueryText
FROM DBC.QryLogV
WHERE StartTime >= CURRENT_TIMESTAMP - CAST({{timeframe_hours}} AS INTERVAL HOUR(4))
  AND SpoolUsage > {{spool_threshold_mb}} * 1048576
  AND UserName = '{{username}}'
ORDER BY SpoolUsage DESC;
```

### top_spool_query_plan — Full SQL and stored plan of the heaviest request

TOP is not accepted in a subquery (Error 6916); pick the request in a derived table and join to it. In the plan text, look for the steps that build large spools: product joins, redistribution of a large table, sorts.

```sql
-- Full SQL text
SELECT q.QueryID, q.spool_mb, s.SqlRowNo, s.SqlTextInfo
FROM (
  SELECT TOP 1 QueryID, CAST(SpoolUsage / 1048576.0 AS DECIMAL(18,2)) AS spool_mb
  FROM DBC.QryLogV
  WHERE StartTime >= CURRENT_TIMESTAMP - CAST({{timeframe_hours}} AS INTERVAL HOUR(4))
    AND UserName = '{{username}}'
    AND SpoolUsage IS NOT NULL
  ORDER BY SpoolUsage DESC
) AS q
JOIN DBC.QryLogSQLV AS s
  ON s.QueryID = q.QueryID
ORDER BY s.SqlRowNo;

-- Plan as it was logged
SELECT q.QueryID, e.ExpRowNo, e.ExplainText
FROM (
  SELECT TOP 1 QueryID
  FROM DBC.QryLogV
  WHERE StartTime >= CURRENT_TIMESTAMP - CAST({{timeframe_hours}} AS INTERVAL HOUR(4))
    AND UserName = '{{username}}'
    AND SpoolUsage IS NOT NULL
  ORDER BY SpoolUsage DESC
) AS q
JOIN DBC.QryLogExplainV AS e
  ON e.QueryID = q.QueryID
ORDER BY e.ExpRowNo;
```

### current_spool_usage — Spool in use now, peak and limit per user, and per session

DBC.DiskSpaceV has one row per user and AMP (Vproc). MaxSpool is the limit of that AMP: the user's SpoolSpace from DBC.UsersV divided by the number of AMPs. fullest_amp_peak_pct therefore shows how close the fullest AMP came to its limit. MonitorSession lists the sessions with the spool of the request each one is running (ReqSpool, 0 when idle) as of CollectionTime.

```sql
-- Per user: spool in use, recorded peak, and the limit
SELECT
  DatabaseName AS user_name,
  CAST(SUM(CurrentSpool) / 1048576.0 AS DECIMAL(18,2)) AS current_spool_mb,
  CAST(SUM(PeakSpool) / 1048576.0 AS DECIMAL(18,2)) AS peak_spool_mb,
  CAST(SUM(MaxSpool) / 1048576.0 AS DECIMAL(18,2)) AS spool_limit_mb,
  CAST(SUM(CurrentSpool) * 100.0 / NULLIFZERO(SUM(MaxSpool)) AS DECIMAL(9,4)) AS current_pct_of_limit,
  CAST(MAX(CAST(PeakSpool AS FLOAT) * 100 / NULLIFZERO(MaxSpool)) AS DECIMAL(9,4)) AS fullest_amp_peak_pct
FROM DBC.DiskSpaceV
GROUP BY 1
HAVING SUM(CurrentSpool) > 0 OR SUM(PeakSpool) > 0
ORDER BY current_spool_mb DESC, peak_spool_mb DESC;

-- Per session: the request running now
SELECT
  SessionNo,
  UserName,
  PEstate,
  AMPState,
  ReqSpool,
  ReqHotAmpSpool,
  ReqHotAmpSpoolId,
  ReqStartTime,
  QueryID,
  CollectionTime
FROM TABLE (MonitorSession(-1, '*', 0)) AS s
ORDER BY ReqSpool DESC, SessionNo;
```

### spool_by_user — Spool per user over the time frame

```sql
SELECT
  UserName,
  COUNT(*) AS request_count,
  COUNT(SpoolUsage) AS requests_with_spool,
  CAST(AVG(SpoolUsage) / 1048576.0 AS DECIMAL(18,2)) AS avg_spool_mb,
  CAST(MAX(SpoolUsage) / 1048576.0 AS DECIMAL(18,2)) AS max_spool_mb,
  CAST(SUM(SpoolUsage) / 1048576.0 AS DECIMAL(18,2)) AS total_spool_mb,
  SUM(CASE WHEN SpoolUsage > {{spool_threshold_mb}} * 1048576 THEN 1 ELSE 0 END) AS heavy_requests,
  SUM(CASE WHEN ErrorCode = 2646 THEN 1 ELSE 0 END) AS spool_errors
FROM DBC.QryLogV
WHERE StartTime >= CURRENT_TIMESTAMP - CAST({{timeframe_hours}} AS INTERVAL HOUR(4))
GROUP BY UserName
ORDER BY total_spool_mb DESC;
```

### spool_trends — Spool per day and hour

```sql
SELECT
  CAST(StartTime AS DATE) AS log_date,
  EXTRACT(HOUR FROM StartTime) AS log_hour,
  COUNT(*) AS request_count,
  CAST(AVG(SpoolUsage) / 1048576.0 AS DECIMAL(18,2)) AS avg_spool_mb,
  CAST(MAX(SpoolUsage) / 1048576.0 AS DECIMAL(18,2)) AS max_spool_mb,
  SUM(CASE WHEN SpoolUsage > {{spool_threshold_mb}} * 1048576 THEN 1 ELSE 0 END) AS heavy_requests,
  SUM(CASE WHEN ErrorCode = 2646 THEN 1 ELSE 0 END) AS spool_errors
FROM DBC.QryLogV
WHERE StartTime >= CURRENT_TIMESTAMP - CAST({{timeframe_hours}} AS INTERVAL HOUR(4))
GROUP BY 1, 2
ORDER BY 1 DESC, 2 DESC;
```

### spool_error_diagnosis — Requests that failed, and those that ran out of spool

Error 2646 is "No more spool space in <user>". DBC.ErrorMsgs holds the message template of every error code. Codes 2647 and 2648 are internal errors, not spool limits; 2644 is "No more room in database" (permanent space). The second statement returns no row when no request hit the spool limit in the time frame.

```sql
-- Failed requests by error code
SELECT
  l.ErrorCode,
  m.ErrorText AS message_template,
  COUNT(*) AS failed_requests,
  MAX(l.StartTime) AS last_seen
FROM DBC.QryLogV AS l
LEFT JOIN DBC.ErrorMsgs AS m
  ON m.ErrorCode = l.ErrorCode
WHERE l.StartTime >= CURRENT_TIMESTAMP - CAST({{timeframe_hours}} AS INTERVAL HOUR(4))
  AND l.ErrorCode <> 0
GROUP BY 1, 2
ORDER BY failed_requests DESC;

-- Requests that ran out of spool
SELECT
  UserName,
  QueryID,
  StartTime,
  ErrorCode,
  ErrorText,
  CAST(SpoolUsage / 1048576.0 AS DECIMAL(18,2)) AS spool_mb,
  StatementType,
  QueryText
FROM DBC.QryLogV
WHERE StartTime >= CURRENT_TIMESTAMP - CAST({{timeframe_hours}} AS INTERVAL HOUR(4))
  AND ErrorCode = 2646
ORDER BY StartTime DESC;
```

### explain_spool_estimate — Estimated spool of a query before running it

Each step of the plan that writes a spool states its estimated size ("The size of Spool 2 is estimated with no confidence to be 327 rows (9,483 bytes)"). A join without a join condition shows as a product join whose row estimate is about the product of both tables (second statement: 2.5 million rows from 1,000 and 2,500).

```sql
EXPLAIN
SELECT o.order_id, o.amount, i.product_id, i.quantity
FROM {{database}}.orders AS o
JOIN {{database}}.order_items AS i
  ON o.order_id = i.order_id
WHERE o.status = 'completed';

-- No join condition: product join
EXPLAIN
SELECT o.order_id, i.product_id
FROM {{database}}.orders AS o
CROSS JOIN {{database}}.order_items AS i;
```

## Common errors

- **5628 "Column MaxSpoolUsage not found in DBC.QryLogV"** — the column is SpoolUsage. LogDate is not in the view either (same error); filter on StartTime.
- **3807 "Object 'DBC.ResUsageSpoolV' does not exist"** — there is no such view. Current spool per user is in DBC.DiskSpaceV, per session in MonitorSession.
- **6916 "Top N option is not supported in subquery"** — `WHERE QueryID = (SELECT TOP 1 ...)`. Put the TOP query in a derived table and join to it.
- **3706 on `ABORT SESSION n`** — not a statement. Ending another session is an administrator action.
- **3707 on `AS spool`** — spool is a reserved word. Name the column spool_mb, or double-quote it.
- **A request is missing from DBC.QryLogV** — it ran in the last few minutes and the log cache is not written yet, or a DBQL rule excludes the user (see dbql_rules).

## Practices

- Read the plan of a heavy request (top_spool_query_plan) before changing it; the step with the largest spool names the join or sort to fix.
- Select only the needed columns and filter early; spool size is rows times the width of the selected columns.
- A missing join condition gives a product join whose spool is the product of both row counts. Check EXPLAIN for "product join" before running a multi-table query.
- Collect statistics on join and filter columns; without them the spool estimates in the plan are marked "no confidence".
- A skewed join or GROUP BY column concentrates the spool on one AMP, and the limit applies per AMP. Compare fullest_amp_peak_pct with the user total.
- Use SAMPLE or a date filter while developing a query on a large table.
- PeakSpool in DBC.DiskSpaceV is a recorded maximum without a time; use DBC.QryLogV to see what happened in a time frame.
