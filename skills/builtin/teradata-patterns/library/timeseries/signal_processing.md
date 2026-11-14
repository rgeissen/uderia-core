# Signal processing with UAF functions

TD_DFFT, TD_IDFFT, TD_DFFTCONV, TD_WINDOWDFFT, TD_POWERSPEC, TD_LINESPEC, TD_CONVOLVE, TD_DTW, TD_DWT, TD_IDWT and TD_BINARYSERIESOP on a regular series.

**Use for:** finding the cycles in a series; removing known cycles before modelling; smoothing with a kernel; cycles that change over time; comparing shapes shifted in time; splitting a series into smooth and detail parts.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the orders table and the series tables | `retail_demo` |
| `signal_table` | Synthetic signal the statements create: `sensor_id`, `sample_no`, `amplitude` | `sensor_signal` |
| `revenue_table` | Business series the statements create: `channel`, `day_no`, `revenue` | `daily_revenue` |
| `wavelet` | Wavelet name for TD_DWT and TD_IDWT, lower case | `db2` |

## Statements

### build_series — The two series the other statements read

Synthetic signal, 256 samples per sensor: period 8 (amplitude 10) plus period 32 (amplitude 3 for sensor 1, 6 for sensor 2) plus small deterministic noise; sensor 3 switches from period 8 to period 16 after sample 128. Business series: daily revenue per channel for the first 256 days of 2024, days without orders filled with 0 from `sys_calendar.calendar`. The row axis is an integer position, the payload FLOAT.

```sql
CREATE TABLE {{database}}.{{signal_table}} AS (
  SELECT s.sensor_id, c.day_of_calendar AS sample_no,
    CAST(CASE WHEN s.sensor_id = 3 AND c.day_of_calendar > 128
              THEN 10 * SIN(2 * 3.14159265358979 * c.day_of_calendar / 16)
              ELSE 10 * SIN(2 * 3.14159265358979 * c.day_of_calendar / 8)
                   + CASE s.sensor_id WHEN 1 THEN 3 WHEN 2 THEN 6 ELSE 0 END
                     * COS(2 * 3.14159265358979 * c.day_of_calendar / 32) END
         + ((c.day_of_calendar * 7919) MOD 13) / 13.0 AS FLOAT) AS amplitude
  FROM sys_calendar.calendar AS c
  CROSS JOIN (SELECT day_of_calendar AS sensor_id FROM sys_calendar.calendar
              WHERE day_of_calendar BETWEEN 1 AND 3) AS s
  WHERE c.day_of_calendar BETWEEN 1 AND 256
) WITH DATA PRIMARY INDEX (sensor_id);

CREATE TABLE {{database}}.{{revenue_table}} AS (
  SELECT ch.channel, c.calendar_date - DATE '2024-01-01' + 1 AS day_no,
    CAST(COALESCE(SUM(o.amount), 0) AS FLOAT) AS revenue
  FROM sys_calendar.calendar AS c
  CROSS JOIN (SELECT DISTINCT channel FROM {{database}}.orders) AS ch
  LEFT JOIN {{database}}.orders AS o
    ON o.order_date = c.calendar_date AND o.channel = ch.channel
  WHERE c.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2024-01-01' + 255
  GROUP BY 1, 2
) WITH DATA PRIMARY INDEX (channel);
```

### fft — Spectrum with TD_DFFT, back to the series with TD_IDFFT, layouts with TD_DFFTCONV

Centred layout (`HUMAN_READABLE(1)`, the default): `ROW_I` runs -N/2 to N/2-1 and period P sits at k = ±N/P; raw layout runs 0 to N-1. Output is COMPLEX (`REAL_<field>`, `IMAG_<field>`); `OUTPUT_FMT(CONTENT(AMPL_PHASE_RADIANS))` gives `AMPLITUDE_`/`PHASE_` columns. Magnitudes are N·A/2 for a sine of amplitude A: here 1280 at k=32 (period 8) and, at k=8 (period 32), 384 for sensor 1 and 768 for sensor 2. TD_IDFFT reads the TD_DFFT ART (`REAL_REAL_<field>`, `ROW_I` from 0); its HUMAN_READABLE must match the spectrum's layout or the result is silently wrong. TD_DFFTCONV switches layouts (`CONV(HR_TO_RAW)` or `RAW_TO_HR`); neither TD_IDFFT nor TD_DFFTCONV accepts its result as input.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(spectrum)
TD_DFFT(
  SERIES_SPEC(TABLE_NAME({{database}}.{{signal_table}}), SERIES_ID(sensor_id),
    ROW_AXIS(SEQUENCE(sample_no)), PAYLOAD(FIELDS(amplitude), CONTENT(REAL))),
  FUNC_PARAMS(FREQ_STYLE(K_INTEGRAL), HUMAN_READABLE(1)),
  OUTPUT_FMT(CONTENT(COMPLEX))
);

