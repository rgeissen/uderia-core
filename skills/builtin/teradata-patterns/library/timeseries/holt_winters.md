# Holt-Winters forecasting

TD_HOLT_WINTERS_FORECASTER calls for additive and multiplicative seasonality, fixed smoothing factors, level/trend/season layers, and model comparison.

**Use for:** forecasting a series with a trend and a stable cycle (weekly, yearly); deciding between additive and multiplicative seasonality; reading the level, trend and seasonal parts of a series; a quick seasonal baseline to compare ARIMA against.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the series table | `retail_demo` |
| `series_table` | Table the first statement builds: one row per series and day | `seasonal_daily` |
| `series_id` | Column that identifies each series | `series_id` |
| `seq_column` | Integer position of each point, 1..n, no gaps | `day_no` |
| `value_column` | Numeric column to forecast | `value_amt` |
| `forecast_periods` | Number of future points to forecast | `14` |
| `season_count` | SEASONAL_PERIODS: the number of whole cycles in each series (rows ÷ cycle length) | `52` |
| `model_type` | ADDITIVE or MULTIPLICATIVE | `ADDITIVE` |
| `alpha` | Level smoothing factor, strictly between 0 and 1 | `0.3` |
| `beta` | Trend smoothing factor, between 0 and 1 | `0.05` |
| `gamma` | Seasonal smoothing factor, above 0 | `0.3` |

## Statements

### seasonal_series — Build two synthetic daily series with a weekly cycle

The fixture has no seasonal series, so this builds 364 days (52 weeks) of two deterministic series: series 1 adds a fixed weekly swing to a trend (additive), series 2 multiplies the trend by a weekly factor (multiplicative). Both have noise from `(day_of_calendar * 7919) MOD 13`; `day_of_week` 1 is Sunday.

```sql
CREATE TABLE {{database}}.{{series_table}} AS (
  SELECT s.{{series_id}}, c.calendar_date AS obs_date,
         c.calendar_date - DATE '2024-01-01' + 1 AS {{seq_column}},
         CAST(CASE s.{{series_id}}
                WHEN 1 THEN 200 + 0.25 * (c.calendar_date - DATE '2024-01-01')
                            + CASE c.day_of_week WHEN 1 THEN -30 WHEN 6 THEN 25 WHEN 7 THEN 40 ELSE 0 END
                ELSE (100 + 1.0 * (c.calendar_date - DATE '2024-01-01'))
                     * CASE c.day_of_week WHEN 1 THEN 0.7 WHEN 6 THEN 1.2 WHEN 7 THEN 1.4 ELSE 1.0 END
              END + ((c.day_of_calendar * 7919) MOD 13) - 6 AS FLOAT) AS {{value_column}}
  FROM sys_calendar.calendar AS c
  CROSS JOIN (SELECT 1 AS {{series_id}} FROM (SELECT 1 AS x) AS a
              UNION ALL SELECT 2 FROM (SELECT 1 AS x) AS b) AS s
  WHERE c.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-29'
) WITH DATA PRIMARY INDEX ({{series_id}});
```

### additive_forecast — Fit and forecast with additive seasonality

SEASONAL_PERIODS is the number of whole cycles in each series, not the cycle length: for 364 daily rows with a weekly cycle it is 52 (364 ÷ 7). With SEASONAL_PERIODS(7) the same call treats the cycle as 52 days long and fits poorly (R_SQUARE 0.25 instead of 0.99). All series in one call get the same value, so give them equal length. The result holds every observed row and the forecast rows (OBSERVED_VALUE NULL, with 80% and 95% intervals); ROW_I counts from 0, and the first cycle's FORECAST_VALUE is 0 while the model initializes.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(hw_add)
TD_HOLT_WINTERS_FORECASTER(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), SERIES_ID({{series_id}}),
              ROW_AXIS(SEQUENCE({{seq_column}})), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), MODEL(ADDITIVE),
              SEASONAL_PERIODS({{season_count}}), PREDICTION_INTERVALS(BOTH),
              FIT_METRICS(1), SELECTION_METRICS(1), RESIDUALS(1)));

