# Statistical tests

TD_ChiSq for independence, TD_FTest for equal variances, TD_ZTest for means — input shapes and required parameters.

**Use for:** testing whether an outcome depends on a category (status by channel); checking equal variances before ANOVA or a two-sample test; testing a mean against a target; comparing the means of two groups.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table with one row per observation | `orders` |
| `group_column` | Categorical column that names the samples or the rows of the cross-tabulation | `channel` |
| `outcome_column` | Second categorical column (columns of the cross-tabulation) | `status` |
| `value_column` | Numeric measure | `amount` |
| `first_sample` | Value of `group_column` for the first sample | `web` |
| `second_sample` | Value of `group_column` for the second sample | `mobile` |
| `target_mean` | Mean under the null hypothesis for a one-sample test | `800` |
| `known_variance` | Population variance for a one-sample test on fewer than 30 rows | `334000` |
| `alpha` | Significance level | `0.05` |

## Statements

### chisq_independence — Does the outcome depend on the group?

TD_ChiSq takes a contingency table: the first column labels the rows, the other columns are counts. Build it with SUM(CASE ...) from raw rows. The input alias may be `CONTINGENCY` or `InputTable`. Output: chi_square, Cramér's V (0 = no association, 1 = perfect), df = (rows − 1)(columns − 1), p_value. Expected counts below about 5 per cell make the test unreliable; merge sparse categories.

```sql
SELECT chi_square, cramers_v, df, p_value, criticalvalue,
  TRIM(TRAILING '00'XC FROM conclusion) AS conclusion
FROM TD_ChiSq(
  ON (SELECT {{group_column}},
        SUM(CASE WHEN {{outcome_column}} = 'completed' THEN 1 ELSE 0 END) AS completed,
        SUM(CASE WHEN {{outcome_column}} = 'shipped' THEN 1 ELSE 0 END) AS shipped,
        SUM(CASE WHEN {{outcome_column}} = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
        SUM(CASE WHEN {{outcome_column}} = 'returned' THEN 1 ELSE 0 END) AS returned
      FROM {{database}}.{{table}}
      GROUP BY {{group_column}}) AS CONTINGENCY
  USING Alpha({{alpha}})
) AS t;
```

### chisq_expected_counts — The counts expected if the variables were independent

`OUT TABLE EXPCOUNTS(name)` writes the expected count of every cell to a permanent table, which must not exist yet. Compare them with the observed counts to see which cells drive the statistic, then drop the table.

```sql
SELECT chi_square, p_value
FROM TD_ChiSq(
  ON (SELECT {{group_column}},
        SUM(CASE WHEN {{outcome_column}} = 'completed' THEN 1 ELSE 0 END) AS completed,
        SUM(CASE WHEN {{outcome_column}} <> 'completed' THEN 1 ELSE 0 END) AS not_completed
      FROM {{database}}.{{table}}
      GROUP BY {{group_column}}) AS CONTINGENCY
  OUT TABLE EXPCOUNTS({{database}}.chisq_expected)
  USING Alpha({{alpha}})
) AS t;

SELECT * FROM {{database}}.chisq_expected ORDER BY 1;

DROP TABLE {{database}}.chisq_expected;
```

### ftest_two_samples — Are two variances equal?

In long format, name the samples: `FirstSampleName` and `SecondSampleName` are required with `SampleNameColumn` (Error 7810 without them). As with every test here, pass only the name and value columns. `varianceratio` is the first variance over the second.

```sql
SELECT firstsamplevariance, secondsamplevariance, varianceratio, df1, df2, p_value
FROM TD_FTest(
  ON (SELECT {{group_column}}, {{value_column}} FROM {{database}}.{{table}}) AS InputTable
  USING
    SampleNameColumn('{{group_column}}')
    SampleValueColumn('{{value_column}}')
    FirstSampleName('{{first_sample}}')
    SecondSampleName('{{second_sample}}')
    AlternativeHypothesis('two-tailed')
    Alpha({{alpha}})
) AS t;
```

