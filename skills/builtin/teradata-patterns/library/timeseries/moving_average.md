# Moving average

Trailing and exponential moving averages with AVG OVER and a recursive WITH, crossover signals, and the UAF functions TD_SMOOTHMA, TD_MAMEAN, TD_SIMPLEEXP.

**Use for:** smoothing a daily or weekly series; comparing a short and a long average to find trend changes; exponential smoothing; a flat short-horizon forecast from a moving average or exponential smoothing; choosing between window SQL and a UAF function.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the series table | `retail_demo` |
| `table` | Table with one row per series and period | `channel_sales` |
| `value_column` | Numeric column to average | `revenue` |
| `time_column` | Date column that orders the periods | `sale_date` |
| `seq_column` | Integer period number within a series (1, 2, ...) | `seq_no` |
| `partition_column` | Column naming each series (PARTITION BY, SERIES_ID) | `channel` |
| `window_size` | Number of periods in the window | `7` |
| `window_preceding` | `window_size` minus 1, for ROWS ... PRECEDING | `6` |
| `forecast_periods` | Periods to forecast | `8` |
| `alpha` | Weight of the newest value in exponential smoothing (0 to 1) | `0.3` |

## Statements

### daily_series — A gap-free daily series per channel

A moving average over ROWS counts rows, not days: a day without orders must exist as a row or the window spans more than its nominal days. This builds daily revenue per channel for 2024-01-01 to 2025-08-23 from the orders, filling missing days from `sys_calendar.calendar` (601 rows per channel). The statements below read it.

```sql
CREATE TABLE {{database}}.{{table}} AS (
  SELECT ch.{{partition_column}},
         c.calendar_date AS {{time_column}},
         ROW_NUMBER() OVER (PARTITION BY ch.{{partition_column}} ORDER BY c.calendar_date) AS {{seq_column}},
         CAST(COALESCE(SUM(o.amount), 0) AS FLOAT) AS {{value_column}}
  FROM sys_calendar.calendar AS c
  CROSS JOIN (SELECT DISTINCT channel AS {{partition_column}} FROM {{database}}.orders) AS ch
  LEFT JOIN {{database}}.orders AS o
    ON o.order_date = c.calendar_date AND o.channel = ch.{{partition_column}}
  WHERE c.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2025-08-23'
  GROUP BY ch.{{partition_column}}, c.calendar_date
) WITH DATA PRIMARY INDEX ({{partition_column}});
```

### simple_moving_average — Trailing average per series

The first `window_size - 1` rows of each series average fewer rows. The row count in the window identifies them; this statement returns NULL for them instead of a partial average.

```sql
SELECT {{partition_column}}, {{time_column}}, {{value_column}},
  CASE WHEN COUNT(*) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                           ROWS BETWEEN {{window_preceding}} PRECEDING AND CURRENT ROW) = {{window_size}}
       THEN AVG({{value_column}}) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                                       ROWS BETWEEN {{window_preceding}} PRECEDING AND CURRENT ROW)
  END AS ma_{{window_size}}
FROM {{database}}.{{table}}
ORDER BY {{partition_column}}, {{time_column}};
```

### multiple_windows — Short, medium and long averages side by side

```sql
SELECT {{partition_column}}, {{time_column}}, {{value_column}},
  AVG({{value_column}}) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                              ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma_7,
  AVG({{value_column}}) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                              ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS ma_30,
  AVG({{value_column}}) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                              ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) AS ma_90
FROM {{database}}.{{table}}
ORDER BY {{partition_column}}, {{time_column}};
```

### exponential_moving_average — EMA with a recursive WITH

EMA(t) = alpha * value(t) + (1 - alpha) * EMA(t - 1), started at the first value. A window function cannot refer to its own previous result, so the recursion walks the series by period number.

