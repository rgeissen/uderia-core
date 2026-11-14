# ARIMA forecasting

TD_ARIMAESTIMATE, TD_ARIMAVALIDATE, TD_ARIMAFORECAST and TD_AUTOARIMA calls, chained through volatile ART tables, plus SARIMA and TD_DICKEY_FULLER.

**Use for:** forecasting a regular numeric series (sales, orders, traffic); choosing an ARIMA order by hand or automatically; checking a model on a holdout before forecasting; a series with a weekly or yearly cycle.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the source data and the series tables | `retail_demo` |
| `series_table` | Table the statements build to hold one regular series | `weekly_revenue` |
| `seq_column` | Integer position of each point, 1..n, no gaps | `week_no` |
| `series_id` | Column that identifies each series in the table | `series_id` |
| `value_column` | Numeric column to model | `revenue` |
| `p_order` | AR order | `1` |
| `d_order` | Differencing order | `0` |
| `q_order` | MA order | `1` |
| `algorithm` | TD_ARIMAESTIMATE method: MLE, CSS, CSS_MLE, or OLE (AR terms only) | `MLE` |
| `fit_percentage` | Share of the series used to fit; the rest is the holdout for TD_ARIMAVALIDATE | `80` |
| `forecast_periods` | Number of future points to forecast | `8` |
| `seasonal_period` | Points in one seasonal cycle | `7` |

## Statements

### weekly_series — Build a regular weekly series from orders

UAF functions need one row per point and no gaps. This business series sums `orders.amount` per full week (Sunday to Saturday); `sys_calendar.calendar` supplies weeks without orders.

```sql
CREATE TABLE {{database}}.{{series_table}} AS (
  SELECT 1 AS {{series_id}},
         ROW_NUMBER() OVER (ORDER BY MIN(c.calendar_date)) AS {{seq_column}},
         MIN(c.calendar_date) AS week_start,
         CAST(COALESCE(SUM(o.amount), 0) AS FLOAT) AS {{value_column}}
  FROM sys_calendar.calendar AS c
  LEFT JOIN {{database}}.orders AS o ON o.order_date = c.calendar_date
  WHERE c.calendar_date BETWEEN DATE '2024-01-07' AND DATE '2025-08-23'
  GROUP BY c.week_of_calendar
) WITH DATA PRIMARY INDEX ({{series_id}});

SELECT COUNT(*) AS weeks, AVG({{value_column}}) AS mean_revenue FROM {{database}}.{{series_table}};
```

### seasonal_series — Build a synthetic daily series with a weekly cycle

A deterministic series with a trend, a weekly cycle (`day_of_week` 1 is Sunday) and noise from `(day_of_calendar * 7919) MOD 13`; 364 days are 52 weeks.

```sql
CREATE TABLE {{database}}.synthetic_daily AS (
  SELECT 1 AS {{series_id}}, c.calendar_date AS obs_date,
         c.calendar_date - DATE '2024-01-01' + 1 AS day_no,
         CAST(200 + 0.25 * (c.calendar_date - DATE '2024-01-01')
              + CASE c.day_of_week WHEN 1 THEN -30 WHEN 6 THEN 25 WHEN 7 THEN 40 ELSE 0 END
              + ((c.day_of_calendar * 7919) MOD 13) - 6 AS FLOAT) AS value_amt
  FROM sys_calendar.calendar AS c
  WHERE c.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-29'
) WITH DATA PRIMARY INDEX ({{series_id}});
```

### stationarity_check — Unit-root test before choosing d

NULL_HYP `REJECT` means no unit root (the series can be modelled with d = 0); `ACCEPT` means difference it. ALGORITHM is NONE, DRIFT or DRIFTNTREND. Here the weekly revenue rejects with DRIFT; the trending synthetic series accepts with NONE.

