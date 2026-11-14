# Funnel analysis

Step-by-step funnel conversion from an event table with GROUP BY flags, an ordered time-limited funnel built with MIN timestamps, drop-off and cohort splits.

**Use for:** counting how many users reach each step of a sequence (view, add to cart, checkout, purchase); finding the step with the largest drop-off; requiring the steps to happen in order and within a time window; comparing conversion between segments or start weeks.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the event table | `retail_demo` |
| `table` | Event table, one row per event | `events` |
| `user_column` | Column that identifies the user | `customer_id` |
| `event_column` | Column holding the event name | `event_type` |
| `timestamp_column` | Timestamp of the event | `event_ts` |
| `step_1` | First funnel step (event name) | `view` |
| `step_2` | Second funnel step | `add_to_cart` |
| `step_3` | Third funnel step | `checkout` |
| `step_4` | Fourth funnel step (the conversion) | `purchase` |
| `start_date` | First day of the analysed period | `2025-01-01` |
| `end_date` | Day after the analysed period | `2025-04-01` |
| `time_window_hours` | Hours allowed from the first step to each later step | `24` |
| `progress_table` | Table the ordered funnel writes, one row per user | `funnel_progress` |
| `cohort_table` | Table with one row per user and a segment column | `customers` |
| `cohort_column` | Column of the cohort table to compare by | `segment` |

## Statements

### basic_funnel — Users who reached each step, in any order

Each step counts the distinct users who had that event in the period, regardless of order. A later step can therefore exceed the previous one (here `purchase` has more users than `checkout`); use `ordered_funnel` when the order matters. The step names are cast to VARCHAR because the first branch of a UNION fixes the column type: an uncast `'view'` makes the column CHAR(4) and later names are cut to four characters.

```sql
WITH reached AS (
  SELECT {{user_column}},
    MAX(CASE WHEN {{event_column}} = '{{step_1}}' THEN 1 ELSE 0 END) AS s1,
    MAX(CASE WHEN {{event_column}} = '{{step_2}}' THEN 1 ELSE 0 END) AS s2,
    MAX(CASE WHEN {{event_column}} = '{{step_3}}' THEN 1 ELSE 0 END) AS s3,
    MAX(CASE WHEN {{event_column}} = '{{step_4}}' THEN 1 ELSE 0 END) AS s4
  FROM {{database}}.{{table}}
  WHERE {{timestamp_column}} >= TIMESTAMP '{{start_date}} 00:00:00'
    AND {{timestamp_column}} <  TIMESTAMP '{{end_date}} 00:00:00'
  GROUP BY {{user_column}}
), steps AS (
  SELECT 1 AS step_no, CAST('{{step_1}}' AS VARCHAR(40)) AS step_name, SUM(s1) AS users FROM reached
  UNION ALL SELECT 2, '{{step_2}}', SUM(s2) FROM reached
  UNION ALL SELECT 3, '{{step_3}}', SUM(s3) FROM reached
  UNION ALL SELECT 4, '{{step_4}}', SUM(s4) FROM reached
)
SELECT step_no, step_name, users,
  CAST(100.0 * users / NULLIFZERO(FIRST_VALUE(users) OVER (ORDER BY step_no)) AS DECIMAL(5,1)) AS pct_of_first,
  CAST(100.0 * users / NULLIFZERO(LAG(users) OVER (ORDER BY step_no)) AS DECIMAL(5,1)) AS pct_of_previous
FROM steps
ORDER BY step_no;
```

### ordered_funnel — Steps in order, within a time window from the first step

Builds one row per user who did the first step: the time of the first `step_1` event, then the first `step_2` after it, the first `step_3` after that, and so on, each within `time_window_hours` of `t1`. A NULL time means the user did not reach that step in order. Only the user's first `step_1` event starts a journey. The table is read by the next three statements.