```sql
WITH RECURSIVE ema ({{partition_column}}, {{seq_column}}, {{time_column}}, {{value_column}}, ema_value) AS (
  SELECT {{partition_column}}, {{seq_column}}, {{time_column}}, {{value_column}}, {{value_column}}
  FROM {{database}}.{{table}}
  WHERE {{seq_column}} = 1
  UNION ALL
  SELECT t.{{partition_column}}, t.{{seq_column}}, t.{{time_column}}, t.{{value_column}},
         {{alpha}} * t.{{value_column}} + (1 - {{alpha}}) * e.ema_value
  FROM ema AS e
  JOIN {{database}}.{{table}} AS t
    ON t.{{partition_column}} = e.{{partition_column}}
   AND t.{{seq_column}} = e.{{seq_column}} + 1
)
SELECT {{partition_column}}, {{time_column}}, {{value_column}}, ema_value
FROM ema
ORDER BY {{partition_column}}, {{time_column}};
```

### crossover — Days the 7-day average crosses the 30-day average

An analytic function cannot be nested in another, so LAG reads the averages from a previous step. Rows before the long window is full are left out.

```sql
WITH ma AS (
  SELECT {{partition_column}}, {{time_column}},
    AVG({{value_column}}) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                                ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma_short,
    AVG({{value_column}}) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                                ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS ma_long,
    COUNT(*) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}
                   ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS n_long
  FROM {{database}}.{{table}}
), lagged AS (
  SELECT ma.*,
    LAG(ma_short) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}) AS prev_short,
    LAG(ma_long) OVER (PARTITION BY {{partition_column}} ORDER BY {{time_column}}) AS prev_long
  FROM ma
)
SELECT {{partition_column}}, {{time_column}}, ma_short, ma_long,
  CASE WHEN ma_short > ma_long THEN 'crossed above' ELSE 'crossed below' END AS crossing
FROM lagged
WHERE n_long = 30
  AND ((ma_short > ma_long AND prev_short <= prev_long)
    OR (ma_short < ma_long AND prev_short >= prev_long))
ORDER BY {{partition_column}}, {{time_column}};
```

### uaf_smoothma — TD_SMOOTHMA: trailing, centred, median and preset averages

TD_SMOOTHMA's output column keeps the input field name. By default the window is centred and the edge points are dropped. ONE_SIDED(1) with PAD(0) gives the trailing average, with the first `window_size - 1` points set to 0; with OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)) and a TIMECODE axis the index is the date, so it joins back to the table. On this series it equals the AVG OVER result to within 1e-12. MA also takes MEDIAN and CUMULATIVE, and WELL_KNOWN selects a weighted preset such as "5MA", "2x12MA", "3x5MA", "S21MA" or "H23MA".

```sql
EXECUTE FUNCTION COLUMNS({{value_column}} AS ma_{{window_size}})
INTO VOLATILE ART(sma_art)
TD_SMOOTHMA(
  SERIES_SPEC(TABLE_NAME({{database}}.{{table}}), ROW_AXIS(TIMECODE({{time_column}})),
              SERIES_ID({{partition_column}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(MA(MEAN), WINDOW({{window_size}}), ONE_SIDED(1), PAD(0)),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)));

SELECT t.{{partition_column}}, t.{{time_column}}, t.{{value_column}}, a.ma_{{window_size}}
FROM {{database}}.{{table}} AS t
JOIN sma_art AS a
  ON a.{{partition_column}} = t.{{partition_column}} AND a.ROW_I = t.{{time_column}}
WHERE t.{{seq_column}} >= {{window_size}}
ORDER BY 1, 2;

-- Centred median, padded at the edges
EXECUTE FUNCTION TD_SMOOTHMA(
  SERIES_SPEC(TABLE_NAME({{database}}.{{table}}), ROW_AXIS(TIMECODE({{time_column}})),
              SERIES_ID({{partition_column}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(MA(MEDIAN), WINDOW({{window_size}}), PAD(0)),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)));

-- A preset weighted average; it replaces WINDOW
EXECUTE FUNCTION TD_SMOOTHMA(
  SERIES_SPEC(TABLE_NAME({{database}}.{{table}}), ROW_AXIS(TIMECODE({{time_column}})),
              SERIES_ID({{partition_column}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(MA(MEAN), WELL_KNOWN("2x12MA")),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)));
```

### uaf_smoothma_exponential — TD_SMOOTHMA: exponential smoothing

MA(EXPONENTIAL) takes LAMBDA, the weight of the newest value, and no WINDOW. It equals the recursive EMA above with alpha = LAMBDA (difference below 1e-12 on this series).