SELECT sensor_id, ROW_I AS k, SQRT(REAL_amplitude ** 2 + IMAG_amplitude ** 2) AS magnitude
FROM spectrum
WHERE ROW_I > 0
QUALIFY ROW_NUMBER() OVER (PARTITION BY sensor_id ORDER BY magnitude DESC) <= 2
ORDER BY sensor_id, magnitude DESC;

EXECUTE FUNCTION INTO VOLATILE ART(signal_back)
TD_IDFFT(ART_SPEC(TABLE_NAME(spectrum)));

-- Largest reconstruction error: about 1e-13
SELECT MAX(ABS(s.amplitude - r.REAL_REAL_amplitude)) AS max_error
FROM {{database}}.{{signal_table}} AS s
JOIN signal_back AS r ON r.sensor_id = s.sensor_id AND r.ROW_I = s.sample_no - 1;

EXECUTE FUNCTION INTO VOLATILE ART(spectrum_raw)
TD_DFFTCONV(ART_SPEC(TABLE_NAME(spectrum)),
  FUNC_PARAMS(CONV(HR_TO_RAW), FREQ_STYLE(K_INTEGRAL)), OUTPUT_FMT(CONTENT(COMPLEX)));

-- Raw layout: ROW_I 0 to N-1, the zero frequency first
SELECT TOP 3 * FROM spectrum_raw ORDER BY sensor_id, ROW_I;
```

### windowed_fft — How the frequency content changes over time with TD_WINDOWDFFT

One FFT per window: `COLUMN_I` is the window, `ROW_I` the frequency index in it. In 64-sample windows period 8 sits at k=8 and period 16 at k=4; on sensor 3 the dominant k moves from 8 to 4 halfway, which one FFT cannot show. `SERIES_SPEC(...) WHERE ...` selects series.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(stft)
TD_WINDOWDFFT(
  SERIES_SPEC(TABLE_NAME({{database}}.{{signal_table}}), SERIES_ID(sensor_id),
    ROW_AXIS(SEQUENCE(sample_no)), PAYLOAD(FIELDS(amplitude), CONTENT(REAL)))
    WHERE sensor_id = 3,
  FUNC_PARAMS(
    WINDOW(SIZE(NUM(64)), OVERLAP(32), TYPE(HANN), SCALE(SPECTRUM)),
    DFFT(FREQ_STYLE(K_INTEGRAL), HUMAN_READABLE(1))
  ),
  OUTPUT_FMT(CONTENT(AMPL_PHASE_RADIANS))
);

SELECT sensor_id, COLUMN_I AS window_no, ROW_I AS k, AMPLITUDE_amplitude
FROM stft
WHERE ROW_I > 0
QUALIFY ROW_NUMBER() OVER (PARTITION BY sensor_id, COLUMN_I ORDER BY AMPLITUDE_amplitude DESC) = 1
ORDER BY window_no;
```

### spectra — Dominant periods with TD_POWERSPEC and TD_LINESPEC

With `FREQ_STYLE(K_PERIODICITY)` `ROW_I` is the period, so the top rows name the cycles (8 and 32; 16 and 8 for sensor 3). TD_LINESPEC `INCLUDE_COEFF(1)` adds `DFFT_COEFF_REAL_`/`DFFT_COEFF_IMAG_` columns. `ALGORITHM(AUTOCORR), WINDOW(WINDOW_NAME(BARTLETT))` also runs; it smears the peaks and here loses period 32.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(power_spec)
TD_POWERSPEC(
  SERIES_SPEC(TABLE_NAME({{database}}.{{signal_table}}), SERIES_ID(sensor_id),
    ROW_AXIS(SEQUENCE(sample_no)), PAYLOAD(FIELDS(amplitude), CONTENT(REAL))),
  FUNC_PARAMS(FREQ_STYLE(K_PERIODICITY))
);

SELECT sensor_id, ROW_I AS period, SPECTRAL_DENSITY_amplitude
FROM power_spec
QUALIFY ROW_NUMBER() OVER (PARTITION BY sensor_id ORDER BY SPECTRAL_DENSITY_amplitude DESC) <= 2
ORDER BY sensor_id, SPECTRAL_DENSITY_amplitude DESC;

EXECUTE FUNCTION INTO VOLATILE ART(line_spec)
TD_LINESPEC(
  SERIES_SPEC(TABLE_NAME({{database}}.{{signal_table}}), SERIES_ID(sensor_id),
    ROW_AXIS(SEQUENCE(sample_no)), PAYLOAD(FIELDS(amplitude), CONTENT(REAL))),
  FUNC_PARAMS(FREQ_STYLE(K_PERIODICITY), INCLUDE_COEFF(1))
);

