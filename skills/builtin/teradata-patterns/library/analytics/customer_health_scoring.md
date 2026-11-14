# Customer health scoring

A 0-100 customer health score from order recency, frequency and spend plus click-stream activity, as weighted components with tiers and actions.

**Use for:** ranking customers by health for retention or account-management work; finding at-risk and expansion-ready customers; a health-tier summary for a dashboard; turning several behaviour metrics into one weighted score that tolerates missing data.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the customer, order and event tables | `retail_demo` |
| `customer_table` | One row per customer | `customers` |
| `order_table` | Transactions: customer_id, order_date, status, amount | `orders` |
| `event_table` | Activity log: customer_id, event_ts, event_type | `events` |
| `health_table` | Name for the per-customer metrics table the first statement creates | `health_inputs` |
| `score_view` | Name for the view that computes the scores | `customer_health` |
| `as_of_date` | Date the score is computed as of | `2025-08-31` |
| `lookback_days` | Days of history that count toward frequency, spend and activity | `365` |
| `weight_recency` | Weight of the recency component | `30` |
| `weight_frequency` | Weight of the order-frequency component | `25` |
| `weight_monetary` | Weight of the spend component | `25` |
| `weight_activity` | Weight of the click-stream activity component | `20` |

## Statements

### build_health_inputs — Raw per-customer metrics: recency, frequency, spend, activity

LEFT JOINs keep every customer, including those with no order or no event in the window; their metrics stay NULL so the score can tell "no data" from "zero". Cancelled orders do not count as activity; only completed and shipped orders count as spend.

```sql
CREATE MULTISET TABLE {{database}}.{{health_table}} AS (
  SELECT
    c.customer_id,
    c.segment,
    o.last_order_date,
    DATE '{{as_of_date}}' - o.last_order_date AS days_since_last_order,
    o.orders_in_window,
    o.spend_in_window,
    e.events_in_window,
    e.purchase_events,
    e.last_event_date
  FROM {{database}}.{{customer_table}} AS c
  LEFT JOIN (
    SELECT
      customer_id,
      MAX(order_date) AS last_order_date,
      SUM(CASE WHEN order_date > DATE '{{as_of_date}}' - {{lookback_days}} THEN 1 ELSE 0 END) AS orders_in_window,
      SUM(CASE WHEN order_date > DATE '{{as_of_date}}' - {{lookback_days}}
                AND status IN ('completed', 'shipped') THEN amount ELSE 0 END) AS spend_in_window
    FROM {{database}}.{{order_table}}
    WHERE status <> 'cancelled'
      AND order_date <= DATE '{{as_of_date}}'
    GROUP BY customer_id
  ) AS o
    ON o.customer_id = c.customer_id
  LEFT JOIN (
    SELECT
      customer_id,
      COUNT(*) AS events_in_window,
      SUM(CASE WHEN event_type = 'purchase' THEN 1 ELSE 0 END) AS purchase_events,
      MAX(CAST(event_ts AS DATE)) AS last_event_date
    FROM {{database}}.{{event_table}}
    WHERE CAST(event_ts AS DATE) >  DATE '{{as_of_date}}' - {{lookback_days}}
      AND CAST(event_ts AS DATE) <= DATE '{{as_of_date}}'
    GROUP BY customer_id
  ) AS e
    ON e.customer_id = c.customer_id
) WITH DATA
PRIMARY INDEX (customer_id);
```

### health_score_view — Component scores, weighted composite, tier and action

Each component is scored 0-100 on fixed thresholds; set them from your own data. The composite divides by the sum of the weights of the components that have data, so a customer with no click-stream data is scored on the other three instead of losing the activity points. A customer with no data at all gets a NULL score.

```sql
REPLACE VIEW {{database}}.{{score_view}} AS
WITH components AS (
  SELECT
    customer_id,
    segment,
    days_since_last_order,
    orders_in_window,
    spend_in_window,
    events_in_window,
    CASE
      WHEN days_since_last_order IS NULL THEN NULL
      WHEN days_since_last_order <= 30  THEN 100
      WHEN days_since_last_order <= 90  THEN 75
      WHEN days_since_last_order <= 180 THEN 50
      WHEN days_since_last_order <= 365 THEN 25
      ELSE 0
    END AS recency_score,
    CASE
      WHEN orders_in_window IS NULL THEN NULL
      WHEN orders_in_window >= 5 THEN 100
      WHEN orders_in_window >= 3 THEN 70
      WHEN orders_in_window >= 2 THEN 50
      WHEN orders_in_window >= 1 THEN 25
      ELSE 0
    END AS frequency_score,
    CASE
      WHEN spend_in_window IS NULL THEN NULL
      WHEN spend_in_window >= 5000 THEN 100
      WHEN spend_in_window >= 2500 THEN 75
      WHEN spend_in_window >= 1000 THEN 50
      WHEN spend_in_window >  0    THEN 25
      ELSE 0
    END AS monetary_score,
    CASE
      WHEN events_in_window IS NULL THEN NULL
      WHEN events_in_window >= 20 THEN 100
      WHEN events_in_window >= 12 THEN 75
      WHEN events_in_window >= 6  THEN 50
      ELSE 25
    END AS activity_score
  FROM {{database}}.{{health_table}}
), weighted AS (
  SELECT
    c.*,
      COALESCE(recency_score   * {{weight_recency}},   0)
    + COALESCE(frequency_score * {{weight_frequency}}, 0)
    + COALESCE(monetary_score  * {{weight_monetary}},  0)
    + COALESCE(activity_score  * {{weight_activity}},  0) AS weighted_points,
      CASE WHEN recency_score   IS NULL THEN 0 ELSE {{weight_recency}}   END
    + CASE WHEN frequency_score IS NULL THEN 0 ELSE {{weight_frequency}} END
    + CASE WHEN monetary_score  IS NULL THEN 0 ELSE {{weight_monetary}}  END
    + CASE WHEN activity_score  IS NULL THEN 0 ELSE {{weight_activity}}  END AS available_weight
  FROM components AS c
)
SELECT
  customer_id,
  segment,
  recency_score,
  frequency_score,
  monetary_score,
  activity_score,
  available_weight,
  CAST(weighted_points / NULLIFZERO(available_weight) AS DECIMAL(5,1)) AS health_score,
  CASE
    WHEN available_weight = 0 THEN 'No data'
    WHEN weighted_points / available_weight >= 80 THEN 'Excellent'
    WHEN weighted_points / available_weight >= 65 THEN 'Good'
    WHEN weighted_points / available_weight >= 50 THEN 'Fair'
    WHEN weighted_points / available_weight >= 35 THEN 'At risk'
    ELSE 'Critical'
  END AS health_tier,
  CASE
    WHEN available_weight = 0 THEN 'No activity on record'
    WHEN weighted_points / available_weight >= 75 AND monetary_score >= 75 THEN 'Expansion candidate'
    WHEN weighted_points / available_weight < 40 THEN 'Retention risk: intervene now'
    WHEN weighted_points / available_weight < 60 THEN 'Retention risk: proactive contact'
    WHEN recency_score <= 25 THEN 'Lapsing: win-back offer'
    WHEN activity_score IS NULL OR activity_score <= 25 THEN 'Low engagement: re-engage online'
    ELSE 'Stable'
  END AS recommended_action
FROM weighted;

SELECT TOP 20 *
FROM {{database}}.{{score_view}}
WHERE health_score IS NOT NULL
ORDER BY health_score ASC, customer_id;
```

