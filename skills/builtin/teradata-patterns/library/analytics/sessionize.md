# Sessionize: group events into sessions

Split each user's time-ordered events into sessions on an idle timeout with the SESSIONIZE table operator, or with LAG and a running SUM.

**Use for:** web or app session identification; session duration, depth and bounce rate; preparing a click stream for funnel or nPath analysis within one session; flagging rapid, bot-like clicks.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the events table | `retail_demo` |
| `table` | Table of time-ordered events | `events` |
| `partition_column` | Column that identifies one user or device | `customer_id` |
| `timestamp_column` | TIMESTAMP column that orders the events | `event_ts` |
| `timeout_seconds` | Idle gap, in seconds, after which a new session starts | `1800` |
| `timeout_minutes` | The same gap in minutes, for the window-function form | `30` |
| `page_column` | Column naming the page or screen of an event | `page` |
| `click_lag_seconds` | Gap, in seconds, at or below which a click is flagged as too fast | `120` |

## Statements

### basic — Assign a session number to every event

SESSIONIZE returns every input column plus `SESSIONID`. The number restarts at 0 for each partition, so a session is identified by the partition column and `SESSIONID` together, never by `SESSIONID` alone. The ORDER BY on the ON clause is what orders the events; give it.

```sql
SELECT
  s.{{partition_column}},
  s.{{timestamp_column}},
  s.event_type,
  s.SESSIONID,
  COUNT(*) OVER (PARTITION BY s.{{partition_column}}, s.SESSIONID) AS events_in_session
FROM SESSIONIZE (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{timestamp_column}}
  USING
    TimeColumn ('{{timestamp_column}}')
    TimeOut ({{timeout_seconds}})
) AS s
ORDER BY s.{{partition_column}}, s.{{timestamp_column}};
```

### session_metrics — Start, end, duration, depth and pages per session

Inside CAST, put the interval expression in its own parentheses: `CAST(((b - a) MINUTE(4)) AS INTEGER)`. Without them the statement fails with Error 3706.

```sql
SELECT
  s.{{partition_column}},
  s.SESSIONID,
  MIN(s.{{timestamp_column}}) AS session_start,
  MAX(s.{{timestamp_column}}) AS session_end,
  CAST(((MAX(s.{{timestamp_column}}) - MIN(s.{{timestamp_column}})) MINUTE(4)) AS INTEGER) AS duration_minutes,
  COUNT(*) AS event_count,
  COUNT(DISTINCT s.{{page_column}}) AS unique_pages
FROM SESSIONIZE (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{timestamp_column}}
  USING
    TimeColumn ('{{timestamp_column}}')
    TimeOut ({{timeout_seconds}})
) AS s
GROUP BY s.{{partition_column}}, s.SESSIONID
ORDER BY session_start DESC;
```

### bounce_rate — Single-event sessions and the bounce rate

```sql
WITH sessions AS (
  SELECT s.{{partition_column}}, s.SESSIONID, COUNT(*) AS event_count
  FROM SESSIONIZE (
    ON {{database}}.{{table}}
    PARTITION BY {{partition_column}}
    ORDER BY {{timestamp_column}}
    USING
      TimeColumn ('{{timestamp_column}}')
      TimeOut ({{timeout_seconds}})
  ) AS s
  GROUP BY 1, 2
)
SELECT
  COUNT(*) AS sessions,
  SUM(CASE WHEN event_count = 1 THEN 1 ELSE 0 END) AS bounces,
  CAST(100.0 * SUM(CASE WHEN event_count = 1 THEN 1 ELSE 0 END) / COUNT(*) AS DECIMAL(5,2)) AS bounce_pct,
  CAST(AVG(CAST(event_count AS DECIMAL(9,2))) AS DECIMAL(9,2)) AS avg_events_per_session
FROM sessions;
```

### conversion_by_session — Sessions that contain a purchase

```sql
SELECT
  COUNT(*) AS sessions,
  SUM(converted) AS converting_sessions,
  CAST(100.0 * SUM(converted) / COUNT(*) AS DECIMAL(5,2)) AS session_conversion_pct
FROM (
  SELECT s.{{partition_column}}, s.SESSIONID,
         MAX(CASE WHEN s.event_type = 'purchase' THEN 1 ELSE 0 END) AS converted
  FROM SESSIONIZE (
    ON {{database}}.{{table}}
    PARTITION BY {{partition_column}}
    ORDER BY {{timestamp_column}}
    USING
      TimeColumn ('{{timestamp_column}}')
      TimeOut ({{timeout_seconds}})
  ) AS s
  GROUP BY 1, 2
) AS per_session;
```

