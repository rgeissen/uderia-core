# Data validation rules

Find rows that break a rule: required fields, ranges, allowed values, REGEXP_SIMILAR formats, TRYCAST conversions, orphaned keys, duplicates, CHECK constraints.

**Use for:** testing a table before or after a load; finding the rows that break a rule and counting failures per rule; checking that text can be converted to a date or number; splitting a load into accepted and rejected rows.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the tables | `retail_demo` |
| `table` | Table to validate | `customers` |
| `id_column` | Column that identifies a row of the table | `customer_id` |
| `email_column` | Column that must hold an email address | `email` |
| `age_column` | Integer column that must lie between 0 and 120 | `age` |
| `date_column` | Date column that must not lie in the future | `signup_date` |
| `numeric_column` | Numeric column to check against a range | `income` |
| `min_value` | Lowest accepted value | `20000` |
| `max_value` | Highest accepted value | `200000` |
| `text_column` | Required character column to check against a pattern | `phone` |
| `format_pattern` | Regular expression a valid value matches in full | `^[0-9]{3}-[0-9]{3}-[0-9]{4}$` |
| `category_column` | Column that may hold only listed values | `segment` |
| `allowed_values` | The accepted values, as a quoted list | `'consumer', 'business'` |
| `child_table` | Table whose rows refer to the validated table | `orders` |
| `child_id` | Column that identifies a row of the child table | `order_id` |
| `foreign_key` | Child column that holds the parent's `id_column` value | `customer_id` |
| `child_date_column` | Child date that must not precede the parent's `date_column` | `order_date` |
| `text_date_column` | Character column of the child table that should hold a date | `order_date_text` |
| `valid_table` | Name for the table of accepted rows | `customers_valid` |
| `rejected_table` | Name for the table of rejected rows | `customers_rejected` |

## Statements

### basic_validation — Rows that fail a required-field, format or range rule, with the finding per column

REGEXP_SIMILAR returns 1 or 0 and cannot stand alone as a condition: `NOT REGEXP_SIMILAR(...)` is refused (Error 3707). NULL and the empty string are different values; `= ''` is also true for a value of only spaces, because trailing spaces are ignored in a comparison. An alias from the SELECT list can be used in the WHERE clause.

```sql
SELECT
  {{id_column}},
  CASE
    WHEN {{email_column}} IS NULL OR {{email_column}} = '' THEN 'missing'
    WHEN REGEXP_SIMILAR({{email_column}}, '^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$', 'i') = 0 THEN 'invalid format'
    ELSE 'valid'
  END AS email_check,
  CASE
    WHEN {{text_column}} IS NULL OR {{text_column}} = '' THEN 'missing'
    ELSE 'valid'
  END AS text_check,
  CASE
    WHEN {{age_column}} IS NULL THEN 'missing'
    WHEN {{age_column}} NOT BETWEEN 0 AND 120 THEN 'out of range'
    ELSE 'valid'
  END AS age_check,
  CASE
    WHEN {{date_column}} IS NULL THEN 'missing'
    WHEN {{date_column}} > CURRENT_DATE THEN 'in the future'
    WHEN {{date_column}} < DATE '1900-01-01' THEN 'before 1900'
    ELSE 'valid'
  END AS date_check
FROM {{database}}.{{table}}
WHERE email_check <> 'valid' OR text_check <> 'valid' OR age_check <> 'valid' OR date_check <> 'valid'
ORDER BY {{id_column}};
```

### validation_summary — Failure counts per rule and the share of rows that fail any rule

A comparison with NULL is neither true nor false, so each range rule tests IS NULL separately.

```sql
SELECT
  COUNT(*) AS total_rows,
  SUM(email_failed) AS email_failures,
  SUM(age_failed) AS age_failures,
  SUM(range_failed) AS range_failures,
  SUM(date_failed) AS date_failures,
  SUM(CASE WHEN email_failed + age_failed + range_failed + date_failed > 0 THEN 1 ELSE 0 END) AS failed_rows,
  CAST(SUM(CASE WHEN email_failed + age_failed + range_failed + date_failed > 0 THEN 1 ELSE 0 END) * 100.00
       / COUNT(*) AS DECIMAL(5,2)) AS failure_pct
FROM (
  SELECT
    CASE WHEN {{email_column}} IS NULL
           OR REGEXP_SIMILAR({{email_column}}, '^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$', 'i') = 0
         THEN 1 ELSE 0 END AS email_failed,
    CASE WHEN {{age_column}} IS NULL OR {{age_column}} NOT BETWEEN 0 AND 120 THEN 1 ELSE 0 END AS age_failed,
    CASE WHEN {{numeric_column}} IS NULL OR {{numeric_column}} NOT BETWEEN {{min_value}} AND {{max_value}}
         THEN 1 ELSE 0 END AS range_failed,
    CASE WHEN {{date_column}} IS NULL OR {{date_column}} > CURRENT_DATE THEN 1 ELSE 0 END AS date_failed
  FROM {{database}}.{{table}}
) AS r;
```

