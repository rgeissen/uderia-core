# UAF call syntax

How to call any TD_SYSFNLIB UAF function: SERIES_SPEC, MATRIX_SPEC, ART_SPEC, GENSERIES_SPEC, FUNC_PARAMS, INTO ART and TD_EXTRACT_RESULTS.

**Use for:** writing a first EXECUTE FUNCTION call for a time-series or signal function; feeding one function's result into the next; reading fit metrics or residuals from a result table; fixing a 3706 "Func_Param ... is not valid" or "Invalid layer name" error.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the series tables | `retail_demo` |
| `series_table` | Table with one row per series point | `daily_sales` |
| `seq_column` | Integer column that orders the points (SEQUENCE axis) | `seq_no` |
| `time_column` | DATE or TIMESTAMP column that orders the points (TIMECODE axis) | `sale_date` |
| `series_id` | Column that names the series a row belongs to | `store_id` |
| `value_column` | Numeric payload column | `revenue` |
| `art_name` | Name of an analytic result table (ART) a call writes | `smooth_art` |

## Statements

### series_tables — Build regular series from the orders

UAF functions expect one row per step and no gaps. The first table is daily revenue for 2024, days without orders filled from `sys_calendar.calendar` (366 rows, one series); the second is weekly revenue and order count per channel (three series of 86 weeks). Later statements read both.

```sql
CREATE TABLE {{database}}.{{series_table}} AS (
  SELECT c.calendar_date AS {{time_column}},
         ROW_NUMBER() OVER (ORDER BY c.calendar_date) AS {{seq_column}},
         CAST(1 AS INTEGER) AS {{series_id}},
         CAST(COALESCE(SUM(o.amount), 0) AS FLOAT) AS {{value_column}}
  FROM sys_calendar.calendar AS c
  LEFT JOIN {{database}}.orders AS o ON o.order_date = c.calendar_date
  WHERE c.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
  GROUP BY c.calendar_date
) WITH DATA PRIMARY INDEX ({{series_id}});

CREATE TABLE {{database}}.weekly_channel AS (
  SELECT ch.channel, w.week_no, w.week_start,
         CAST(COALESCE(SUM(o.amount), 0) AS FLOAT) AS revenue,
         CAST(COUNT(o.order_id) AS FLOAT) AS order_count
  FROM (SELECT (calendar_date - DATE '2024-01-01') / 7 + 1 AS week_no,
               MIN(calendar_date) AS week_start
        FROM sys_calendar.calendar
        WHERE calendar_date BETWEEN DATE '2024-01-01' AND DATE '2025-08-24'
        GROUP BY 1) AS w
  CROSS JOIN (SELECT DISTINCT channel FROM {{database}}.orders) AS ch
  LEFT JOIN {{database}}.orders AS o
    ON o.channel = ch.channel
   AND (o.order_date - DATE '2024-01-01') / 7 + 1 = w.week_no
  GROUP BY 1, 2, 3
) WITH DATA PRIMARY INDEX (channel);
```

### series_spec — ROW_AXIS as SEQUENCE or TIMECODE

TD_SINFO describes a series, a cheap check of a SERIES_SPEC. SEQUENCE takes an integer column, TIMECODE a DATE or TIMESTAMP; TD_SINFO reports the index type and sample interval (`1` versus `1 DAYS`). TABLE_NAME may name a table or a view.

```sql
EXECUTE FUNCTION TD_SINFO(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))));

EXECUTE FUNCTION TD_SINFO(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(TIMECODE({{time_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))));
```

### series_id_and_multivar — Several series in one call, several fields in one payload

Each distinct SERIES_ID value is a separate series, processed on its own; the result has one row set per series (three here). CONTENT(MULTIVAR_REAL) carries several fields per point (TD_LINEAR_REGR reads it as Y, X1, ...).

```sql
EXECUTE FUNCTION TD_SINFO(
  SERIES_SPEC(TABLE_NAME({{database}}.weekly_channel), ROW_AXIS(SEQUENCE(week_no)),
              SERIES_ID(channel), PAYLOAD(FIELDS(revenue, order_count), CONTENT(MULTIVAR_REAL))));
```

### interval — A calendar interval on a TIMECODE axis

INTERVAL states the step of a TIMECODE series; TD_SEASONALNORMALIZE needs it. With a SEQUENCE axis it is refused (3706 "INTERVAL is not compatible with ROW_AXIS field"). This call removes a weekly cycle from the daily series.