SELECT TOP 4 * FROM line_spec ORDER BY SPECTRAL_DENSITY_amplitude DESC;
```

### periodicity_removal — Fit known cycles and subtract them with TD_BINARYSERIESOP

TD_GENSERIES4SINUSOIDS fits sinusoids of the given periods (`MAGNITUDE_<field>`); TD_BINARYSERIESOP subtracts them point by point (`OUT_<field>`, `ROW_I` from 0). `MATHOP`: ADD, SUB, MUL, DIV. `INPUT_MODE`: MATCH pairs by SERIES_ID, ONE2ONE pairs two series each chosen by `WHERE`, MANY2ONE applies one to many. The residual is the noise (standard deviation 0.28, from 7.4 and 8.2).

```sql
EXECUTE FUNCTION INTO VOLATILE ART(cycles)
TD_GENSERIES4SINUSOIDS(
  SERIES_SPEC(TABLE_NAME({{database}}.{{signal_table}}), SERIES_ID(sensor_id),
    ROW_AXIS(SEQUENCE(sample_no)), PAYLOAD(FIELDS(amplitude), CONTENT(REAL)))
    WHERE sensor_id IN (1, 2),
  FUNC_PARAMS(PERIODICITIES(8, 32)),
  OUTPUT_FMT(INDEX_STYLE(FLOW_THROUGH))
);

EXECUTE FUNCTION INTO VOLATILE ART(residual)
TD_BINARYSERIESOP(
  SERIES_SPEC(TABLE_NAME({{database}}.{{signal_table}}), SERIES_ID(sensor_id),
    ROW_AXIS(SEQUENCE(sample_no)), PAYLOAD(FIELDS(amplitude), CONTENT(REAL)))
    WHERE sensor_id IN (1, 2),
  ART_SPEC(TABLE_NAME(cycles)),
  FUNC_PARAMS(MATHOP(SUB)),
  INPUT_FMT(INPUT_MODE(MATCH))
);

SELECT sensor_id, STDDEV_POP(OUT_amplitude) AS residual_sd FROM residual GROUP BY 1 ORDER BY 1;
```

### convolution — Filter a series with a kernel using TD_CONVOLVE

Series first, kernel second; with `MANY2ONE` the kernel is one series chosen by `WHERE`. Eight taps of 1/8 are an 8-sample moving average, which removes the period-8 cycle. Output is the full convolution, COMPLEX: N+M-1 rows with `CONV_SUMMATION` (263), zero-padded with the FFT default (270). Row k equals the window AVG ending at sample k+1.

```sql
CREATE TABLE {{database}}.smoothing_kernel AS (
  SELECT 1 AS kernel_id, day_of_calendar AS tap, CAST(1 AS FLOAT) / 8 AS weight
  FROM sys_calendar.calendar WHERE day_of_calendar BETWEEN 1 AND 8
) WITH DATA PRIMARY INDEX (kernel_id);

EXECUTE FUNCTION INTO VOLATILE ART(smoothed)
TD_CONVOLVE(
  SERIES_SPEC(TABLE_NAME({{database}}.{{signal_table}}), SERIES_ID(sensor_id),
    ROW_AXIS(SEQUENCE(sample_no)), PAYLOAD(FIELDS(amplitude), CONTENT(REAL))),
  SERIES_SPEC(TABLE_NAME({{database}}.smoothing_kernel), SERIES_ID(kernel_id),
    ROW_AXIS(SEQUENCE(tap)), PAYLOAD(FIELDS(weight), CONTENT(REAL))) WHERE kernel_id = 1,
  FUNC_PARAMS(ALGORITHM(CONV_SUMMATION)),
  INPUT_FMT(INPUT_MODE(MANY2ONE))
);

SELECT m.sensor_id, m.sample_no, m.amplitude, m.ma8, c.REAL_amplitude AS convolved
FROM (
  SELECT s.sensor_id, s.sample_no, s.amplitude,
    AVG(s.amplitude) OVER (PARTITION BY s.sensor_id ORDER BY s.sample_no ROWS 7 PRECEDING) AS ma8
  FROM {{database}}.{{signal_table}} AS s
) AS m
JOIN smoothed AS c ON c.sensor_id = m.sensor_id AND c.ROW_I = m.sample_no - 1
WHERE m.sensor_id = 1 AND m.sample_no BETWEEN 8 AND 12
ORDER BY m.sample_no;
```

### dtw — Shape distance between series with TD_DTW

`WarpDistance` is the dynamic time warping distance, which tolerates shifts in time. Each channel's daily revenue against the web channel (`MANY2ONE`). `RADIUS` bounds the warp; `WARPPATH(1)` returns the aligned pairs (`WarpX`, `WarpY`) instead.

```sql
EXECUTE FUNCTION INTO VOLATILE ART(channel_dtw)
TD_DTW(
  SERIES_SPEC(TABLE_NAME({{database}}.{{revenue_table}}), SERIES_ID(channel),
    ROW_AXIS(SEQUENCE(day_no)), PAYLOAD(FIELDS(revenue), CONTENT(REAL))),
  SERIES_SPEC(TABLE_NAME({{database}}.{{revenue_table}}), SERIES_ID(channel),
    ROW_AXIS(SEQUENCE(day_no)), PAYLOAD(FIELDS(revenue), CONTENT(REAL))) WHERE channel = 'web',
  FUNC_PARAMS(RADIUS(7)),
  INPUT_FMT(INPUT_MODE(MANY2ONE))
);