### filtered_input — Sessionize a subset of the events

The ON clause takes a query, so filter before sessionizing. Here sessions are built from browsing events only.

```sql
SELECT s.{{partition_column}}, COUNT(DISTINCT s.SESSIONID) AS browse_sessions
FROM SESSIONIZE (
  ON (SELECT * FROM {{database}}.{{table}} WHERE event_type IN ('view', 'search'))
  PARTITION BY {{partition_column}}
  ORDER BY {{timestamp_column}}
  USING
    TimeColumn ('{{timestamp_column}}')
    TimeOut ({{timeout_seconds}})
) AS s
GROUP BY 1
ORDER BY browse_sessions DESC;
```

### click_lag — Flag clicks that come too fast

With ClickLag, SESSIONIZE adds a `CLICKLAG` column: `t` for an event whose gap from the previous event is at most the given seconds, `f` otherwise. On this system the flagged rows were returned with and without `EmitNull ('true')`.

```sql
SELECT
  s.{{partition_column}},
  s.SESSIONID,
  SUM(CASE WHEN TRIM(s.CLICKLAG) = 't' THEN 1 ELSE 0 END) AS fast_clicks,
  COUNT(*) AS event_count
FROM SESSIONIZE (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{timestamp_column}}
  USING
    TimeColumn ('{{timestamp_column}}')
    TimeOut ({{timeout_seconds}})
    ClickLag ({{click_lag_seconds}})
    EmitNull ('true')
) AS s
GROUP BY 1, 2
HAVING fast_clicks > 0
ORDER BY fast_clicks DESC;
```

### window_sessions — The same sessions with window functions only

Use this where SESSIONIZE is not available. A new session starts on a partition's first event and after each gap above the timeout; a running sum numbers the sessions from 0. On the fixture it yields the same 976 sessions as SESSIONIZE with a 1800-second timeout.

```sql
SELECT
  e.{{partition_column}},
  e.{{timestamp_column}},
  e.event_type,
  SUM(CASE WHEN e.prev_ts IS NULL
             OR (e.{{timestamp_column}} - e.prev_ts) MINUTE(4) > INTERVAL '{{timeout_minutes}}' MINUTE
           THEN 1 ELSE 0 END)
    OVER (PARTITION BY e.{{partition_column}} ORDER BY e.{{timestamp_column}}
          ROWS UNBOUNDED PRECEDING) - 1 AS session_no
FROM (
  SELECT t.*,
         LAG(t.{{timestamp_column}}) OVER (PARTITION BY t.{{partition_column}} ORDER BY t.{{timestamp_column}}) AS prev_ts
  FROM {{database}}.{{table}} AS t
) AS e
ORDER BY e.{{partition_column}}, e.{{timestamp_column}};
```

## Common errors

- **7810 "Error in function SESSIONIZE: The specified TIMECOLUMN could not be found in the input"** — the name in TimeColumn is not a column of the ON input. Name a column the ON query returns.
- **3707 on a negative TimeOut** — `TimeOut (-5)` is a syntax error; give a positive number of seconds. `TimeOut (0)` is accepted and puts every event in its own session.
- **3706 "expected something between ')' and the 'MINUTE' keyword"** — `CAST((b - a) MINUTE(4) AS INTEGER)`. Wrap the interval expression in its own parentheses: `CAST(((b - a) MINUTE(4)) AS INTEGER)`.
- **Sessions merged across users** — grouping or windowing by `SESSIONID` alone. It restarts at 0 per partition; always pair it with the partition column.

## Practices

- Choose the timeout from the data: look at the distribution of gaps between consecutive events and pick a value above the common within-visit gaps. 30 minutes is a widespread default for web traffic.
- Partition by the user or device that produces the events; sessions never span two partitions.
- Filter the input in the ON query rather than after sessionizing, so the timeout is measured between the events you care about.
- Run nPath or funnel analysis with PARTITION BY the user column and SESSIONID when a pattern must stay within one visit.
- Give timestamp differences an explicit interval precision such as `MINUTE(4)`; the default precision of 2 overflows on long gaps.