```sql
EXECUTE FUNCTION TD_SEASONALNORMALIZE(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(TIMECODE({{time_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL)), INTERVAL(CAL_DAYS(1))),
  FUNC_PARAMS(SEASON_CYCLE(CYCLES("CAL_DAYS"), DURATION(7))));
```

### func_params_and_index — FUNC_PARAMS, OUTPUT_FMT and the output index ROW_I

EXECUTE FUNCTION without INTO returns the rows directly. Parameters go in FUNC_PARAMS as NAME(value); enum values are bare words (`MA(MEAN)`). The output index column is ROW_I, counted from 0 (see into_art); OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)) puts the input axis values there instead (here the dates), so the result joins back to the source.

```sql
EXECUTE FUNCTION TD_SMOOTHMA(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(TIMECODE({{time_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(MA(MEAN), WINDOW(7), ONE_SIDED(1), PAD(0)),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)));
```

### into_art — Keep a result in an ART

INTO VOLATILE ART writes the result to a table that lives for the session. COLUMNS(... AS ...) between EXECUTE FUNCTION and INTO renames output columns. SELECT reads the ART like a table.

```sql
EXECUTE FUNCTION COLUMNS({{value_column}} AS {{value_column}}_ma7)
INTO VOLATILE ART({{art_name}})
TD_SMOOTHMA(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(MA(MEAN), WINDOW(7)));

SELECT TOP 5 * FROM {{art_name}} ORDER BY ROW_I;
```

### permanent_art — An ART that outlives the session

Without VOLATILE the ART is a table in the named database (TableKind `O` in DBC.TablesV) and is removed with DROP TABLE. A following call reads it through ART_SPEC.

```sql
EXECUTE FUNCTION INTO ART({{database}}.smooth_keep)
TD_SMOOTHMA(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(MA(MEAN), WINDOW(7)));

SELECT TableName, TableKind FROM DBC.TablesV
WHERE DatabaseName = '{{database}}' AND TableName = 'smooth_keep';

EXECUTE FUNCTION TD_SINFO(ART_SPEC(TABLE_NAME({{database}}.smooth_keep)));

DROP TABLE {{database}}.smooth_keep;
```

### extract_results — Read the other layers of an ART

A function can write several layers. SELECT on the ART returns the primary layer only; TD_EXTRACT_RESULTS returns any layer by name. TD_MAMEAN writes ARTFITMETADATA when FIT_METRICS(1) is set and ARTFITRESIDUALS when RESIDUALS(1) is set.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(mamean_art)
TD_MAMEAN(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS(8), ALGORITHM(MA), K_ORDER(7),
              PREDICTION_INTERVALS(BOTH), FIT_METRICS(1), RESIDUALS(1)));

-- Primary layer: OBSERVED_VALUE, FORECAST_VALUE and the 80/95 % bounds
SELECT TOP 8 * FROM mamean_art ORDER BY ROW_I DESC;

EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(mamean_art), LAYER(ARTFITMETADATA)));
EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(mamean_art), LAYER(ARTFITRESIDUALS)));
```

### art_chaining — Estimate, validate, forecast

ART_SPEC(TABLE_NAME(art)) hands a whole ART, model layer included, to the next function. TD_ARIMAESTIMATE requires CONSTANT and ALGORITHM; FIT_PERCENTAGE(70) keeps the last 30 % of points for TD_ARIMAVALIDATE (layer ARTVALDATA). The estimate ART also holds ARTMODEL, a binary MODEL_DATA layer.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(arima_est)
TD_ARIMAESTIMATE(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(NONSEASONAL(MODEL_ORDER(1, 0, 1)), CONSTANT(1), ALGORITHM(MLE),
              FIT_PERCENTAGE(70), FIT_METRICS(1), RESIDUALS(1)));

SELECT * FROM arima_est ORDER BY ROW_I;   -- AR1, MA1, MEAN coefficients

EXECUTE FUNCTION INTO VOLATILE ART(arima_val)
TD_ARIMAVALIDATE(ART_SPEC(TABLE_NAME(arima_est)),
                 FUNC_PARAMS(FIT_METRICS(1), RESIDUALS(1)));

EXECUTE FUNCTION INTO VOLATILE ART(arima_fc)
TD_ARIMAFORECAST(ART_SPEC(TABLE_NAME(arima_val)),
                 FUNC_PARAMS(FORECAST_PERIODS(12)));

SELECT * FROM arima_fc ORDER BY ROW_I;
```

