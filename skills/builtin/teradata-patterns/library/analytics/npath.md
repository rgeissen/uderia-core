# nPath sequence analysis

Find ordered event sequences per user with the NPATH table operator: SYMBOLS, PATTERN operators, MODE, and RESULT with ACCUMULATE, FIRST, LAST, COUNT.

**Use for:** conversion paths and funnels in a click stream; abandoned-cart detection; the steps users take before or after an event; sequence analysis of logs or transactions; paths within one session after SESSIONIZE.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the events table | `retail_demo` |
| `table` | Table of time-ordered events | `events` |
| `partition_column` | Column that identifies one user or entity | `customer_id` |
| `order_column` | Column that orders the events within a partition | `event_ts` |
| `event_column` | Column holding the event type | `event_type` |
| `start_event` | Event value that opens the sequence | `view` |
| `middle_event` | Event value expected between start and end | `add_to_cart` |
| `end_event` | Event value that closes the sequence (the conversion) | `purchase` |
| `timeout_seconds` | Session timeout for the per-session statement | `1800` |

## Statements

### discovery — The event values and their share

Symbol predicates compare exact values, so look at the values first.

```sql
SELECT TOP 100
  {{event_column}} AS event_value,
  COUNT(*) AS event_count,
  CAST(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS DECIMAL(5,2)) AS pct_of_total
FROM {{database}}.{{table}}
GROUP BY 1
ORDER BY event_count DESC;
```

### symbol_validation — Check that each planned symbol value occurs

```sql
SELECT {{event_column}}, COUNT(*) AS match_count
FROM {{database}}.{{table}}
WHERE {{event_column}} IN ('{{start_event}}', '{{middle_event}}', '{{end_event}}')
GROUP BY 1
ORDER BY 2 DESC;
```

### basic_pattern — Count the paths that match a pattern

`.` means "followed immediately by"; `A+` is one or more consecutive A rows. ACCUMULATE returns the matched values as a bracketed list, `[view|add_to_cart|purchase]` with the delimiter below. MODE is required.

```sql
SELECT TOP 50
  n.path,
  COUNT(*) AS path_count,
  CAST(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS DECIMAL(5,2)) AS pct_of_total
FROM NPATH (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{order_column}}
  USING
    MODE (NONOVERLAPPING)
    PATTERN ('S.M+.E')
    SYMBOLS (
      {{event_column}} = '{{start_event}}' AS S,
      {{event_column}} = '{{middle_event}}' AS M,
      {{event_column}} = '{{end_event}}' AS E
    )
    RESULT (
      ACCUMULATE ({{event_column}} OF ANY (S, M, E) DELIMITER '|') AS path
    )
) AS n
GROUP BY n.path
ORDER BY path_count DESC;
```

### with_gaps — Allow any events in between, and capture timing

`TRUE AS W` matches any row; `W*` allows any number of rows between S and E. W* is greedy: with `TRUE AS W` one match ran through several purchases. Defining W as "anything but the end event" stops each match at the first end event.

```sql
SELECT
  n.cust,
  n.first_ts,
  n.last_ts,
  CAST(((n.last_ts - n.first_ts) MINUTE(4)) AS INTEGER) AS minutes_to_convert,
  n.steps,
  n.path
FROM NPATH (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{order_column}}
  USING
    MODE (NONOVERLAPPING)
    PATTERN ('S.W*.E')
    SYMBOLS (
      {{event_column}} = '{{start_event}}' AS S,
      {{event_column}} <> '{{end_event}}' AS W,
      {{event_column}} = '{{end_event}}' AS E
    )
    RESULT (
      FIRST ({{partition_column}} OF S) AS cust,
      FIRST ({{order_column}} OF S) AS first_ts,
      LAST ({{order_column}} OF E) AS last_ts,
      COUNT (* OF ANY (S, W, E)) AS steps,
      ACCUMULATE ({{event_column}} OF ANY (S, W, E) DELIMITER ' > ') AS path
    )
) AS n
ORDER BY n.steps DESC;
```

### anchors_and_operators — Anchors, repetition and alternation

`^` anchors a pattern to the first row of the partition and `$` to the last; `{2,3}` repeats a symbol, `(A|B)` takes either. On the fixture `^S` matched exactly the customers whose first event is a view (90), and `E$` those whose last event is a purchase (32).