```sql
CREATE MULTISET TABLE {{database}}.{{progress_table}} AS (
  WITH ev AS (
    SELECT {{user_column}} AS user_id, {{event_column}} AS ev_name, {{timestamp_column}} AS ts
    FROM {{database}}.{{table}}
    WHERE {{timestamp_column}} >= TIMESTAMP '{{start_date}} 00:00:00'
      AND {{timestamp_column}} <  TIMESTAMP '{{end_date}} 00:00:00'
  ), s1 AS (
    SELECT user_id, MIN(ts) AS t1 FROM ev WHERE ev_name = '{{step_1}}' GROUP BY user_id
  ), s2 AS (
    SELECT s1.user_id, s1.t1, MIN(ev.ts) AS t2
    FROM s1 JOIN ev ON ev.user_id = s1.user_id AND ev.ev_name = '{{step_2}}'
     AND ev.ts > s1.t1 AND ev.ts <= s1.t1 + CAST({{time_window_hours}} AS INTERVAL HOUR(4))
    GROUP BY s1.user_id, s1.t1
  ), s3 AS (
    SELECT s2.user_id, MIN(ev.ts) AS t3
    FROM s2 JOIN ev ON ev.user_id = s2.user_id AND ev.ev_name = '{{step_3}}'
     AND ev.ts > s2.t2 AND ev.ts <= s2.t1 + CAST({{time_window_hours}} AS INTERVAL HOUR(4))
    GROUP BY s2.user_id
  ), s4 AS (
    SELECT s3.user_id, MIN(ev.ts) AS t4
    FROM s3 JOIN s2 ON s2.user_id = s3.user_id
    JOIN ev ON ev.user_id = s3.user_id AND ev.ev_name = '{{step_4}}'
     AND ev.ts > s3.t3 AND ev.ts <= s2.t1 + CAST({{time_window_hours}} AS INTERVAL HOUR(4))
    GROUP BY s3.user_id
  )
  SELECT s1.user_id, s1.t1, s2.t2, s3.t3, s4.t4
  FROM s1
  LEFT JOIN s2 ON s2.user_id = s1.user_id
  LEFT JOIN s3 ON s3.user_id = s1.user_id
  LEFT JOIN s4 ON s4.user_id = s1.user_id
) WITH DATA
PRIMARY INDEX (user_id);

SELECT
  COUNT(t1) AS step_1_users,
  COUNT(t2) AS step_2_users,
  COUNT(t3) AS step_3_users,
  COUNT(t4) AS step_4_users,
  CAST(100.0 * COUNT(t2) / NULLIFZERO(COUNT(t1)) AS DECIMAL(5,1)) AS pct_1_to_2,
  CAST(100.0 * COUNT(t3) / NULLIFZERO(COUNT(t2)) AS DECIMAL(5,1)) AS pct_2_to_3,
  CAST(100.0 * COUNT(t4) / NULLIFZERO(COUNT(t3)) AS DECIMAL(5,1)) AS pct_3_to_4,
  CAST(100.0 * COUNT(t4) / NULLIFZERO(COUNT(t1)) AS DECIMAL(5,1)) AS pct_overall
FROM {{database}}.{{progress_table}};
```

### drop_off_analysis — Where users left the ordered funnel

Reads the table built by `ordered_funnel`. Each user falls in exactly one group, so the percentages add up to 100.

```sql
SELECT
  CASE
    WHEN t4 IS NOT NULL THEN '4 completed'
    WHEN t3 IS NOT NULL THEN '3 dropped before {{step_4}}'
    WHEN t2 IS NOT NULL THEN '2 dropped before {{step_3}}'
    ELSE '1 dropped before {{step_2}}'
  END AS user_segment,
  COUNT(*) AS users,
  CAST(100.0 * COUNT(*) / SUM(COUNT(*)) OVER () AS DECIMAL(5,1)) AS pct_of_entrants,
  AVG(CAST(((COALESCE(t4, t3, t2, t1) - t1) MINUTE(4)) AS INTEGER)) AS avg_minutes_in_funnel
FROM {{database}}.{{progress_table}}
GROUP BY 1
ORDER BY 1;
```

### cohort_funnel — Ordered funnel by segment and by start week

Reads the table built by `ordered_funnel`. The first query compares segments from a user table; the second groups users by the ISO week (`TRUNC(date, 'IW')`, the Monday) in which they entered the funnel.

```sql
SELECT c.{{cohort_column}} AS cohort,
  COUNT(p.t1) AS entered,
  COUNT(p.t2) AS reached_step_2,
  COUNT(p.t4) AS converted,
  CAST(100.0 * COUNT(p.t2) / NULLIFZERO(COUNT(p.t1)) AS DECIMAL(5,1)) AS pct_1_to_2,
  CAST(100.0 * COUNT(p.t4) / NULLIFZERO(COUNT(p.t1)) AS DECIMAL(5,1)) AS pct_overall
FROM {{database}}.{{progress_table}} AS p
JOIN {{database}}.{{cohort_table}} AS c
  ON c.{{user_column}} = p.user_id
GROUP BY 1
ORDER BY pct_overall DESC;

SELECT TRUNC(CAST(t1 AS DATE), 'IW') AS cohort_week,
  COUNT(t1) AS entered,
  COUNT(t4) AS converted,
  CAST(100.0 * COUNT(t4) / NULLIFZERO(COUNT(t1)) AS DECIMAL(5,1)) AS pct_overall
FROM {{database}}.{{progress_table}}
GROUP BY 1
ORDER BY 1;
```

## Common errors

- **A later step has more users than the step before it** — counting each step independently (`basic_funnel`) ignores order. Use `ordered_funnel`, where each step must follow the previous one.
- **Step names cut to four characters** — a UNION column takes its type from the first branch; `'view'` makes it CHAR(4). Cast the first literal to VARCHAR.
- **3706 near an interval expression** — `CAST((ts2 - ts1) MINUTE(4) AS INTEGER)` is refused; wrap the interval in its own parentheses: `CAST(((ts2 - ts1) MINUTE(4)) AS INTEGER)`.
- **Empty funnel** — a window such as `CURRENT_DATE - 30` finds nothing when the data is older. Bound the period with explicit `start_date` and `end_date`.

## Practices

- Count distinct users per step (one row per user, MAX of a 0/1 flag), never event rows.
- Decide whether order matters before reading a drop-off: an unordered count overstates later steps.
- Match `time_window_hours` to the journey: hours for a checkout, days for a trial signup.
- Store the per-user step times once (`ordered_funnel`) and run drop-off and cohort queries against that table.
- Report the absolute number of users lost at each step beside the percentage; a small percentage of a large step can be the bigger loss.
