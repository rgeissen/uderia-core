# Attribution analysis

Multi-touch attribution of conversions to channels: journeys via XMLAGG, first/last/linear/time-decay/U-shaped credit in SQL, and the ATTRIBUTION operator.

**Use for:** deciding which channels or touchpoints get credit for a conversion; comparing attribution models side by side; reading the touchpoints that preceded each conversion; running Teradata's ATTRIBUTION function.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the touchpoint table | `retail_demo` |
| `table` | Touchpoint table, one row per interaction, conversions included | `events` |
| `partition_column` | Column that identifies the customer | `customer_id` |
| `timestamp_column` | Timestamp of the interaction | `event_ts` |
| `event_column` | Column whose value marks a conversion | `event_type` |
| `conversion_event` | Value of `event_column` that is a conversion | `purchase` |
| `channel_column` | Column holding the channel or touchpoint type to credit | `page` |
| `start_date` | First day of the journeys to explore | `2025-03-01` |
| `lookback_window_days` | Days before a conversion in which a touch can earn credit | `30` |
| `half_life_days` | Time-decay half-life: a touch this many days older gets half the weight | `7` |
| `window_seconds` | ATTRIBUTION window in seconds (the lookback window) | `2592000` |

## Statements

### journey_exploration — The ordered touch path of customers who converted

XMLAGG builds the path; there is no LISTAGG. XMLAGG separates items with a space, so append the separator to each item and trim the last one.

```sql
SELECT TOP 20
  {{partition_column}},
  COUNT(*) AS touchpoints,
  SUM(CASE WHEN {{event_column}} = '{{conversion_event}}' THEN 1 ELSE 0 END) AS conversions,
  TRIM(TRIM(TRAILING '>' FROM TRIM(XMLAGG(
    CASE WHEN {{event_column}} = '{{conversion_event}}' THEN 'CONVERSION' ELSE {{channel_column}} END || ' >'
    ORDER BY {{timestamp_column}}) (VARCHAR(8000))))) AS journey_path
FROM {{database}}.{{table}}
WHERE CAST({{timestamp_column}} AS DATE) >= DATE '{{start_date}}'
GROUP BY {{partition_column}}
HAVING SUM(CASE WHEN {{event_column}} = '{{conversion_event}}' THEN 1 ELSE 0 END) > 0
ORDER BY touchpoints DESC;
```

### model_comparison — First-touch, last-touch, linear, time-decay and U-shaped credit per channel

A journey is the touches since the customer's previous conversion and at most `lookback_window_days` before this conversion; a running count of conversions numbers the journeys, so no touch is credited to two conversions. Each model distributes exactly one unit of credit per converted journey, so every credit column sums to the number of conversions that had at least one touch in the window (`journey_quality` counts the rest). Weights use FLOAT: `1.0 / n` is DECIMAL arithmetic and truncates (1.0/3 returns 0.3). U-shaped gives 40% to the first and last touch and splits 20% across the middle; one touch gets 100%, two get 50% each.

```sql
WITH ev AS (
  SELECT {{partition_column}} AS cust, {{timestamp_column}} AS ts, {{channel_column}} AS channel,
    CASE WHEN {{event_column}} = '{{conversion_event}}' THEN 1 ELSE 0 END AS is_conv
  FROM {{database}}.{{table}}
), journeys AS (
  SELECT ev.*,
    SUM(is_conv) OVER (PARTITION BY cust ORDER BY ts ROWS UNBOUNDED PRECEDING) - is_conv AS journey_no
  FROM ev
), conv AS (
  SELECT cust, journey_no, ts AS conv_ts FROM journeys WHERE is_conv = 1
), touches AS (
  SELECT j.cust, j.journey_no, j.channel,
    ROW_NUMBER() OVER (PARTITION BY j.cust, j.journey_no ORDER BY j.ts) AS pos,
    COUNT(*) OVER (PARTITION BY j.cust, j.journey_no) AS n,
    CAST(((c.conv_ts - j.ts) MINUTE(4)) AS INTEGER) / 1440.0 AS age_days
  FROM journeys AS j
  JOIN conv AS c ON c.cust = j.cust AND c.journey_no = j.journey_no
  WHERE j.is_conv = 0
    AND j.ts >= c.conv_ts - CAST({{lookback_window_days}} AS INTERVAL DAY(4))
), weighted AS (
  SELECT t.*,
    EXP(-LN(2) * age_days / {{half_life_days}}) AS decay_raw,
    CASE WHEN n = 1 THEN CAST(1 AS FLOAT)
         WHEN n = 2 THEN CAST(0.5 AS FLOAT)
         WHEN pos = 1 OR pos = n THEN CAST(0.4 AS FLOAT)
         ELSE CAST(0.2 AS FLOAT) / (n - 2) END AS u_weight
  FROM touches AS t
)
SELECT channel,
  SUM(CASE WHEN pos = 1 THEN 1 ELSE 0 END) AS first_touch,
  SUM(CASE WHEN pos = n THEN 1 ELSE 0 END) AS last_touch,
  CAST(SUM(CAST(1 AS FLOAT) / n) AS DECIMAL(10,2)) AS linear_credit,
  CAST(SUM(decay_raw / decay_sum) AS DECIMAL(10,2)) AS time_decay_credit,
  CAST(SUM(u_weight) AS DECIMAL(10,2)) AS u_shaped_credit
FROM (
  SELECT w.*, SUM(decay_raw) OVER (PARTITION BY cust, journey_no) AS decay_sum
  FROM weighted AS w
) AS x
GROUP BY channel
ORDER BY linear_credit DESC;
```