### complex_payload — A COMPLEX series between two functions

TD_DFFT returns REAL_<field> and IMAG_<field>. To read them back, name both in an ART_SPEC payload with CONTENT(COMPLEX) (AMPL_PHASE_RADIANS and AMPL_PHASE_DEGREES are the polar forms). TD_IDFFT then returns the original values.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(fft_art)
TD_DFFT(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))));

EXECUTE FUNCTION TD_IDFFT(
  ART_SPEC(TABLE_NAME(fft_art),
           PAYLOAD(FIELDS(REAL_{{value_column}}, IMAG_{{value_column}}), CONTENT(COMPLEX))));
```

### genseries_spec — A generated series, no table

GENSERIES_SPEC generates start, start + step, ... for a number of entries; the payload column is `field0` and the series name column comes from INSTANCE_NAMES. TD_ACF accepts it; TD_SINFO and TD_SMOOTHMA refuse it (3706). Only one GENSERIES_SPEC is allowed per call.

```sql
EXECUTE FUNCTION TD_ACF(
  GENSERIES_SPEC(INSTANCE_NAMES('{"series_name": "ramp"}'),
                 DT(VARCHAR(30)),
                 GEN_PAYLOAD(1.0, 0.5, 100)),
  FUNC_PARAMS(MAXLAGS(3)));
```

### matrix_spec — A matrix: weeks by channel

MATRIX_SPEC adds COLUMN_AXIS and uses MATRIX_ID in place of SERIES_ID. TD_MINFO describes a matrix and reports WELL_FORM when every cell is present; functions such as TD_DFFT2 take the same spec.

```sql
CREATE TABLE {{database}}.weekly_matrix AS (
  SELECT CAST(1 AS INTEGER) AS matrix_id, week_no,
         CASE channel WHEN 'mobile' THEN 1 WHEN 'store' THEN 2 ELSE 3 END AS channel_no,
         revenue
  FROM {{database}}.weekly_channel
) WITH DATA PRIMARY INDEX (matrix_id);

EXECUTE FUNCTION TD_MINFO(
  MATRIX_SPEC(TABLE_NAME({{database}}.weekly_matrix),
              ROW_AXIS(SEQUENCE(week_no)), COLUMN_AXIS(SEQUENCE(channel_no)),
              MATRIX_ID(matrix_id), PAYLOAD(FIELDS(revenue), CONTENT(REAL))));
```

## Common errors

- **3706 "Func_Param X is not valid"** or **"Invalid enum value for X"** — a wrong parameter name or value. The message names it; for example TD_SIMPLEEXP has no MODEL, and TD_SMOOTHMA's MA takes EXPONENTIAL, not EXP.
- **3706 "Params: X value must be specified"** — a required parameter is missing (TD_ARIMAESTIMATE needs CONSTANT and ALGORITHM).
- **3706 "Func_Param SEASON_CYCLE is a record type"** — the parameter takes named fields: `SEASON_CYCLE(CYCLES("CAL_DAYS"), DURATION(7))`.
- **3706 "INTERVAL is not compatible with ROW_AXIS field"** — INTERVAL on a SEQUENCE axis. Use TIMECODE.
- **3706 "Invalid layer name specified"** or **"ART table does not contain data in ARTFITMETADATA layer"** — the function does not write that layer, or the call that wrote the ART lacked FIT_METRICS(1) / RESIDUALS(1).
- **3706 "If ART_SPEC 1 is reading multi layers, user must omit LAYER clause"** — drop LAYER(...) from an ART_SPEC given as function input.
- **9153 "Trying to open a handle on a layer which does not exist"** — TD_IDFFT given a SERIES_SPEC over a TD_DFFT ART. Use ART_SPEC.
- **3803 "Table '<art>' already exists"** — an ART name reused in the session. Use a new name or DROP TABLE the old one.
- **TD_SINFO with INTERVAL did not return** — on this system it ran past 150 seconds twice. Leave INTERVAL out where the function does not need it.

## Practices

- Build a regular series first, gaps filled from `sys_calendar.calendar`; TD_SINFO's NUM_ENTRIES and SAMPLE_INTERVAL confirm it.
- Try an unfamiliar call without INTO; add INTO ART once a later step needs the result.
- Use VOLATILE ARTs for intermediate steps, each with its own name.
- Treat parameter lists from other sources as unchecked; the error names the parameter it rejects.
