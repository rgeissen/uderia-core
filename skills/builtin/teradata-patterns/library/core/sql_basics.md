# Teradata SQL basics and dialect differences

Teradata forms of everyday SQL that differ from PostgreSQL and MySQL: QUALIFY, TOP and SAMPLE, REPLACE VIEW, volatile tables, CTAS, reserved words.

**Use for:** writing a first correct statement on Teradata; converting a statement written for another database; fixing a syntax error (3706, 3707) on a statement that looks right.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to read | `events` |
| `partition_column` | Column that identifies one group | `customer_id` |
| `order_column` | Column that orders rows within a group | `event_ts` |
| `new_table` | Name for a table the statement creates | `events_copy` |
| `view_name` | Name for a view the statement creates | `recent_events` |

## Statements

### qualify_dedup — Keep the most recent row per key

`SELECT *, <expression>` is refused (Error 3706, "SELECT * must have a FROM clause"). Give the table an alias and write `t.*`.

```sql
SELECT t.*
FROM {{database}}.{{table}} AS t
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY {{partition_column}}
  ORDER BY {{order_column}} DESC
) = 1;
```

### qualify_topn — Top three rows per group

RANK keeps ties; use ROW_NUMBER for exactly three.

```sql
SELECT t.*,
  RANK() OVER (
    PARTITION BY {{partition_column}}
    ORDER BY {{order_column}} DESC
  ) AS rnk
FROM {{database}}.{{table}} AS t
QUALIFY rnk <= 3;
```

### row_limits — TOP, SAMPLE, and a limit on a QUALIFY query

There is no LIMIT. TOP and QUALIFY cannot appear in the same SELECT (Error 6916); put the QUALIFY query in a derived table and apply TOP outside it.

```sql
-- The first rows in a stated order
SELECT TOP 10 * FROM {{database}}.{{table}} ORDER BY {{order_column}} DESC;

-- A fixed number of random rows, then a fraction of the rows
SELECT * FROM {{database}}.{{table}} SAMPLE 100;
SELECT * FROM {{database}}.{{table}} SAMPLE 0.05;

-- A row limit on a query that has a QUALIFY
SELECT TOP 10 q.*
FROM (
  SELECT t.*
  FROM {{database}}.{{table}} AS t
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {{partition_column}} ORDER BY {{order_column}} DESC) = 1
) AS q
ORDER BY q.{{order_column}} DESC;

-- Rows 11 to 20 in a stated order (an offset): a QUALIFY window and no TOP
SELECT t.*
FROM {{database}}.{{table}} AS t
QUALIFY ROW_NUMBER() OVER (ORDER BY {{order_column}} DESC) BETWEEN 11 AND 20;
```

### replace_view — Create or replace a view

`CREATE OR REPLACE VIEW` is refused. REPLACE VIEW creates the view or replaces it.

```sql
REPLACE VIEW {{database}}.{{view_name}} AS
SELECT {{partition_column}}, {{order_column}}, event_type
FROM {{database}}.{{table}}
WHERE {{order_column}} >= TIMESTAMP '2025-01-01 00:00:00';

SELECT COUNT(*) FROM {{database}}.{{view_name}};
```

### volatile_table — A table that lives for the session

It is dropped when the session ends. Without ON COMMIT PRESERVE ROWS it is emptied at the end of each transaction.

```sql
CREATE VOLATILE TABLE tmp_{{table}} AS (
  SELECT {{partition_column}}, COUNT(*) AS event_count
  FROM {{database}}.{{table}}
  GROUP BY {{partition_column}}
) WITH DATA
ON COMMIT PRESERVE ROWS;

SELECT TOP 5 * FROM tmp_{{table}} ORDER BY event_count DESC;
```

### ctas — Create a table from a query

WITH DATA or WITH NO DATA is required. Without a PRIMARY INDEX clause the first column becomes the primary index; name one, or say NO PRIMARY INDEX for a staging table.

```sql
-- Copy rows
CREATE MULTISET TABLE {{database}}.{{new_table}} AS (
  SELECT * FROM {{database}}.{{table}} WHERE event_type = 'purchase'
) WITH DATA
PRIMARY INDEX ({{partition_column}});

DROP TABLE {{database}}.{{new_table}};

-- Copy the structure only, as a staging table with no primary index
CREATE MULTISET TABLE {{database}}.{{new_table}} AS (
  SELECT * FROM {{database}}.{{table}}
) WITH NO DATA
NO PRIMARY INDEX;
```

### reserved_words — A reserved word used as a name

A column or alias named like a keyword (type, date, time, value, name, format, title, level, mode, status, class, key, index, year, month, day, hour, minute, second, amp) must be double-quoted (Error 3707 otherwise). Quoting a word that is not reserved is harmless.

```sql
SELECT event_type AS "type", CAST({{order_column}} AS DATE) AS "date", COUNT(*) AS "value"
FROM {{database}}.{{table}}
GROUP BY 1, 2
ORDER BY "value" DESC;
```

### operators — Not-equal, set difference, concatenation, booleans

```sql
-- Not-equal is <>. The form != is refused (Error 3707).
SELECT COUNT(*) FROM {{database}}.{{table}} WHERE event_type <> 'view';

-- Set difference: MINUS and EXCEPT are both accepted. Each SELECT must read a table.
SELECT {{partition_column}} FROM {{database}}.{{table}} WHERE event_type = 'add_to_cart'
MINUS
SELECT {{partition_column}} FROM {{database}}.{{table}} WHERE event_type = 'purchase';

-- Strings are joined with ||. The + operator converts both sides to numbers.
SELECT TOP 5 event_type || ' on ' || page AS what FROM {{database}}.{{table}};

-- There are no boolean literals: compare with 1 and 0.
SELECT COUNT(*) FROM {{database}}.{{table}}
WHERE CASE WHEN event_type = 'purchase' THEN 1 ELSE 0 END = 1;
```

## Common errors

- **3706 "SELECT * must have a FROM clause"** — `SELECT *, expr`. Write `t.*, expr` with a table alias.
- **3706 near LIMIT** — there is no LIMIT. Use `SELECT TOP n`, or a QUALIFY window for an offset.
- **6916 "Top N option is not supported with QUALIFY clause"** — move the QUALIFY query into a derived table and apply TOP outside it.
- **3707 naming a keyword after AS** — a reserved word used as an alias. Double-quote it.
- **3707 on `!=`** — write `<>`.
- **3706 on CREATE OR REPLACE VIEW** — write REPLACE VIEW.
- **3888 "A SELECT for a UNION, INTERSECT or MINUS must reference a table"** — a set operation between two literal-only SELECTs. Read a table in each.
- **A volatile table "disappears"** — it belongs to the session. Create it again in a new session, or use a permanent table.

## Practices

- Filter on a window function with QUALIFY; it needs no subquery.
- Use a WITH clause for multi-step logic. It reads better than nested derived tables and plans the same.
- Write explicit JOIN ... ON; never comma joins.
- For a look at the data use SAMPLE. TOP without ORDER BY returns arbitrary rows.
- Use a volatile table for an intermediate result that several statements of one session read.
- Choose NO PRIMARY INDEX for a staging table whose access pattern is not known yet; a poor primary index skews the rows across AMPs.
