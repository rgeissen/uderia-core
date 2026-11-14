# One-way ANOVA

TD_ANOVA: do the means of a measure differ across groups? Long and wide input, what the input may contain, and reading the result.

**Use for:** comparing an average across three or more groups (channels, segments, regions, test arms); checking whether observed differences in group means are larger than chance.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table with one row per observation | `orders` |
| `group_column` | Column naming each row's group | `channel` |
| `value_column` | Numeric measure to compare | `amount` |
| `alpha` | Significance level | `0.05` |

## Statements

### long_format — Compare the groups named in a group column

Pass ONLY the group column and the value column. TD_ANOVA rejects the input if any other column is not numeric, with the misleading message "Values column must be numeric type" (Error 7810) — `ON orders` fails, `ON (SELECT channel, amount FROM orders)` works. `NumGroups(n)` takes the first n groups found; `GroupNames('a','b')` names them; giving both is refused.

```sql
SELECT f_statistic, p_value, critical_f, alpha,
  "df(between groups)" AS df_between, "df(within groups)" AS df_within,
  TRIM(TRAILING '00'XC FROM conclusion) AS conclusion
FROM TD_ANOVA(
  ON (SELECT {{group_column}}, {{value_column}} FROM {{database}}.{{table}}) AS InputTable
  USING
    GroupNameColumn('{{group_column}}')
    GroupValueColumn('{{value_column}}')
    NumGroups(3)
    Alpha({{alpha}})
) AS t;
```

### named_groups — Compare selected groups only, on a filtered population

```sql
SELECT f_statistic, p_value, critical_f
FROM TD_ANOVA(
  ON (SELECT {{group_column}}, {{value_column}} FROM {{database}}.{{table}}
      WHERE status = 'completed') AS InputTable
  USING
    GroupNameColumn('{{group_column}}')
    GroupValueColumn('{{value_column}}')
    GroupNames('web', 'store', 'mobile')
    Alpha({{alpha}})
) AS t;
```

### wide_format — One column per group

`GroupColumns` names the columns; rows are observations, and a NULL is a missing observation, so the groups may have different sizes. A pivot by row number turns long data into this shape.

```sql
SELECT f_statistic, p_value, "df(within groups)" AS df_within
FROM TD_ANOVA(
  ON (SELECT
        MAX(CASE WHEN {{group_column}} = 'web' THEN {{value_column}} END) AS web,
        MAX(CASE WHEN {{group_column}} = 'store' THEN {{value_column}} END) AS store,
        MAX(CASE WHEN {{group_column}} = 'mobile' THEN {{value_column}} END) AS mobile
      FROM (SELECT {{group_column}}, {{value_column}},
              ROW_NUMBER() OVER (PARTITION BY {{group_column}} ORDER BY {{value_column}}) AS rn
            FROM {{database}}.{{table}}) AS x
      GROUP BY rn) AS InputTable
  USING
    GroupColumns('web', 'store', 'mobile')
    Alpha({{alpha}})
) AS t;
```

### check_by_hand — The same F statistic from group sums

F = (between-group sum of squares / (k − 1)) / (within-group sum of squares / (N − k)). It reproduces TD_ANOVA's `f_statistic` (0.3393 on the example data), and the per-group means and variances show which groups drive it. Statistics in FLOAT: DECIMAL arithmetic truncates.

```sql
WITH g AS (
  SELECT {{group_column}} AS grp, COUNT(*) AS n,
    AVG(CAST({{value_column}} AS FLOAT)) AS mean_value,
    VAR_SAMP(CAST({{value_column}} AS FLOAT)) AS var_value
  FROM {{database}}.{{table}}
  GROUP BY 1
), a AS (
  SELECT AVG(CAST({{value_column}} AS FLOAT)) AS grand_mean, COUNT(*) AS n_all FROM {{database}}.{{table}}
)
SELECT
  (SELECT COUNT(*) FROM g) AS k,
  SUM(g.n * (g.mean_value - a.grand_mean) ** 2) / ((SELECT COUNT(*) FROM g) - 1)
    / (SUM((g.n - 1) * g.var_value) / (MAX(a.n_all) - (SELECT COUNT(*) FROM g))) AS f_statistic
FROM g CROSS JOIN a;
```

### interpret — Turn the result into a sentence

The `conclusion` column ends in a NUL character (`'00'XC`); trim it before comparing or displaying it.

```sql
SELECT f_statistic, p_value,
  CASE WHEN p_value < {{alpha}} THEN 'group means differ (reject H0)'
       ELSE 'no evidence the group means differ (do not reject H0)' END AS interpretation
FROM TD_ANOVA(
  ON (SELECT {{group_column}}, {{value_column}} FROM {{database}}.{{table}}) AS InputTable
  USING
    GroupNameColumn('{{group_column}}')
    GroupValueColumn('{{value_column}}')
    NumGroups(3)
    Alpha({{alpha}})
) AS t;
```

## Common errors

- **Error 7810 Values column must be numeric type** — The input carries a non-numeric column besides the group column (a status, a date), even when the value column itself is numeric. Fix: `ON (SELECT group_col, value_col FROM ...)`.
- **Error 7810 GroupNames and NumGroups cannot be provided simultaneously** — Fix: use one of them.
- **`conclusion` never equals 'Fail to reject Null hypothesis'** — The text ends in a NUL character. Fix: `TRIM(TRAILING '00'XC FROM conclusion)`, or compare `p_value` with alpha.
- **A significant F with no idea which group differs** — ANOVA says only that some mean differs. Fix: read the per-group means and variances (check_by_hand) or test pairs.

## Practices

- ANOVA assumes roughly normal values with similar variances per group; compare the group variances first (the F-test in statistical_tests) and transform heavily skewed measures such as revenue (for example `LN(x + 1)`).
- Decide on alpha before looking at the result.