```sql
-- Partitions whose first event is the start event
SELECT COUNT(*) AS partitions_starting_with_start_event
FROM NPATH (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{order_column}}
  USING
    MODE (NONOVERLAPPING)
    PATTERN ('^S')
    SYMBOLS ({{event_column}} = '{{start_event}}' AS S)
    RESULT (FIRST ({{partition_column}} OF S) AS cust)
) AS n;

-- Two or three start events in a row, then either the middle or the end event
SELECT n.path, COUNT(*) AS matches
FROM NPATH (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{order_column}}
  USING
    MODE (NONOVERLAPPING)
    PATTERN ('S{2,3}.(M|E)')
    SYMBOLS (
      {{event_column}} = '{{start_event}}' AS S,
      {{event_column}} = '{{middle_event}}' AS M,
      {{event_column}} = '{{end_event}}' AS E
    )
    RESULT (ACCUMULATE ({{event_column}} OF ANY (S, M, E) DELIMITER '|') AS path)
) AS n
GROUP BY n.path
ORDER BY matches DESC;
```

### abandoned_cart — Middle event never followed by the end event

`M.N*$` matches from a cart event to the end of the partition through rows that are not the end event, so it finds carts never converted.

```sql
SELECT n.last_step, COUNT(*) AS abandoned
FROM NPATH (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{order_column}}
  USING
    MODE (NONOVERLAPPING)
    PATTERN ('M.N*$')
    SYMBOLS (
      {{event_column}} = '{{middle_event}}' AS M,
      {{event_column}} <> '{{end_event}}' AS N
    )
    RESULT (
      FIRST ({{partition_column}} OF M) AS cust,
      LAST ({{event_column}} OF ANY (M, N)) AS last_step
    )
) AS n
GROUP BY n.last_step
ORDER BY abandoned DESC;
```

### per_session — Paths to conversion within one session

Sessionize first and partition nPath by user and session, so a path never spans two visits.

```sql
SELECT TOP 20 n.path, COUNT(*) AS sessions
FROM NPATH (
  ON (
    SELECT s.{{partition_column}}, s.SESSIONID, s.{{order_column}}, s.{{event_column}}
    FROM SESSIONIZE (
      ON {{database}}.{{table}}
      PARTITION BY {{partition_column}}
      ORDER BY {{order_column}}
      USING TimeColumn ('{{order_column}}') TimeOut ({{timeout_seconds}})
    ) AS s
  )
  PARTITION BY {{partition_column}}, SESSIONID
  ORDER BY {{order_column}}
  USING
    MODE (NONOVERLAPPING)
    PATTERN ('^W*.E')
    SYMBOLS (
      {{event_column}} <> '{{end_event}}' AS W,
      {{event_column}} = '{{end_event}}' AS E
    )
    RESULT (ACCUMULATE ({{event_column}} OF ANY (W, E) DELIMITER ' > ') AS path)
) AS n
GROUP BY n.path
ORDER BY sessions DESC;
```

### sample_matches — Inspect individual matches

```sql
SELECT n.*
FROM NPATH (
  ON {{database}}.{{table}}
  PARTITION BY {{partition_column}}
  ORDER BY {{order_column}}
  USING
    MODE (NONOVERLAPPING)
    PATTERN ('S.W*.E')
    SYMBOLS (
      {{event_column}} = '{{start_event}}' AS S,
      {{event_column}} <> '{{end_event}}' AS W,
      {{event_column}} = '{{end_event}}' AS E
    )
    RESULT (
      FIRST ({{partition_column}} OF S) AS partition_key,
      FIRST ({{order_column}} OF S) AS first_ts,
      LAST ({{order_column}} OF E) AS last_ts,
      ACCUMULATE ({{event_column}} OF ANY (S, W, E) DELIMITER '|') AS path
    )
) AS n
SAMPLE 20;
```

## Common errors

- **9854 "NPATH requires exactly one MODE clause"** — MODE is not optional. Add `MODE (NONOVERLAPPING)` or `MODE (OVERLAPPING)`.
- **9134 "The symbol 'x' in the pattern is not defined in SYMBOL clause"** — a letter in PATTERN has no symbol. Declare it, or fix the typo.
- **9134 "Undeclared symbol"** — RESULT names a symbol (in `OF ANY (...)`) that is declared but not used in PATTERN. Name only symbols the pattern uses.
- **9134 "Invalid pattern"** — spaces in the pattern string, as in `'V . W* . P'`. Write `'V.W*.P'`.
- **3707 between 'AS' and a keyword** — a reserved word as a symbol name, such as `AS Exit`. Use single letters or non-reserved names.
- **An empty result** — a symbol value with the wrong case or spelling matches nothing and raises no error. Run discovery and symbol_validation.

## Practices

- Start with a short pattern such as `'S.E'`, check the count, then add quantifiers.
- `.` means immediately followed by; to allow other events in between, add a wildcard symbol and `W*`.
- Exclude the end event from the wildcard symbol, or a greedy `W*` will run a match through several end events.
- NONOVERLAPPING uses each row in at most one match; OVERLAPPING lets a new match start inside the previous one (view-to-purchase on the fixture: 263 matches against 763).
- Partition by user and SESSIONID when a path must stay within one visit.
- Wrap NPATH in an outer GROUP BY on the accumulated path to rank paths; use TOP outside, there is no LIMIT.