```sql
EXECUTE FUNCTION TD_DICKEY_FULLER(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(ALGORITHM(DRIFT)));

EXECUTE FUNCTION TD_DICKEY_FULLER(
  SERIES_SPEC(TABLE_NAME({{database}}.synthetic_daily), ROW_AXIS(SEQUENCE(day_no)),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS(value_amt), CONTENT(REAL))),
  FUNC_PARAMS(ALGORITHM(NONE)));

-- A plain-SQL look at the level: monthly mean and its change
SELECT TRUNC(week_start, 'MM') AS month_start, AVG({{value_column}}) AS monthly_avg,
       monthly_avg - LAG(monthly_avg) OVER (ORDER BY month_start) AS change_vs_prior
FROM {{database}}.{{series_table}}
GROUP BY 1
ORDER BY 1;
```

### arima_estimate — Estimate coefficients and keep a holdout (step 1)

CONSTANT is required (Error 3706 without it). With FIT_PERCENTAGE below 100 the rest of the series goes to the ARTVALDATA layer for validation. The primary layer holds the coefficients (AR1, MA1, MEAN); TD_EXTRACT_RESULTS reads the other layers.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(arima_est)
TD_ARIMAESTIMATE(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(NONSEASONAL(MODEL_ORDER({{p_order}}, {{d_order}}, {{q_order}})),
              ALGORITHM({{algorithm}}), CONSTANT(1),
              FIT_PERCENTAGE({{fit_percentage}}), FIT_METRICS(1), RESIDUALS(1)));

SELECT * FROM arima_est;

-- R_SQUARE, MAE, MSE, MAPE, F test of the fit
EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(arima_est), LAYER(ARTFITMETADATA)));
-- The held-out points (ARTFITRESIDUALS holds the fitted ones)
EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(arima_est), LAYER(ARTVALDATA)));
```

### arima_validate — Score the model on the holdout (step 2)

Reads the ART of step 1. The primary layer holds AIC, SBIC, HQIC and MSE on the holdout; compare these across candidate orders. TD_EXTRACT_RESULTS can write a layer into a volatile ART that plain SQL then reads, here for error metrics.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(arima_val)
TD_ARIMAVALIDATE(ART_SPEC(TABLE_NAME(arima_est)), FUNC_PARAMS(FIT_METRICS(1), RESIDUALS(1)));

SELECT * FROM arima_val;

EXECUTE FUNCTION INTO VOLATILE ART(val_resid)
TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(arima_val), LAYER(ARTFITRESIDUALS)));

SELECT COUNT(*) AS n_points, AVG(RESIDUAL) AS mean_error, AVG(ABS(RESIDUAL)) AS mae,
       SQRT(AVG(RESIDUAL * RESIDUAL)) AS rmse,
       AVG(ABS(RESIDUAL / NULLIFZERO(ACTUAL_VALUE))) * 100 AS mape
FROM val_resid;
```

### arima_forecast — Forecast from the validated model (step 3)

ART_SPEC takes TABLE_NAME only. PREDICTION_INTERVALS is NONE, "80", "95" or BOTH (the default); the numbers must be quoted. The forecast's ROW_I starts at 0, so date it from the last observation.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(arima_fcst)
TD_ARIMAFORECAST(ART_SPEC(TABLE_NAME(arima_val)),
                 FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), PREDICTION_INTERVALS(BOTH)));

SELECT f.ROW_I, l.last_week + 7 * (f.ROW_I + 1) AS week_start,
       f.FORECAST_VALUE, f.LO_80, f.HI_80, f.LO_95, f.HI_95
FROM arima_fcst AS f
CROSS JOIN (SELECT MAX(week_start) AS last_week FROM {{database}}.{{series_table}}) AS l
ORDER BY f.ROW_I;
```

### fitted_and_forecast — Fit on the whole series, keep the model, forecast

With FIT_PERCENTAGE(100) there is no holdout and TD_ARIMAFORECAST reads the estimate directly; below 100 it refuses (Error 9153). INTO ART without VOLATILE writes a table (TableKind O) that later sessions can forecast from; drop it when done.

```sql
EXECUTE FUNCTION INTO ART({{database}}.arima_model)
TD_ARIMAESTIMATE(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), ROW_AXIS(SEQUENCE({{seq_column}})),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(NONSEASONAL(MODEL_ORDER({{p_order}}, {{d_order}}, {{q_order}})),
              ALGORITHM({{algorithm}}), CONSTANT(1), FIT_PERCENTAGE(100), RESIDUALS(1)));

