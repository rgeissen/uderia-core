# Customer churn analysis

Derive a churn flag from order history (no order in the last N days), then churn rate overall, by segment and tenure, churned-vs-retained metrics, risk scores.

**Use for:** measuring churn when the data has no churn column, only transactions; comparing churn across customer segments or tenure cohorts; quantifying the revenue of churned customers; listing high-value retained customers who show early signs of leaving.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the customer and order tables | `retail_demo` |
| `customer_table` | One row per customer, with descriptive columns | `customers` |
| `order_table` | Transaction history: customer_id, order_date, status, amount | `orders` |
| `churn_table` | Name for the per-customer analysis table the first statement creates | `churn_base` |
| `as_of_date` | Date the analysis is made as of (the end of the order history) | `2025-08-31` |
| `churn_window_days` | A customer with no order in this many days before `as_of_date` counts as churned | `90` |
| `segment_column` | Categorical customer column to break churn down by | `segment` |
| `min_group_size` | Smallest group whose churn rate is reported | `10` |

## Statements

### build_churn_base — One row per customer with order metrics and a churn flag

Churn is defined here as no non-cancelled order in the `churn_window_days` before `as_of_date`. Customers who never placed an order are left out by the inner join. Subtracting two DATE values gives a number of days. Every later statement reads this table.

```sql
CREATE MULTISET TABLE {{database}}.{{churn_table}} AS (
  SELECT
    c.customer_id,
    c.{{segment_column}} AS segment_value,
    c.age,
    c.income,
    c.signup_date,
    DATE '{{as_of_date}}' - c.signup_date        AS tenure_days,
    o.first_order_date,
    o.last_order_date,
    DATE '{{as_of_date}}' - o.last_order_date    AS days_since_last_order,
    o.order_count,
    o.revenue,
    o.orders_last_180,
    o.orders_prior_180,
    CASE WHEN o.last_order_date < DATE '{{as_of_date}}' - {{churn_window_days}}
         THEN 1 ELSE 0 END                       AS churned
  FROM {{database}}.{{customer_table}} AS c
  JOIN (
    SELECT
      customer_id,
      MIN(order_date) AS first_order_date,
      MAX(order_date) AS last_order_date,
      COUNT(*)        AS order_count,
      SUM(CASE WHEN status IN ('completed', 'shipped') THEN amount ELSE 0 END) AS revenue,
      SUM(CASE WHEN order_date >  DATE '{{as_of_date}}' - 180 THEN 1 ELSE 0 END) AS orders_last_180,
      SUM(CASE WHEN order_date <= DATE '{{as_of_date}}' - 180
                AND order_date >  DATE '{{as_of_date}}' - 360 THEN 1 ELSE 0 END) AS orders_prior_180
    FROM {{database}}.{{order_table}}
    WHERE status <> 'cancelled'
      AND order_date <= DATE '{{as_of_date}}'
    GROUP BY customer_id
  ) AS o
    ON o.customer_id = c.customer_id
) WITH DATA
PRIMARY INDEX (customer_id);
```

### overall_churn_metrics — Customer count, churned, retained, churn and retention rate

```sql
SELECT
  COUNT(*)                                          AS total_customers,
  SUM(churned)                                      AS churned_customers,
  COUNT(*) - SUM(churned)                           AS retained_customers,
  CAST(SUM(churned) * 100.0 / COUNT(*) AS DECIMAL(5,2))             AS churn_rate_pct,
  CAST((COUNT(*) - SUM(churned)) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS retention_rate_pct
FROM {{database}}.{{churn_table}};
```

### churn_by_segment — Churn rate and revenue per segment

`revenue_lost` is the historical revenue of the customers who churned in each segment.

```sql
SELECT
  segment_value                                         AS segment_name,
  COUNT(*)                                              AS total_customers,
  SUM(churned)                                          AS churned_customers,
  CAST(SUM(churned) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS churn_rate_pct,
  CAST(AVG(CASE WHEN churned = 1 THEN revenue END) AS DECIMAL(12,2)) AS avg_revenue_churned,
  CAST(AVG(CASE WHEN churned = 0 THEN revenue END) AS DECIMAL(12,2)) AS avg_revenue_retained,
  SUM(CASE WHEN churned = 1 THEN revenue ELSE 0 END)    AS revenue_lost
FROM {{database}}.{{churn_table}}
GROUP BY segment_value
HAVING COUNT(*) >= {{min_group_size}}
ORDER BY churn_rate_pct DESC;
```

### churned_vs_retained — Numeric profile of churned and retained customers side by side

AVG ignores NULL ages and incomes; `known_age` and `known_income` show how many rows each average rests on.

```sql
SELECT
  CASE churned WHEN 1 THEN 'churned' ELSE 'retained' END AS churn_status,
  COUNT(*)                                     AS customer_count,
  COUNT(age)                                   AS known_age,
  CAST(AVG(age) AS DECIMAL(6,1))               AS avg_age,
  COUNT(income)                                AS known_income,
  CAST(AVG(income) AS DECIMAL(12,2))           AS avg_income,
  CAST(AVG(tenure_days / 30.4) AS DECIMAL(6,1)) AS avg_tenure_months,
  CAST(AVG(order_count) AS DECIMAL(6,1))       AS avg_orders,
  CAST(AVG(revenue) AS DECIMAL(12,2))          AS avg_revenue,
  CAST(SUM(revenue) / NULLIFZERO(SUM(order_count)) AS DECIMAL(12,2)) AS avg_order_value,
  SUM(revenue)                                 AS total_revenue
FROM {{database}}.{{churn_table}}
GROUP BY 1
ORDER BY 1;
```

