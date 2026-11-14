# Outlier detection

Flag unusual numeric values by z-score, IQR (PERCENTILE_CONT), median absolute deviation, percentile bands or a two-column distance, overall or per group.

**Use for:** finding implausible values before analysis or model training; unusual amounts in transactions; comparing how many rows each method flags; writing flagged rows to a log table.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table with the numeric column | `customers` |
| `id_column` | Column that identifies a row | `customer_id` |
| `column` | Numeric column to check | `income` |
| `second_column` | Second numeric column for the two-column check | `age` |
| `group_column` | Column that splits the rows into groups | `segment` |
| `z_threshold` | Standard deviations from the mean beyond which a row is an outlier | `3` |
| `iqr_multiplier` | Multiple of the interquartile range added to the quartiles (1.5 usual, 3 for extreme values only) | `1.5` |
| `mad_threshold` | Modified z-score beyond which a row is an outlier | `3.5` |
| `log_table` | Name for the table that records flagged rows | `outlier_log` |

## Statements

### zscore_method — Distance from the mean in standard deviations

The mean and standard deviation are themselves moved by extreme values, so a few large outliers can hide milder ones. NULLIF avoids Error 2618 (division by zero) on a constant column.

```sql
WITH stats AS (
  SELECT AVG({{column}}) AS mean_value, STDDEV_POP({{column}}) AS stddev_value
  FROM {{database}}.{{table}}
)
SELECT
  t.{{id_column}},
  t.{{column}},
  (t.{{column}} - s.mean_value) / NULLIF(s.stddev_value, 0) AS z_score,
  CASE
    WHEN z_score > {{z_threshold}} THEN 'High outlier'
    WHEN z_score < -{{z_threshold}} THEN 'Low outlier'
    WHEN ABS(z_score) > {{z_threshold}} * 0.67 THEN 'Potential outlier'
    ELSE 'Normal'
  END AS outlier_flag
FROM {{database}}.{{table}} AS t
CROSS JOIN stats AS s
WHERE t.{{column}} IS NOT NULL
ORDER BY ABS(z_score) DESC;
```

### zscore_by_group — Outliers relative to their own group

Window aggregates give each row the mean and standard deviation of its group, and QUALIFY keeps the outliers. A value that is ordinary overall can be unusual within its group, and the reverse.

```sql
SELECT
  t.{{id_column}},
  t.{{group_column}},
  t.{{column}},
  (t.{{column}} - AVG(t.{{column}}) OVER (PARTITION BY t.{{group_column}}))
    / NULLIF(STDDEV_POP(t.{{column}}) OVER (PARTITION BY t.{{group_column}}), 0) AS z_score
FROM {{database}}.{{table}} AS t
WHERE t.{{column}} IS NOT NULL
QUALIFY ABS(z_score) > {{z_threshold}}
ORDER BY t.{{group_column}}, z_score DESC;
```

### iqr_method — Outside the quartiles by a multiple of the interquartile range

The quartiles do not move with a few extreme values. `PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY x)` is an aggregate here; the form with `OVER ()` is refused (Error 3707). NULLs are ignored.

```sql
WITH quartiles AS (
  SELECT
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {{column}}) AS q1,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {{column}}) AS q3
  FROM {{database}}.{{table}}
),
bounds AS (
  SELECT
    q3 - q1 AS iqr,
    q1 - {{iqr_multiplier}} * (q3 - q1) AS lower_bound,
    q3 + {{iqr_multiplier}} * (q3 - q1) AS upper_bound
  FROM quartiles
)
SELECT
  t.{{id_column}},
  t.{{column}},
  b.lower_bound,
  b.upper_bound,
  CASE WHEN t.{{column}} < b.lower_bound THEN 'Low outlier' ELSE 'High outlier' END AS outlier_flag,
  CASE
    WHEN t.{{column}} < b.lower_bound THEN b.lower_bound - t.{{column}}
    ELSE t.{{column}} - b.upper_bound
  END AS deviation_amount
FROM {{database}}.{{table}} AS t
CROSS JOIN bounds AS b
WHERE t.{{column}} < b.lower_bound OR t.{{column}} > b.upper_bound
ORDER BY deviation_amount DESC;
```

### mad_method — Modified z-score from the median absolute deviation

The modified z-score is 0.6745 × (value − median) / MAD, where MAD is the median of the absolute deviations from the median. Both medians ignore extreme values. The filter is a WHERE on the expression: QUALIFY is refused in a query without an ordered analytic function (Error 3706).