SELECT channel, WarpDistance FROM channel_dtw ORDER BY WarpDistance;
```

### wavelets — Decompose and reconstruct with TD_DWT and TD_IDWT

TD_DWT returns the approximation `A_<field>` and detail `D_<field>` at `LEVEL` (default 1), about N/2^LEVEL rows per series. TD_IDWT returns `MAGNITUDE_1`: the exact series from a level-1 result, but only a shorter series from a deeper level (130 rows from a level-2 result of 256 days). Wavelet names are lower case (`haar`, `db2`, `db4`).

```sql
EXECUTE FUNCTION INTO VOLATILE ART(dwt_level1)
TD_DWT(
  SERIES_SPEC(TABLE_NAME({{database}}.{{revenue_table}}), SERIES_ID(channel),
    ROW_AXIS(SEQUENCE(day_no)), PAYLOAD(FIELDS(revenue), CONTENT(REAL))),
  FUNC_PARAMS(WAVELET('{{wavelet}}'), LEVEL(1))
);

EXECUTE FUNCTION INTO VOLATILE ART(revenue_back)
TD_IDWT(ART_SPEC(TABLE_NAME(dwt_level1)), FUNC_PARAMS(WAVELET('{{wavelet}}')));

SELECT d.channel, MAX(ABS(d.revenue - r.MAGNITUDE_1)) AS max_error, COUNT(*) AS days
FROM {{database}}.{{revenue_table}} AS d
JOIN revenue_back AS r ON r.channel = d.channel AND r.ROW_I = d.day_no - 1
GROUP BY 1 ORDER BY 1;
```

## Common errors

- **3707 on a table named `signal`** — SIGNAL is reserved; choose another name.
- **3706 "Invalid enum value for FREQ_STYLE"** — K_PERIODICITY in TD_DFFT; it belongs to TD_POWERSPEC and TD_LINESPEC.
- **3706 "K_HERTZ requires usage of HERTZ_SAMPLE_RATE"** — add `HERTZ_SAMPLE_RATE(rate)`.
- **3706 "Specified payload not found in layer column definitions"** — a payload column the ART lacks (it is `IMAG_<field>`, not `IMAGINARY_<field>`); with ART_SPEC leave the payload out.
- **9153 "Trying to open a handle on a layer which does not exist"** — TD_IDFFT given an ART through SERIES_SPEC, or a TD_DFFTCONV result. Give it the TD_DFFT ART through ART_SPEC.
- **9153 "No meta information available for art input without index layer"** — a TD_DFFTCONV result passed to TD_DFFTCONV; convert from a TD_DFFT ART.
- **9153 "Exceeded Singleton max prime"** — TD_IDFFT through SERIES_SPEC on a centred spectrum copied to a table; a raw spectrum with `HUMAN_READABLE(0)` works.
- **3706 "Missing INPUT_FMT/INPUT_MODE for multi-logical data types"** — two-input functions need `INPUT_FMT(INPUT_MODE(...))`.
- **3706 "Not all fields of the ID were specified in the WHERE clause for logical input #2"** — MANY2ONE or ONE2ONE without a WHERE selecting one series. Write `SERIES_SPEC(...) WHERE id = value`; a WHERE inside the parentheses is a 3706 syntax error.
- **3706 "Unknown wavelet"** — `'HAAR'`; names are lower case. Unquoted, the error is "Could not convert value for 'WAVELET'".
- **3706 "Func_Param LEVEL is not valid"** — TD_IDWT takes only WAVELET.
- **TD_IDFFT output wrong, no error** — its HUMAN_READABLE does not match the spectrum's layout.

## Practices

- Make the series regular first: one row per position, gaps filled from a calendar join.
- Keep each step in a VOLATILE ART and pass it on with ART_SPEC; it lives for the session.
- Check a transform by inverting it and comparing with the source.
- Read periods from TD_POWERSPEC or TD_LINESPEC before choosing a seasonal period or the PERIODICITIES to remove.