-- The fitted value for every point
EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME({{database}}.arima_model), LAYER(ARTFITRESIDUALS)));

EXECUTE FUNCTION TD_ARIMAFORECAST(ART_SPEC(TABLE_NAME({{database}}.arima_model)),
                                  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), PREDICTION_INTERVALS(NONE)));

DROP TABLE {{database}}.arima_model;
```

### auto_arima — Let TD_AUTOARIMA pick the order

It searches orders up to the given maxima and keeps the best by INFOR_CRITERIA (AIC, AICC or BIC; SBIC is refused). On the synthetic series it finds the weekly cycle (ARTICANDORDER reads `ARIMA(2, 0, 0)(0, 1, 1)[7]`); on the weekly revenue it picks ARIMA(0, 0, 0), a constant mean. Forecast directly; TD_ARIMAVALIDATE refuses an AUTOARIMA ART.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(auto_art)
TD_AUTOARIMA(
  SERIES_SPEC(TABLE_NAME({{database}}.synthetic_daily), ROW_AXIS(SEQUENCE(day_no)),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS(value_amt), CONTENT(REAL))),
  FUNC_PARAMS(MAX_PQ_NONSEASONAL(2, 2), MAX_PQ_SEASONAL(1, 1), PERIOD({{seasonal_period}}),
              INFOR_CRITERIA(AIC), STEPWISE(1)));

SELECT * FROM auto_art;
EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(auto_art), LAYER(ARTICANDORDER)));

EXECUTE FUNCTION TD_ARIMAFORECAST(ART_SPEC(TABLE_NAME(auto_art)),
                                  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}})));
```

### seasonal_arima — SARIMA with a SEASONAL model order

SEASONAL(MODEL_ORDER(P, D, Q), PERIOD(s)) adds the seasonal terms (SMA1 here); this is the order TD_AUTOARIMA chose. With differencing no constant term is estimated, whether CONSTANT is 1 or 0. The forecast repeats the weekly pattern. An MA coefficient at -1 signals one difference too many: (1, 1, 1)(0, 1, 1) on this series gives MA1 = -1.0.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(sarima_est)
TD_ARIMAESTIMATE(
  SERIES_SPEC(TABLE_NAME({{database}}.synthetic_daily), ROW_AXIS(SEQUENCE(day_no)),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS(value_amt), CONTENT(REAL))),
  FUNC_PARAMS(NONSEASONAL(MODEL_ORDER(2, 0, 0)),
              SEASONAL(MODEL_ORDER(0, 1, 1), PERIOD({{seasonal_period}})),
              CONSTANT(0), ALGORITHM(MLE), FIT_PERCENTAGE(100)));

SELECT * FROM sarima_est;

EXECUTE FUNCTION TD_ARIMAFORECAST(ART_SPEC(TABLE_NAME(sarima_est)),
                                  FUNC_PARAMS(FORECAST_PERIODS({{seasonal_period}}), PREDICTION_INTERVALS("95")));