### tenure_analysis — Churn rate by tenure cohort

The CASE is repeated in GROUP BY; grouping by its position (`GROUP BY 1`) works as well.

```sql
SELECT
  CASE
    WHEN tenure_days <= 180 THEN '1: 0-6 months'
    WHEN tenure_days <= 365 THEN '2: 7-12 months'
    WHEN tenure_days <= 545 THEN '3: 13-18 months'
    ELSE '4: over 18 months'
  END                                                   AS tenure_cohort,
  COUNT(*)                                              AS total_customers,
  SUM(churned)                                          AS churned_customers,
  CAST(SUM(churned) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS churn_rate_pct,
  CAST(AVG(revenue) AS DECIMAL(12,2))                   AS avg_revenue
FROM {{database}}.{{churn_table}}
GROUP BY 1
ORDER BY 1;
```

### multi_segment_analysis — Churn by segment and age band, small groups dropped

Customers with no recorded age fall into their own band rather than disappearing.

```sql
SELECT
  segment_value AS segment_name,
  CASE
    WHEN age IS NULL THEN 'unknown'
    WHEN age < 35   THEN 'under 35'
    WHEN age < 55   THEN '35-54'
    ELSE '55 and over'
  END                                                   AS age_band,
  COUNT(*)                                              AS total_customers,
  SUM(churned)                                          AS churned_customers,
  CAST(SUM(churned) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS churn_rate_pct,
  CAST(AVG(revenue) AS DECIMAL(12,2))                   AS avg_revenue
FROM {{database}}.{{churn_table}}
GROUP BY 1, 2
HAVING COUNT(*) >= {{min_group_size}}
ORDER BY churn_rate_pct DESC, total_customers DESC;
```

### risk_score — Weighted churn-risk score for customers not yet churned

Points add up from leading indicators: time since the last order, fewer orders in the last 180 days than in the 180 before, short tenure, and a single order. The weights are a starting point to tune against customers who later churned.

```sql
SELECT
  r.customer_id,
  r.segment_value AS segment_name,
  r.days_since_last_order,
  r.orders_last_180,
  r.orders_prior_180,
  r.revenue,
  r.risk_score,
  CASE
    WHEN r.risk_score >= 60 THEN 'high'
    WHEN r.risk_score >= 30 THEN 'medium'
    ELSE 'low'
  END AS risk_band
FROM (
  SELECT
    b.*,
      CASE WHEN days_since_last_order > 60 THEN 40
           WHEN days_since_last_order > 30 THEN 20 ELSE 0 END
    + CASE WHEN orders_last_180 < orders_prior_180 THEN 30 ELSE 0 END
    + CASE WHEN tenure_days < 180 THEN 15 ELSE 0 END
    + CASE WHEN order_count = 1 THEN 15 ELSE 0 END AS risk_score
  FROM {{database}}.{{churn_table}} AS b
  WHERE churned = 0
) AS r
ORDER BY r.risk_score DESC, r.revenue DESC;
```

### high_value_at_risk — Top-quartile retained customers with a rising risk

PERCENTILE_CONT ... WITHIN GROUP is refused in a subquery of the WHERE clause (Error 3706, "Order-based Aggregate ... not allowed in subqueries"); compute it in a derived table and join to it.

```sql
SELECT TOP 50
  b.customer_id,
  b.segment_value AS segment_name,
  b.revenue,
  p.revenue_p75,
  b.order_count,
  b.days_since_last_order,
  b.orders_last_180,
  b.orders_prior_180
FROM {{database}}.{{churn_table}} AS b
CROSS JOIN (
  SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY revenue) AS revenue_p75
  FROM {{database}}.{{churn_table}}
) AS p
WHERE b.churned = 0
  AND b.revenue >= p.revenue_p75
  AND (b.days_since_last_order > 30 OR b.orders_last_180 < b.orders_prior_180)
ORDER BY b.revenue DESC;
```

## Common errors

- **3706 "Order-based Aggregate and Ordered Analytical Functions are not allowed in subqueries"** — PERCENTILE_CONT (or a window function) in a WHERE-clause subquery. Compute it in a derived table and join or cross join to it.
- **3707 after AS** — an alias that is a reserved word, such as `AS value` or `AS type`. Choose another name (`churn_rate_pct`, `segment_name`) or double-quote it.
- **2618 "Invalid calculation: division by zero"** — a rate whose denominator is zero for some group. Wrap the denominator in NULLIFZERO or NULLIF(..., 0).

## Practices

- Write the churn definition down before computing anything: the event that counts as activity (here a non-cancelled order) and the window (here `churn_window_days`). Different windows give very different rates.
- Fix `as_of_date` to the end of the data rather than using CURRENT_DATE when the history is not current; otherwise every customer looks churned.
- Build the per-customer table once and run every breakdown against it, so all figures share one definition.
- Report churn rates only for groups large enough to mean something (`min_group_size`).
- Weigh churn by revenue as well as by count: a few high-value churners can cost more than many small ones.
- Use NULLIFZERO or NULLIF in a denominator that can be zero.