### tier_distribution — Customers, average component scores and spend per tier

```sql
SELECT
  s.health_tier,
  COUNT(*)                                      AS customers,
  CAST(AVG(s.health_score)    AS DECIMAL(5,1))  AS avg_health,
  CAST(AVG(s.recency_score)   AS DECIMAL(5,1))  AS avg_recency,
  CAST(AVG(s.frequency_score) AS DECIMAL(5,1))  AS avg_frequency,
  CAST(AVG(s.monetary_score)  AS DECIMAL(5,1))  AS avg_monetary,
  CAST(AVG(s.activity_score)  AS DECIMAL(5,1))  AS avg_activity,
  SUM(h.spend_in_window)                        AS spend_in_window
FROM {{database}}.{{score_view}} AS s
JOIN {{database}}.{{health_table}} AS h
  ON h.customer_id = s.customer_id
GROUP BY s.health_tier
ORDER BY MIN(s.health_score) DESC;
```

### action_worklist — Customers per recommended action, by segment

```sql
SELECT
  recommended_action,
  segment,
  COUNT(*) AS customers,
  CAST(AVG(health_score) AS DECIMAL(5,1)) AS avg_health
FROM {{database}}.{{score_view}}
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
```

### relative_score — Component scores from percentile ranks instead of fixed thresholds

PERCENT_RANK scores each customer against the others (0 = lowest, 1 = highest), so no thresholds need tuning; the result shifts when the population shifts. Recency is ranked descending because fewer days is better. Customers with a NULL metric are excluded here.

```sql
SELECT TOP 20 r.*
FROM (
  SELECT
    customer_id,
    segment,
    CAST(100 * PERCENT_RANK() OVER (ORDER BY days_since_last_order DESC) AS DECIMAL(5,1)) AS recency_pct,
    CAST(100 * PERCENT_RANK() OVER (ORDER BY orders_in_window)            AS DECIMAL(5,1)) AS frequency_pct,
    CAST(100 * PERCENT_RANK() OVER (ORDER BY spend_in_window)             AS DECIMAL(5,1)) AS monetary_pct,
    CAST(100 * PERCENT_RANK() OVER (ORDER BY events_in_window)            AS DECIMAL(5,1)) AS activity_pct
  FROM {{database}}.{{health_table}}
  WHERE days_since_last_order IS NOT NULL
    AND events_in_window IS NOT NULL
) AS r
ORDER BY
  (r.recency_pct * {{weight_recency}} + r.frequency_pct * {{weight_frequency}}
   + r.monetary_pct * {{weight_monetary}} + r.activity_pct * {{weight_activity}})
  / ({{weight_recency}} + {{weight_frequency}} + {{weight_monetary}} + {{weight_activity}}) ASC;
```

## Common errors

- **2618 "Invalid calculation: division by zero"** — a composite divided by the sum of available weights when a customer has no data at all. Divide by NULLIFZERO(...) so the score becomes NULL.
- **3706 "Order-based Aggregate and Ordered Analytical Functions are not allowed in subqueries"** — PERCENT_RANK or another window function in a subquery used as a predicate (for example `WHERE customer_id IN (SELECT ... QUALIFY PERCENT_RANK() ...)`). Compute the ranks in a derived table in FROM, as `relative_score` does.

## Practices

- Fix `as_of_date` to the end of the data when it is not current; CURRENT_DATE would make every customer look lapsed.
- Keep "no data" (NULL) apart from "zero": score missing components out of the weights that are available rather than counting them as zero.
- Aggregate behaviour over a lookback window, not a single day, so one quiet day does not move the score.
- Report tiers and actions to business users; the raw score is for ranking.
- Check the thresholds against the distribution of each metric before trusting the tiers, and recheck the weights against customers who later churned.
- Materialise the view into a table for dashboards that read it often.