```

### normalized_arima — Remove the cycle, model, then restore it

TD_SEASONALNORMALIZE turns each point into a z-score against its cycle position (ARTMETADATA holds MEAN_ and SD_ per position); ARIMA models what is left; TD_UNNORMALIZE restores the scale. TD_UNNORMALIZE assigns cycle positions from the first row of its input, not from the dates: the training series is 52 whole weeks, so the first forecast day is position 1.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(norm_series)
TD_SEASONALNORMALIZE(
  SERIES_SPEC(TABLE_NAME({{database}}.synthetic_daily), ROW_AXIS(TIMECODE(obs_date)),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS(value_amt), CONTENT(REAL)),
              INTERVAL(CAL_DAYS(1))),
  FUNC_PARAMS(SEASON_CYCLE(CYCLES("CAL_DAYS"), DURATION({{seasonal_period}}))));

EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(norm_series), LAYER(ARTMETADATA)));

EXECUTE FUNCTION INTO VOLATILE ART(norm_est)
TD_ARIMAESTIMATE(
  SERIES_SPEC(TABLE_NAME(norm_series), ROW_AXIS(SEQUENCE(ROW_I)),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS(value_amt), CONTENT(REAL))),
  FUNC_PARAMS(NONSEASONAL(MODEL_ORDER(1, 1, 1)), CONSTANT(0), ALGORITHM(MLE), FIT_PERCENTAGE(100)));

EXECUTE FUNCTION INTO VOLATILE ART(norm_fcst)
TD_ARIMAFORECAST(ART_SPEC(TABLE_NAME(norm_est)),
                 FUNC_PARAMS(FORECAST_PERIODS({{seasonal_period}}), PREDICTION_INTERVALS(NONE)));

-- TD_UNNORMALIZE needs a dated series: label the forecast with the following days
CREATE VOLATILE TABLE norm_forecast AS (
  SELECT {{series_id}}, DATE '2024-12-30' + CAST(ROW_I AS INTEGER) AS obs_date, FORECAST_VALUE AS value_amt
  FROM norm_fcst
) WITH DATA ON COMMIT PRESERVE ROWS;

EXECUTE FUNCTION TD_UNNORMALIZE(
  SERIES_SPEC(TABLE_NAME(norm_forecast), ROW_AXIS(TIMECODE(obs_date)),
              SERIES_ID({{series_id}}), PAYLOAD(FIELDS(value_amt), CONTENT(REAL)),
              INTERVAL(CAL_DAYS(1))),
  ART_SPEC(TABLE_NAME(norm_series), LAYER(ARTMETADATA),
           PAYLOAD(FIELDS(MEAN_value_amt, SD_value_amt), CONTENT(MULTIVAR_REAL))),
  INPUT_FMT(INPUT_MODE(MATCH)),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)));
```

## Common errors

- **3706 "Params: CONSTANT value must be specified"** — TD_ARIMAESTIMATE without CONSTANT. Add CONSTANT(1) or CONSTANT(0).
- **3706 "Invalid enum value for ALGORITHM"** — `MLE_CSS`. The values are MLE, CSS, CSS_MLE, OLE.
- **9153 "Unexpected MA model parameter when using OLE method"** — OLE fits AR terms only. Set q to 0 or use MLE/CSS.
- **3706 "Invalid enum value for PREDICTION_INTERVALS"** — `PREDICTION_INTERVALS(80)` or `(95)`. Quote them: `"80"`, `"95"`; or NONE, BOTH.
- **9153 "TD_ARIMAFORECAST expects TD_ARIMAESTIMATE or TD_AUTOARIMA art with 100 as FIT_PERCENTAGE"** — the estimate kept a holdout. Run TD_ARIMAVALIDATE first, or estimate with FIT_PERCENTAGE(100).
- **9153 "FLOW_THROUGH is not available …"** — OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH)) on TD_ARIMAVALIDATE or TD_ARIMAFORECAST after an estimate without it. Drop the OUTPUT_FMT.
- **3706 "If ART_SPEC 1 is reading multi layers, user must omit LAYER clause"** — LAYER in TD_ARIMAFORECAST's ART_SPEC. Use TABLE_NAME only.
- **3706 "Encountered TD_AUTOARIMA's output … There is no need to do validation"** — TD_ARIMAVALIDATE on a TD_AUTOARIMA ART. Forecast directly.
- **3706 "ART table does not contain data in ARTFITMETADATA layer"** — the layer was not produced (TD_AUTOARIMA, or no FIT_METRICS(1)).

## Practices

- Build the series as one row per point with a gap-free integer sequence; fill missing periods from sys_calendar.calendar.
- Test for a unit root with TD_DICKEY_FULLER before choosing d; difference only when it does not reject.
- Use INTO VOLATILE ART for intermediate models; each step reads the previous ART through ART_SPEC(TABLE_NAME(...)).
- Keep a holdout (FIT_PERCENTAGE below 100) and compare candidate orders on TD_ARIMAVALIDATE's AIC and MSE.
- Use TD_AUTOARIMA to explore; if its choice is ARIMA(0, 0, 0) the series has no structure ARIMA can use and the forecast is the mean.
- For a cycle, either add SEASONAL(MODEL_ORDER(...), PERIOD(s)) or normalize with TD_SEASONALNORMALIZE and restore with TD_UNNORMALIZE.
- Map a forecast's ROW_I (from 0) back to dates yourself.