### range_validation — Values below the minimum or above the maximum, with the distance

```sql
SELECT
  {{id_column}},
  {{numeric_column}},
  CASE WHEN {{numeric_column}} < {{min_value}} THEN 'below minimum' ELSE 'above maximum' END AS range_check,
  CASE
    WHEN {{numeric_column}} < {{min_value}} THEN {{min_value}} - {{numeric_column}}
    ELSE {{numeric_column}} - {{max_value}}
  END AS distance
FROM {{database}}.{{table}}
WHERE {{numeric_column}} < {{min_value}} OR {{numeric_column}} > {{max_value}}
ORDER BY distance DESC;
```

### enum_validation — Values outside the allowed list, with their row counts

NOT IN never returns a row whose value is NULL, so NULL is tested separately.

```sql
SELECT
  {{category_column}},
  COUNT(*) AS row_count
FROM {{database}}.{{table}}
WHERE {{category_column}} IS NULL
   OR {{category_column}} NOT IN ({{allowed_values}})
GROUP BY {{category_column}}
ORDER BY row_count DESC;
```

### format_validation — Values that do not match a regular expression

The pattern must match the whole value: `'^https?://[a-z0-9.-]+'` does not match a URL that has a path. Add `'i'` as third argument to ignore case. Other patterns: email `^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$`; ISO date `^[0-9]{4}-[0-9]{2}-[0-9]{2}$`; US ZIP code `^[0-9]{5}(-[0-9]{4})?$`; URL `^https?://[a-z0-9.-]+.*$`.

```sql
SELECT
  {{id_column}},
  {{text_column}}
FROM {{database}}.{{table}}
WHERE {{text_column}} IS NOT NULL
  AND REGEXP_SIMILAR({{text_column}}, '{{format_pattern}}') = 0
ORDER BY {{id_column}};
```

### data_type_validation — Text that cannot be converted to a date or a number

TRYCAST returns NULL where CAST would fail (Error 2665 for an invalid date, 2620 for a bad character). For a date it is the better test: `'2024-02-30'` matches the ISO pattern but is not a date. For a number it is too tolerant to prove the form: `TRYCAST('2024-03-05' AS INTEGER)` returns 20240305 and `TRYCAST('' AS INTEGER)` returns 0. Test the form with a pattern and use TRYCAST for the range.

```sql
-- How many values convert
SELECT
  COUNT(*) AS total_rows,
  COUNT({{text_date_column}}) AS non_null_rows,
  SUM(CASE WHEN TRYCAST({{text_date_column}} AS DATE) IS NOT NULL THEN 1 ELSE 0 END) AS date_rows,
  SUM(CASE WHEN REGEXP_SIMILAR({{text_date_column}}, '^-?[0-9]+$') = 1
            AND TRYCAST({{text_date_column}} AS INTEGER) IS NOT NULL THEN 1 ELSE 0 END) AS integer_rows
FROM {{database}}.{{child_table}};

-- The rows that are not a date
SELECT {{child_id}}, {{text_date_column}}
FROM {{database}}.{{child_table}}
WHERE {{text_date_column}} IS NOT NULL
  AND TRYCAST({{text_date_column}} AS DATE) IS NULL;
```

### uniqueness_validation — Rows whose value in a column that should be unique occurs more than once

```sql
SELECT
  {{id_column}},
  {{email_column}},
  COUNT(*) OVER (PARTITION BY {{email_column}}) AS occurrences
FROM {{database}}.{{table}}
WHERE {{email_column}} IS NOT NULL
QUALIFY occurrences > 1
ORDER BY {{email_column}}, {{id_column}};
```

### referential_integrity — Child rows whose foreign key value has no parent row

```sql
-- Totals: child rows, rows with no foreign key value, rows whose value has no parent
SELECT
  COUNT(*) AS child_rows,
  SUM(CASE WHEN c.{{foreign_key}} IS NULL THEN 1 ELSE 0 END) AS null_foreign_keys,
  SUM(CASE WHEN c.{{foreign_key}} IS NOT NULL AND p.{{id_column}} IS NULL THEN 1 ELSE 0 END) AS orphaned_rows
FROM {{database}}.{{child_table}} AS c
LEFT JOIN {{database}}.{{table}} AS p
  ON p.{{id_column}} = c.{{foreign_key}};

-- The orphaned child rows
SELECT c.{{child_id}}, c.{{foreign_key}}
FROM {{database}}.{{child_table}} AS c
WHERE c.{{foreign_key}} IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM {{database}}.{{table}} AS p
    WHERE p.{{id_column}} = c.{{foreign_key}}
  );
```