### attribution_function — The ATTRIBUTION table operator

ATTRIBUTION (TD_SYSFNLIB) needs a second and third input: a table listing the conversion event values (`ConversionEventTable`) and a model table (`FirstModelTable`, rows id 0 = model type, id 1 = model). With a single ON clause it fails with Error 9854. It returns every input row inside a window with an `attribution` credit (NULL on the conversion rows) and `time_to_conversion` in seconds, negative. Here the event column carries the channel for touches and the conversion value for conversions. The window counts back from each conversion and stops at the previous one; `rows:10` keeps at most the last ten touches, so first-click results differ from the SQL above for longer journeys. Model values accepted here: `UNIFORM:NA`, `FIRST_CLICK:NA`, `LAST_CLICK:NA`, `EXPONENTIAL:0.5,DAY` (also `HOUR`).

```sql
CREATE VOLATILE TABLE attr_conversion (conversion_event VARCHAR(30))
ON COMMIT PRESERVE ROWS;
INSERT INTO attr_conversion VALUES ('{{conversion_event}}');

CREATE VOLATILE TABLE attr_model (id INTEGER, model VARCHAR(100))
ON COMMIT PRESERVE ROWS;
INSERT INTO attr_model VALUES (0, 'SIMPLE');
INSERT INTO attr_model VALUES (1, 'UNIFORM:NA');

SELECT touch AS channel,
  CAST(SUM(attribution) AS DECIMAL(10,2)) AS credit,
  COUNT(*) AS touches_in_windows
FROM ATTRIBUTION (
  ON (
    SELECT {{partition_column}}, {{timestamp_column}},
      CASE WHEN {{event_column}} = '{{conversion_event}}' THEN {{event_column}} ELSE {{channel_column}} END AS touch
    FROM {{database}}.{{table}}
  ) AS t PARTITION BY {{partition_column}} ORDER BY {{timestamp_column}}
  ON attr_conversion AS ConversionEventTable DIMENSION
  ON attr_model AS FirstModelTable DIMENSION
  USING
    EventColumn ('touch')
    TimeColumn ('{{timestamp_column}}')
    WindowSize ('rows:10&seconds:{{window_seconds}}')
) AS dt
WHERE touch <> '{{conversion_event}}'
GROUP BY 1
ORDER BY credit DESC;
```

### journey_quality — Conversions with no creditable touch, and one-touch journeys

Run this before trusting a model: conversions with no touch in the window get no credit in any model, and a high share of one-touch journeys makes all models agree.

```sql
WITH ev AS (
  SELECT {{partition_column}} AS cust, {{timestamp_column}} AS ts,
    CASE WHEN {{event_column}} = '{{conversion_event}}' THEN 1 ELSE 0 END AS is_conv
  FROM {{database}}.{{table}}
), journeys AS (
  SELECT ev.*,
    SUM(is_conv) OVER (PARTITION BY cust ORDER BY ts ROWS UNBOUNDED PRECEDING) - is_conv AS journey_no
  FROM ev
), per_conversion AS (
  SELECT c.cust, c.journey_no, COUNT(t.ts) AS touches
  FROM journeys AS c
  LEFT JOIN journeys AS t
    ON t.cust = c.cust AND t.journey_no = c.journey_no AND t.is_conv = 0
   AND t.ts >= c.ts - CAST({{lookback_window_days}} AS INTERVAL DAY(4))
  WHERE c.is_conv = 1
  GROUP BY c.cust, c.journey_no
)
SELECT
  COUNT(*) AS conversions,
  SUM(CASE WHEN touches = 0 THEN 1 ELSE 0 END) AS no_touch_in_window,
  SUM(CASE WHEN touches = 1 THEN 1 ELSE 0 END) AS one_touch,
  CAST(AVG(CAST(touches AS FLOAT)) AS DECIMAL(6,2)) AS avg_touches
FROM per_conversion;
```

## Common errors

- **9854 "ATTRIBUTION requires multiple ON clauses"** — the single-input form with conversion values in USING is not available. Pass the conversion values and the model as DIMENSION tables.
- **9134 "The model must be specified as 'K:WEIGHT:MODEL:PARAMETERS'..."** — an exponential model written `EXPONENTIAL:0.5:DAY`. Separate the parameters with a comma: `EXPONENTIAL:0.5,DAY`.
- **9134 "number of parameters must be equal to the segment window size"** — a `WEIGHTED` model needs one weight per row of the `rows:K` window.
- **`EXPONENTIAL:0.5,SECOND` returns NaN credits** — the decay underflows over minutes-long gaps; use `HOUR` or `DAY`.
- **Credits that do not add up to the conversions** — `1.0 / n` in DECIMAL truncates; cast to FLOAT before dividing.
- **Touches credited to the wrong conversion or twice** — joining every earlier touch to every conversion. Number the journeys with a running count of conversions, as in `model_comparison`.
- **3706 "Data Type ... does not match a Defined Type name" on LISTAGG** — there is no LISTAGG; use XMLAGG.

## Practices

- Compare at least first-touch, last-touch and linear; channels whose credit changes most between models depend on the model choice.
- Check that each model's credits sum to the number of converted journeys.
- Set the lookback window to the purchase cycle: days for retail, months for business sales.
- To attribute revenue, multiply each touch's credit by the conversion's value before summing.
- Segment by customer type or product before comparing channels; journeys differ by segment.