### ftest_from_variances — The F-test from variances already computed

No ON clause: pass the two sample variances and their degrees of freedom (n − 1).

```sql
SELECT varianceratio, p_value, CriticalValue
FROM TD_FTest(
  USING
    FirstSampleVariance(354135.92)
    SecondSampleVariance(317636.07)
    df1(339)
    df2(334)
    AlternativeHypothesis('two-tailed')
    Alpha({{alpha}})
) AS t;
```

### ztest_two_samples — Do two group means differ?

The z-test is for large samples. Same long-format rules as the F-test. `two-tailed` tests for any difference, `upper-tailed` for first > second, `lower-tailed` for first < second.

```sql
SELECT N1, N2, mean1, mean2, z_score, p_value
FROM TD_ZTest(
  ON (SELECT {{group_column}}, {{value_column}} FROM {{database}}.{{table}}) AS InputTable
  USING
    SampleNameColumn('{{group_column}}')
    SampleValueColumn('{{value_column}}')
    FirstSampleName('{{first_sample}}')
    SecondSampleName('{{second_sample}}')
    AlternativeHypothesis('two-tailed')
    Alpha({{alpha}})
) AS t;
```

### ztest_one_sample — Is the mean above a target?

On 30 rows or more the variance is estimated from the data; z = (mean − target) / (stddev / √n), the same value the SQL beside it computes.

```sql
SELECT N1, mean1, z_score, p_value
FROM TD_ZTest(
  ON (SELECT {{value_column}} FROM {{database}}.{{table}})
  USING
    FirstSampleColumn('{{value_column}}')
    MeanUnderH0({{target_mean}})
    AlternativeHypothesis('upper-tailed')
    Alpha({{alpha}})
) AS t;

SELECT COUNT(*) AS n,
  (AVG(CAST({{value_column}} AS FLOAT)) - {{target_mean}})
    / (STDDEV_SAMP(CAST({{value_column}} AS FLOAT)) / SQRT(COUNT(*))) AS z_by_hand
FROM {{database}}.{{table}};
```

### ztest_small_sample — Fewer than 30 rows need the population variance

Without `FirstSampleVariance` the call fails with Error 9134 (Sample size < 30).

```sql
SELECT N1, mean1, z_score, p_value
FROM TD_ZTest(
  ON (SELECT {{value_column}} FROM {{database}}.{{table}} WHERE order_id <= 20)
  USING
    FirstSampleColumn('{{value_column}}')
    FirstSampleVariance({{known_variance}})
    MeanUnderH0({{target_mean}})
    AlternativeHypothesis('upper-tailed')
    Alpha({{alpha}})
) AS t;
```

## Common errors

- **Error 7810 Values column must be numeric type** — TD_FTest, TD_ZTest and TD_ANOVA reject an input with any non-numeric column besides the sample-name column, whatever the value column's type. Fix: `ON (SELECT name_col, value_col FROM ...)`.
- **Error 7810 FirstSampleName or SecondSampleName must be provided** — Long-format F-test or z-test without the sample names. Fix: name both samples.
- **Error 9134 Sample size < 30. Please provide population variance.** — One-sample z-test on a small sample. Fix: pass `FirstSampleVariance`, or use a t-based method.
- **Error 3803 Table 'chisq_expected' already exists** — `OUT TABLE EXPCOUNTS` will not overwrite a table. Fix: drop it after use, or choose a new name.
- **`conclusion` text never matches** — It ends in a NUL character. Fix: `TRIM(TRAILING '00'XC FROM conclusion)` or compare `p_value` with alpha.

## Practices

- Pick the test and the tail before looking at the data.
- A large sample makes tiny differences significant: report the size of the difference (the means, Cramér's V) next to the p-value.
- Run the F-test before comparing means of groups whose spread may differ.