```sql
EXECUTE FUNCTION COLUMNS({{value_column}} AS ema)
TD_SMOOTHMA(
  SERIES_SPEC(TABLE_NAME({{database}}.{{table}}), ROW_AXIS(TIMECODE({{time_column}})),
              SERIES_ID({{partition_column}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(MA(EXPONENTIAL), LAMBDA({{alpha}})),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)));
```

### uaf_mamean_forecast — TD_MAMEAN: flat forecast from a moving average

ALGORITHM(MA) forecasts every future period with the mean of the last K_ORDER values (here the last 7 days of each channel); MEAN uses the whole series, NAIVE the last value. The primary layer has OBSERVED_VALUE, FORECAST_VALUE and, with PREDICTION_INTERVALS(BOTH), the 80 % and 95 % bounds; ROW_I counts from 0, so for 601 days the forecasts are ROW_I 601 to 608. TD_MAMEAN refuses OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)).

```sql
EXECUTE FUNCTION INTO VOLATILE ART(mamean_art)
TD_MAMEAN(
  SERIES_SPEC(TABLE_NAME({{database}}.{{table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{partition_column}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), ALGORITHM(MA), K_ORDER({{window_size}}),
              PREDICTION_INTERVALS(BOTH), FIT_METRICS(1), RESIDUALS(1)));

SELECT * FROM mamean_art WHERE OBSERVED_VALUE IS NULL ORDER BY 1, 2;

EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(mamean_art), LAYER(ARTFITMETADATA)));
```

### uaf_simpleexp_forecast — TD_SIMPLEEXP: flat forecast from exponential smoothing

ALPHA weights the newest value. The fitted value for a period is the smoothed level up to the period before, so it trails the TD_SMOOTHMA exponential series by one step. Without ALPHA each fitted value equals the previous observation. FORECAST_STARTING_VALUE('MEAN') starts the level from an average of the opening values instead of the first value. TD_SIMPLEEXP accepts FLOW_THROUGH, so ROW_I is the period number.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(simpleexp_art)
TD_SIMPLEEXP(
  SERIES_SPEC(TABLE_NAME({{database}}.{{table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{partition_column}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), ALPHA({{alpha}}),
              FORECAST_STARTING_VALUE('MEAN'), PREDICTION_INTERVALS(BOTH),
              FIT_METRICS(1), RESIDUALS(1)),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)));

SELECT * FROM simpleexp_art WHERE OBSERVED_VALUE IS NULL ORDER BY 1, 2;

EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(simpleexp_art), LAYER(ARTFITRESIDUALS)));
```

## Common errors

- **A window that spans more days than intended** — missing days in the source. Fill them from `sys_calendar.calendar` before averaging (daily_series).
- **Partial averages at the start of a series** — fewer than `window_size` rows yet. Filter or null them with COUNT(*) over the same window.
- **3706 "Invalid enum value for MA"** — `MA(EXP)`. Write `MA(EXPONENTIAL)`.
- **3706 "Error in function TD_SMOOTHMA: Missing MA Parameters"** — WELL_KNOWN without MA(MEAN).
- **3706 "Error in function TD_MAMEAN: Missing K_ORDER"** — ALGORITHM(MA) needs K_ORDER.
- **3706 "Improper OUTPUT_FMT value passed in"** — TD_MAMEAN with INDEX_STYLE(FLOW_THROUGH). Leave OUTPUT_FMT out; ROW_I then counts from 0.
- **3706 "Func_Param MODEL is not valid"** — TD_SIMPLEEXP has no MODEL parameter.
- **A forecast that copies the last value** — TD_SIMPLEEXP without ALPHA. Set ALPHA.

## Practices

- Average over a gap-free series; a ROWS window counts rows, not days.
- A larger window is smoother and lags more; a smaller one reacts faster and is noisier.
- Use AVG OVER when the averages feed further SQL; use TD_SMOOTHMA for medians, presets or many series at once, and join it back with FLOW_THROUGH on a TIMECODE axis.
- TD_MAMEAN and TD_SIMPLEEXP give one flat value for every future period; they model no trend or season. Use TD_HOLT_WINTERS_FORECASTER or the ARIMA functions when either is present.
- Set FIT_METRICS(1) to compare forecasters on MAE, MSE and R_SQUARE in ARTFITMETADATA.