### cross_field_validation — Dates that contradict each other across a parent and its child rows

Subtracting two dates gives a number of days; comparing that with `INTERVAL '1' YEAR` fails (Error 7453). Use ADD_MONTHS.

```sql
SELECT
  c.{{child_id}},
  p.{{date_column}},
  c.{{child_date_column}},
  CASE
    WHEN c.{{child_date_column}} IS NULL OR p.{{date_column}} IS NULL THEN 'date missing'
    WHEN c.{{child_date_column}} < p.{{date_column}} THEN 'child date before parent date'
    ELSE 'more than a year apart'
  END AS finding
FROM {{database}}.{{child_table}} AS c
JOIN {{database}}.{{table}} AS p
  ON p.{{id_column}} = c.{{foreign_key}}
WHERE c.{{child_date_column}} IS NULL
   OR p.{{date_column}} IS NULL
   OR c.{{child_date_column}} < p.{{date_column}}
   OR c.{{child_date_column}} > ADD_MONTHS(p.{{date_column}}, 12)
ORDER BY c.{{child_id}};
```

### enforce_and_quarantine — Declare the rules as constraints and split a load into accepted and rejected rows

A row that breaks a constraint makes the whole INSERT fail, so the SELECT keeps only rows that pass and the others go to a reject table with the reason.

```sql
CREATE MULTISET TABLE {{database}}.{{valid_table}} (
  {{id_column}} INTEGER NOT NULL,
  {{email_column}} VARCHAR(100) NOT NULL,
  {{age_column}} INTEGER CHECK ({{age_column}} BETWEEN 0 AND 120),
  {{date_column}} DATE NOT NULL CHECK ({{date_column}} <= CURRENT_DATE),
  CONSTRAINT email_shape CHECK ({{email_column}} LIKE '%_@_%._%')
) UNIQUE PRIMARY INDEX ({{id_column}});

CREATE MULTISET TABLE {{database}}.{{rejected_table}} AS (
  SELECT
    {{id_column}}, {{email_column}}, {{age_column}}, {{date_column}},
    CASE
      WHEN {{email_column}} IS NULL THEN 'email missing'
      WHEN {{email_column}} NOT LIKE '%_@_%._%' THEN 'email format'
      WHEN {{age_column}} NOT BETWEEN 0 AND 120 THEN 'age out of range'
      WHEN {{date_column}} IS NULL THEN 'date missing'
      ELSE 'date in the future'
    END AS reject_reason
  FROM {{database}}.{{table}}
  WHERE {{email_column}} IS NULL
     OR {{email_column}} NOT LIKE '%_@_%._%'
     OR {{age_column}} NOT BETWEEN 0 AND 120
     OR {{date_column}} IS NULL
     OR {{date_column}} > CURRENT_DATE
) WITH DATA
PRIMARY INDEX ({{id_column}});

INSERT INTO {{database}}.{{valid_table}}
SELECT {{id_column}}, {{email_column}}, {{age_column}}, {{date_column}}
FROM {{database}}.{{table}}
WHERE {{id_column}} NOT IN (SELECT {{id_column}} FROM {{database}}.{{rejected_table}});

SELECT reject_reason, COUNT(*) AS row_count
FROM {{database}}.{{rejected_table}}
GROUP BY reject_reason
UNION ALL
SELECT 'accepted', COUNT(*)
FROM {{database}}.{{valid_table}};
```

## Common errors

- **3707 after `NOT REGEXP_SIMILAR(...)`** — the function returns 1 or 0. Write `REGEXP_SIMILAR(...) = 0`.
- **7453 "Interval field overflow"** — a date difference compared with an INTERVAL. Compare the later date with `ADD_MONTHS(earlier, n)`.
- **2665 "Invalid date" / 2620 "The format or data contains a bad character"** — CAST of text that is not a date or number. Find the rows with TRYCAST first.
- **5317 "Check constraint violation"** — a row breaks a CHECK constraint; the message names the column. Filter such rows into a reject table before the INSERT.
- **3811 / 3604 NOT NULL** — a NULL for a NOT NULL column (3811 from INSERT VALUES, 3604 from INSERT SELECT).
- **2801 "Duplicate unique prime key error"** — a second row with the same unique primary index value.
- **A NOT IN check returns nothing for NULL values** — add `OR column IS NULL`.

## Practices

- Validate where the data enters: a load writes accepted rows to the target and rejected rows, with the reason, to a reject table.
- Write each rule once: the column, the accepted range or list, and whether NULL is allowed.
- Declare NOT NULL, CHECK and unique indexes for rules that must never be broken; the database then refuses a statement that would store such a row.
- Keep format patterns permissive. Phone numbers and names vary by country; a strict pattern rejects valid values.
- Count failures per rule after every load and compare with the previous load.
- Test a rule with a value that must pass, one that must fail, a NULL and an empty string.