-- The forecast rows only
SELECT {{series_id}}, ROW_I, FORECAST_VALUE, LO_95, HI_95
FROM hw_add
WHERE OBSERVED_VALUE IS NULL
ORDER BY 1, 2;
```

### multiplicative_forecast — Seasonal swings proportional to the level

Use when the peaks grow with the level. ROW_AXIS can also be a date: TIMECODE(obs_date) gives the same result as SEQUENCE here.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(hw_mult)
TD_HOLT_WINTERS_FORECASTER(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), SERIES_ID({{series_id}}),
              ROW_AXIS(TIMECODE(obs_date)), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), MODEL(MULTIPLICATIVE),
              SEASONAL_PERIODS({{season_count}}), FIT_METRICS(1), SELECTION_METRICS(1)));

SELECT {{series_id}}, ROW_I, FORECAST_VALUE, LO_80, HI_80
FROM hw_mult
WHERE OBSERVED_VALUE IS NULL
ORDER BY 1, 2;
```

### model_comparison — Additive or multiplicative, by AIC

ARTSELMETRICS (from SELECTION_METRICS(1)) holds AIC, SBIC, HQIC and MSE per series; lower is better. On these series AIC picks additive for series 1 and multiplicative for series 2, as built.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(sel_add)
TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(hw_add), LAYER(ARTSELMETRICS)));
EXECUTE FUNCTION INTO VOLATILE ART(sel_mult)
TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(hw_mult), LAYER(ARTSELMETRICS)));

SELECT a.{{series_id}}, a.AIC AS aic_additive, m.AIC AS aic_multiplicative,
       a.MSE AS mse_additive, m.MSE AS mse_multiplicative,
       CASE WHEN a.AIC <= m.AIC THEN 'ADDITIVE' ELSE 'MULTIPLICATIVE' END AS better_model
FROM sel_add AS a
JOIN sel_mult AS m ON m.{{series_id}} = a.{{series_id}}
ORDER BY 1;
```

### decomposition — Level, trend and seasonal parts, and the fit

ARTFITRESIDUALS (from RESIDUALS(1)) has ACTUAL_VALUE, CALC_LEVEL, CALC_TREND, CALC_SEASONAL, CALC_VALUE and RESIDUAL per point. ARTFITMETADATA (from FIT_METRICS(1)) has R_SQUARE, the ALPHA, BETA and GAMMA used, MAE, MSE and MAPE. When ALPHA, BETA and GAMMA are omitted the function chooses them; ARTFITMETADATA shows the values (0.071, 0.0071, 0.446 here).

```sql
EXECUTE FUNCTION INTO VOLATILE ART(hw_parts)
TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(hw_add), LAYER(ARTFITRESIDUALS)));

-- One week of series 1: the seasonal part should mirror the weekly pattern
SELECT ROW_I, ACTUAL_VALUE, CALC_LEVEL, CALC_TREND, CALC_SEASONAL, CALC_VALUE, RESIDUAL
FROM hw_parts
WHERE {{series_id}} = 1 AND ROW_I BETWEEN 357 AND 363
ORDER BY ROW_I;

EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(hw_add), LAYER(ARTFITMETADATA)));
```

### custom_smoothing — Fixed smoothing factors, and a trend-only model

ALPHA must lie strictly between 0 and 1 and GAMMA must be above 0. Higher values follow recent points more closely. Without SEASONAL_PERIODS the function fits level and trend only (Holt's method); GAMMA then is refused.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(hw_fixed)
TD_HOLT_WINTERS_FORECASTER(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), SERIES_ID({{series_id}}),
              ROW_AXIS(SEQUENCE({{seq_column}})), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), MODEL({{model_type}}),
              SEASONAL_PERIODS({{season_count}}),
              ALPHA({{alpha}}), BETA({{beta}}), GAMMA({{gamma}}), FIT_METRICS(1)));

EXECUTE FUNCTION TD_EXTRACT_RESULTS(ART_SPEC(TABLE_NAME(hw_fixed), LAYER(ARTFITMETADATA)));

-- Level and trend only
EXECUTE FUNCTION TD_HOLT_WINTERS_FORECASTER(
  SERIES_SPEC(TABLE_NAME({{database}}.{{series_table}}), SERIES_ID({{series_id}}),
              ROW_AXIS(SEQUENCE({{seq_column}})), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS({{forecast_periods}}), MODEL(ADDITIVE), ALPHA({{alpha}}), BETA({{beta}})));
```