```sql
WITH med AS (
  SELECT MEDIAN({{column}}) AS median_value
  FROM {{database}}.{{table}}
),
dev AS (
  SELECT t.{{id_column}}, t.{{column}}, m.median_value,
         ABS(t.{{column}} - m.median_value) AS abs_deviation
  FROM {{database}}.{{table}} AS t
  CROSS JOIN med AS m
  WHERE t.{{column}} IS NOT NULL
),
mad AS (
  SELECT MEDIAN(abs_deviation) AS mad_value
  FROM dev
)
SELECT
  d.{{id_column}},
  d.{{column}},
  d.median_value,
  mad.mad_value,
  0.6745 * (d.{{column}} - d.median_value) / NULLIF(mad.mad_value, 0) AS modified_z_score
FROM dev AS d
CROSS JOIN mad
WHERE ABS(0.6745 * (d.{{column}} - d.median_value) / NULLIF(mad.mad_value, 0)) > {{mad_threshold}}
ORDER BY ABS(modified_z_score) DESC;
```

### percentile_method — Bottom and top 1% and 5% bands

This always flags a fixed share of the rows, whether or not they are unusual. The comparisons include the percentile itself, so values tied at the maximum land in the top band. There is no NTILE (Error 3706); `PERCENT_RANK()` gives each row its position between 0 and 1.

```sql
WITH pct AS (
  SELECT
    PERCENTILE_CONT(0.01) WITHIN GROUP (ORDER BY {{column}}) AS p01,
    PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY {{column}}) AS p05,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY {{column}}) AS p95,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY {{column}}) AS p99
  FROM {{database}}.{{table}}
)
SELECT
  t.{{id_column}},
  t.{{column}},
  CASE
    WHEN t.{{column}} <= p.p01 THEN 'Bottom 1%'
    WHEN t.{{column}} <= p.p05 THEN 'Bottom 5%'
    WHEN t.{{column}} >= p.p99 THEN 'Top 1%'
    WHEN t.{{column}} >= p.p95 THEN 'Top 5%'
  END AS percentile_band,
  PERCENT_RANK() OVER (ORDER BY t.{{column}}) AS pct_rank
FROM {{database}}.{{table}} AS t
CROSS JOIN pct AS p
WHERE t.{{column}} IS NOT NULL
QUALIFY percentile_band IS NOT NULL
ORDER BY t.{{column}} DESC;
```

### multivariate_outliers — Unusual in two columns taken together

Each column is standardised with window aggregates and the row's distance from the centre is the Euclidean length of the two z-scores. This treats the columns as unrelated; it is not a Mahalanobis distance, which would also use their correlation.

```sql
WITH standardized AS (
  SELECT
    {{id_column}},
    {{column}},
    {{second_column}},
    ({{column}} - AVG({{column}}) OVER ()) / NULLIF(STDDEV_POP({{column}}) OVER (), 0) AS z1,
    ({{second_column}} - AVG({{second_column}}) OVER ()) / NULLIF(STDDEV_POP({{second_column}}) OVER (), 0) AS z2
  FROM {{database}}.{{table}}
  WHERE {{column}} IS NOT NULL AND {{second_column}} IS NOT NULL
)
SELECT
  {{id_column}},
  {{column}},
  {{second_column}},
  z1,
  z2,
  SQRT(z1 * z1 + z2 * z2) AS distance
FROM standardized
WHERE SQRT(z1 * z1 + z2 * z2) > {{z_threshold}}
ORDER BY distance DESC;
```

### outlier_summary — How many rows the IQR rule flags, and their range

Run this before acting on the outliers: a rule that flags more than a few percent of the rows is describing the shape of the data, not errors in it.

```sql
WITH quartiles AS (
  SELECT
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {{column}}) AS q1,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {{column}}) AS q3
  FROM {{database}}.{{table}}
)
SELECT
  CASE
    WHEN t.{{column}} < q.q1 - {{iqr_multiplier}} * (q.q3 - q.q1) THEN 'Low outlier'
    WHEN t.{{column}} > q.q3 + {{iqr_multiplier}} * (q.q3 - q.q1) THEN 'High outlier'
    ELSE 'Normal'
  END AS category,
  COUNT(*) AS row_count,
  CAST(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS DECIMAL(5,2)) AS percentage,
  MIN(t.{{column}}) AS min_value,
  MAX(t.{{column}}) AS max_value,
  AVG(t.{{column}}) AS avg_value
FROM {{database}}.{{table}} AS t
CROSS JOIN quartiles AS q
WHERE t.{{column}} IS NOT NULL
GROUP BY 1
ORDER BY row_count DESC;
```

### outlier_log — Record the flagged rows in a log table

In an INSERT ... SELECT the WITH clause goes after `INSERT INTO table`; a WITH before INSERT is refused (Error 3707). The NOT EXISTS keeps a second run on the same day from logging a row twice.

```sql
CREATE MULTISET TABLE {{database}}.{{log_table}} (
  detection_date DATE NOT NULL,
  source_table   VARCHAR(128) NOT NULL,
  column_name    VARCHAR(128) NOT NULL,
  row_id         VARCHAR(64) NOT NULL,
  observed_value FLOAT,
  lower_bound    FLOAT,
  upper_bound    FLOAT
) PRIMARY INDEX (row_id);

INSERT INTO {{database}}.{{log_table}}
WITH quartiles AS (
  SELECT
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {{column}}) AS q1,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {{column}}) AS q3
  FROM {{database}}.{{table}}
)
SELECT
  CURRENT_DATE,
  '{{table}}',
  '{{column}}',
  TRIM(CAST(t.{{id_column}} AS VARCHAR(64))),
  t.{{column}},
  q.q1 - {{iqr_multiplier}} * (q.q3 - q.q1),
  q.q3 + {{iqr_multiplier}} * (q.q3 - q.q1)
FROM {{database}}.{{table}} AS t
CROSS JOIN quartiles AS q
WHERE (t.{{column}} < q.q1 - {{iqr_multiplier}} * (q.q3 - q.q1)
    OR t.{{column}} > q.q3 + {{iqr_multiplier}} * (q.q3 - q.q1))
  AND NOT EXISTS (
    SELECT 1 FROM {{database}}.{{log_table}} AS l
    WHERE l.detection_date = CURRENT_DATE
      AND l.source_table = '{{table}}'
      AND l.column_name = '{{column}}'
      AND l.row_id = TRIM(CAST(t.{{id_column}} AS VARCHAR(64)))
  );

SELECT detection_date, row_id, observed_value, lower_bound, upper_bound
FROM {{database}}.{{log_table}}
WHERE detection_date = CURRENT_DATE
ORDER BY observed_value DESC;
```

## Common errors

- **3706 "Order-based Aggregate and Ordered Analytical Functions are not allowed in subqueries"** — PERCENTILE_CONT or MEDIAN in a scalar subquery, for example `WHERE x > (SELECT PERCENTILE_CONT(0.75) ...)`. Compute the value in a WITH clause or derived table and CROSS JOIN it.
- **3707 on `PERCENTILE_CONT(...) WITHIN GROUP (...) OVER ()`** — there is no windowed form. Use the aggregate, with GROUP BY for per-group quartiles.
- **3706 on `NTILE(100) OVER (...)`** — not available. Use PERCENT_RANK() or CUME_DIST().
- **5480 "Ordered Analytical Functions can not be nested"** — `SELECT TOP n ... ORDER BY` or `QUALIFY ROW_NUMBER() OVER (ORDER BY ...)` on a column that is itself built from window functions. Compute the column in a derived table and apply TOP outside it.
- **3706 "Qualify Clause cannot be used in absence of Ordered Analytic functions"** — the query has no window function. Filter with WHERE.
- **2618 "Invalid calculation: division by zero"** — the standard deviation or MAD of a constant column is 0. Wrap the divisor in NULLIF(..., 0).
- **3707 on `AS value`, `AS count` or `AS percent_rank`** — reserved words. Use another alias or double-quote it.

## Practices

- Compare the methods on the same column before choosing one. On a column with a few very large values the z-score flags only those; the IQR bounds do not move with them and also catch milder values.
- Usual thresholds: 3 for the z-score, 1.5 × IQR (3 × IQR for extreme values only), 3.5 for the modified z-score.
- Check how many rows a rule flags (outlier_summary) and look at the flagged rows before acting on them.
- Detect within comparable groups (segment, product category, period) when the groups have different typical values.
- Do not delete outliers automatically. Decide per case whether the value is an error, a case to investigate, or a real extreme, and record the decision.
- Combine the statistical rule with business rules: values that cannot occur (negative amounts) are errors regardless of their distance from the mean.
- NULLs are ignored by AVG, STDDEV_POP, MEDIAN and PERCENTILE_CONT; filter them from the detail rows so they are not reported with a NULL score.