### holdout_backtest — Forecast a held-back period and measure the error

Fit on the first 308 days (44 weeks, so SEASONAL_PERIODS 44), forecast the last 56 and compare with what happened. Run it once per MODEL: additive had the lower MAE on series 1 (4.5 against 5.0), multiplicative on series 2 (4.8 against 5.2). FIT_PERCENTAGE is accepted by the function, but its holdout rows carried no seasonal pattern on this series, so hold the data back yourself.

```sql
CREATE VOLATILE TABLE hw_train AS (
  SELECT * FROM {{database}}.{{series_table}} WHERE {{seq_column}} <= 308
) WITH DATA ON COMMIT PRESERVE ROWS;

EXECUTE FUNCTION INTO VOLATILE ART(hw_bt)
TD_HOLT_WINTERS_FORECASTER(
  SERIES_SPEC(TABLE_NAME(hw_train), SERIES_ID({{series_id}}),
              ROW_AXIS(SEQUENCE({{seq_column}})), PAYLOAD(FIELDS({{value_column}}), CONTENT(REAL))),
  FUNC_PARAMS(FORECAST_PERIODS(56), MODEL({{model_type}}), SEASONAL_PERIODS(44)));

-- Forecast ROW_I is 0-based, the sequence starts at 1
SELECT f.{{series_id}}, COUNT(*) AS n_points,
       AVG(ABS(a.{{value_column}} - f.FORECAST_VALUE)) AS mae,
       AVG(ABS(a.{{value_column}} - f.FORECAST_VALUE) / a.{{value_column}}) * 100 AS mape
FROM hw_bt AS f
JOIN {{database}}.{{series_table}} AS a
  ON a.{{series_id}} = f.{{series_id}} AND a.{{seq_column}} = f.ROW_I + 1
WHERE f.ROW_I >= 308
GROUP BY 1
ORDER BY 1;
```

## Common errors

- **3706 "The ALPHA parameter must be greater than 0 and less than 1"** — ALPHA of 0, 1 or more. GAMMA(0) with a seasonal model is refused as well (3706); BETA(1.0) was accepted.
- **3706 "The PAYLOAD must be REAL"** — CONTENT(MULTIVAR_REAL) or several fields. Forecast one field per call.
- **3706 "The SEASONAL_PERIODS parameter must be specified if using GAMMA"** (or "must be greater than 1" with SEASONAL_PERIODS(1)) — GAMMA without a seasonal model. Set SEASONAL_PERIODS or drop GAMMA.
- **3706 "ART table does not contain data in ARTFITRESIDUALS layer"** — the call lacked RESIDUALS(1). ARTFITMETADATA needs FIT_METRICS(1), ARTSELMETRICS needs SELECTION_METRICS(1).
- **Poor fit, forecast out of phase** — SEASONAL_PERIODS set to the cycle length (7) instead of the number of cycles (52). R_SQUARE in ARTFITMETADATA shows it.
- **No rows returned, no error** — SEASONAL_PERIODS far too large for the series (200 on 364 rows returned nothing).

## Practices

- Give the function a gap-free series with whole cycles; compute SEASONAL_PERIODS as rows per series divided by the cycle length.
- Check R_SQUARE in ARTFITMETADATA after every fit; a cycle the model missed shows as a low value.
- Fit both models and keep the one with the lower AIC, then confirm it on a held-back period.
- Use MULTIPLICATIVE for positive series whose swings grow with the level; the function does not refuse zeros or negative values, so check the data yourself.
- Start without ALPHA, BETA and GAMMA; set them only when a backtest shows an improvement. On series 1 the function's own values gave a lower MSE (14.6) than 0.3/0.05/0.3 (17.5) and 0.1/0.01/0.1 (18.0).
- Keep intermediate results in INTO VOLATILE ART tables and read layers with TD_EXTRACT_RESULTS.
