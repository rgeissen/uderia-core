# Duplicate detection and deduplication

Find rows that share a key, repeat in every column or differ by a typo (EDITDISTANCE, NGRAM, StringSimilarity); keep one per key, flag, delete or merge a copy.

**Use for:** counting and listing duplicates on a business key; choosing the surviving row (first, latest, most complete); removing or merging duplicates without touching the original table; measuring how much duplicates inflate a total.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to check | `customers` |
| `id_column` | Column that identifies a row | `customer_id` |
| `key_column` | Column that should be unique per entity | `email` |
| `order_column` | Column that tells which row is the latest | `signup_date` |
| `column_1` | First column that makes a row more complete | `phone` |
| `column_2` | Second such column | `age` |
| `column_3` | Third such column | `income` |
| `metric_column` | Numeric column that duplicates inflate | `income` |
| `block_column` | Column two rows must share before their keys are compared | `last_name` |
| `max_distance` | Largest edit distance that counts as a match | `1` |
| `min_similarity` | Lowest Jaro-Winkler similarity (0 to 1) that counts as a match | `0.97` |
| `work_table` | Name for the copy that is changed | `customers_work` |
| `dedup_table` | Name for a table rebuilt without duplicates | `customers_dedup` |

## Statements

### key_duplicates — Rows that share a key value, with group size and rank

Rows whose key is NULL would fall into one partition and look like duplicates of each other, so they are left out. For a key of several columns list them all in PARTITION BY.

```sql
SELECT
  {{id_column}},
  {{key_column}},
  COUNT(*) OVER (PARTITION BY {{key_column}}) AS group_size,
  ROW_NUMBER() OVER (PARTITION BY {{key_column}} ORDER BY {{id_column}}) AS group_rank,
  CASE WHEN group_rank = 1 THEN 'primary' ELSE 'duplicate' END AS record_status
FROM {{database}}.{{table}}
WHERE {{key_column}} IS NOT NULL
QUALIFY group_size > 1
ORDER BY {{key_column}}, group_rank;
```

### duplicate_summary — How many key values occur once, twice, three times

`surplus_rows` is the number of rows a deduplication would remove.

```sql
SELECT
  group_size,
  COUNT(*) AS key_values,
  SUM(group_size) AS row_count,
  SUM(group_size - 1) AS surplus_rows
FROM (
  SELECT {{key_column}}, COUNT(*) AS group_size
  FROM {{database}}.{{table}}
  WHERE {{key_column}} IS NOT NULL
  GROUP BY {{key_column}}
) AS g
GROUP BY group_size
ORDER BY group_size DESC;
```

### exact_duplicates — Rows that repeat another row in every column, and a table without them

`COUNT(DISTINCT *)` is refused (Error 3706). A DELETE cannot remove one of two identical rows, it removes both, so the table is rebuilt from SELECT DISTINCT. An INSERT ... SELECT into a SET table also drops identical rows, without an error.

```sql
-- A copy in which five rows were loaded twice
CREATE MULTISET TABLE {{database}}.{{work_table}} AS (
  SELECT * FROM {{database}}.{{table}}
) WITH DATA PRIMARY INDEX ({{id_column}});

INSERT INTO {{database}}.{{work_table}}
SELECT * FROM {{database}}.{{table}} WHERE {{id_column}} <= 5;

-- How many rows are repeats
SELECT
  COUNT(*) AS total_rows,
  d.distinct_rows,
  COUNT(*) - d.distinct_rows AS surplus_rows
FROM {{database}}.{{work_table}}
CROSS JOIN (
  SELECT COUNT(*) AS distinct_rows
  FROM (SELECT DISTINCT * FROM {{database}}.{{work_table}}) AS x
) AS d
GROUP BY d.distinct_rows;

-- Which rows: identical rows share their identifier
SELECT {{id_column}}, COUNT(*) AS copies
FROM {{database}}.{{work_table}}
GROUP BY {{id_column}}
HAVING COUNT(*) > 1
ORDER BY {{id_column}};

-- The table without the repeats
CREATE MULTISET TABLE {{database}}.{{dedup_table}} AS (
  SELECT DISTINCT * FROM {{database}}.{{work_table}}
) WITH DATA PRIMARY INDEX ({{id_column}});

SELECT COUNT(*) AS rows_after FROM {{database}}.{{dedup_table}};
```

### keep_first_or_last — One row per key: the first by identifier, or the latest

Rows with a NULL key are kept. The identifier as last ORDER BY column makes the choice repeatable when two rows tie.

```sql
-- Keep the first row of each key
SELECT t.*
FROM {{database}}.{{table}} AS t
QUALIFY {{key_column}} IS NULL
     OR ROW_NUMBER() OVER (PARTITION BY {{key_column}} ORDER BY {{id_column}}) = 1;

-- Keep the latest row of each key
SELECT t.*
FROM {{database}}.{{table}} AS t
QUALIFY {{key_column}} IS NULL
     OR ROW_NUMBER() OVER (PARTITION BY {{key_column}} ORDER BY {{order_column}} DESC, {{id_column}} DESC) = 1;
```

### keep_most_complete — One row per key: the one with the fewest NULLs

```sql
SELECT
  t.*,
  CASE WHEN {{column_1}} IS NULL THEN 0 ELSE 1 END
  + CASE WHEN {{column_2}} IS NULL THEN 0 ELSE 1 END
  + CASE WHEN {{column_3}} IS NULL THEN 0 ELSE 1 END AS completeness
FROM {{database}}.{{table}} AS t
QUALIFY {{key_column}} IS NULL
     OR ROW_NUMBER() OVER (PARTITION BY {{key_column}} ORDER BY completeness DESC, {{id_column}}) = 1;
```

### flag_and_delete — Mark duplicates on a copy, review them, then delete

A window function is not allowed in a subquery of a DELETE (Error 3706); put it in a derived table, as the UPDATE does. Deleting by identifier is only safe when the identifier is unique, so remove exact duplicates first.

```sql
CREATE MULTISET TABLE {{database}}.{{work_table}} AS (
  SELECT t.*, CAST(NULL AS INTEGER) AS duplicate_of
  FROM {{database}}.{{table}} AS t
) WITH DATA PRIMARY INDEX ({{id_column}});

-- Each duplicate points to the row that is kept
UPDATE w
FROM {{database}}.{{work_table}} AS w,
  (
    SELECT {{id_column}}, MIN({{id_column}}) OVER (PARTITION BY {{key_column}}) AS kept_id
    FROM {{database}}.{{work_table}}
    WHERE {{key_column}} IS NOT NULL
  ) AS d
SET duplicate_of = d.kept_id
WHERE w.{{id_column}} = d.{{id_column}}
  AND d.{{id_column}} <> d.kept_id;

-- Review before deleting
SELECT {{id_column}}, {{key_column}}, duplicate_of
FROM {{database}}.{{work_table}}
WHERE duplicate_of IS NOT NULL
ORDER BY duplicate_of;

DELETE FROM {{database}}.{{work_table}} WHERE duplicate_of IS NOT NULL;

-- Afterwards every non-null key occurs once: the two counts are equal
SELECT COUNT({{key_column}}) AS keyed_rows, COUNT(DISTINCT {{key_column}}) AS distinct_keys
FROM {{database}}.{{work_table}};
```

### merge_duplicates — One row per key that fills its gaps from the other rows

FIRST_VALUE ... IGNORE NULLS takes the value of the first row of the key that has one, so the kept row wins and the others only fill its NULLs. Rows with a NULL key are copied unchanged.

```sql
CREATE MULTISET TABLE {{database}}.{{dedup_table}} AS (
  SELECT
    {{id_column}},
    {{key_column}},
    FIRST_VALUE({{column_1}} IGNORE NULLS) OVER (PARTITION BY {{key_column}} ORDER BY {{id_column}}
      ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS {{column_1}},
    FIRST_VALUE({{column_2}} IGNORE NULLS) OVER (PARTITION BY {{key_column}} ORDER BY {{id_column}}
      ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS {{column_2}},
    FIRST_VALUE({{column_3}} IGNORE NULLS) OVER (PARTITION BY {{key_column}} ORDER BY {{id_column}}
      ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS {{column_3}},
    COUNT(*) OVER (PARTITION BY {{key_column}}) AS merged_rows
  FROM {{database}}.{{table}}
  WHERE {{key_column}} IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {{key_column}} ORDER BY {{id_column}}) = 1
  UNION ALL
  SELECT {{id_column}}, {{key_column}}, {{column_1}}, {{column_2}}, {{column_3}}, 1
  FROM {{database}}.{{table}}
  WHERE {{key_column}} IS NULL
) WITH DATA PRIMARY INDEX ({{id_column}});

-- The merged rows
SELECT * FROM {{database}}.{{dedup_table}} WHERE merged_rows > 1 ORDER BY {{id_column}};
```

### fuzzy_duplicates — Keys that are nearly the same, compared within a block

EDITDISTANCE counts the single-character edits between two strings and is case sensitive; NGRAM(a, b, 2) counts the two-character sequences they share. LEVENSHTEIN, JARO and JAROWINKLER do not exist as functions; the StringSimilarity table operator provides those measures on a table of pairs. The join compares only rows with the same block value, which keeps the number of pairs small and misses matches across blocks. SOUNDEX accepts Latin letters only (Error 5583 otherwise), so it suits names, not emails.

```sql
SELECT
  a.{{id_column}} AS id_1,
  b.{{id_column}} AS id_2,
  a.{{key_column}} AS value_1,
  b.{{key_column}} AS value_2,
  EDITDISTANCE(LOWER(a.{{key_column}}), LOWER(b.{{key_column}})) AS edit_distance,
  NGRAM(LOWER(a.{{key_column}}), LOWER(b.{{key_column}}), 2) AS shared_bigrams
FROM {{database}}.{{table}} AS a
JOIN {{database}}.{{table}} AS b
  ON a.{{id_column}} < b.{{id_column}}
 AND a.{{block_column}} = b.{{block_column}}
WHERE EDITDISTANCE(LOWER(a.{{key_column}}), LOWER(b.{{key_column}})) <= {{max_distance}}
ORDER BY edit_distance, id_1;

-- Jaro-Winkler and normalized Levenshtein similarity (1 = identical) for the same pairs
SELECT s.*
FROM StringSimilarity(
  ON (
    SELECT a.{{id_column}} AS id_1, b.{{id_column}} AS id_2,
           a.{{key_column}} AS value_1, b.{{key_column}} AS value_2
    FROM {{database}}.{{table}} AS a
    JOIN {{database}}.{{table}} AS b
      ON a.{{id_column}} < b.{{id_column}}
     AND a.{{block_column}} = b.{{block_column}}
  ) PARTITION BY ANY
  USING
    ComparisonColumnPairs('jaro_winkler(value_1, value_2) AS jaro_winkler', 'LD(value_1, value_2) AS levenshtein')
    Accumulate('id_1', 'id_2', 'value_1', 'value_2')
) AS s
WHERE s.jaro_winkler >= {{min_similarity}}
ORDER BY s.jaro_winkler DESC, s.id_1;
```

### duplicate_impact — How much the duplicates add to a total

```sql
SELECT
  record_type,
  COUNT(*) AS row_count,
  SUM({{metric_column}}) AS total_metric,
  CAST(AVG({{metric_column}}) AS DECIMAL(18,2)) AS avg_metric
FROM (
  SELECT
    {{metric_column}},
    CASE
      WHEN {{key_column}} IS NOT NULL
       AND ROW_NUMBER() OVER (PARTITION BY {{key_column}} ORDER BY {{id_column}}) > 1 THEN 'duplicate'
      ELSE 'kept'
    END AS record_type
  FROM {{database}}.{{table}}
) AS f
GROUP BY record_type;
```

## Common errors

- **3706 "Order-based Aggregate and Ordered Analytical Functions are not allowed in subqueries"** — ROW_NUMBER inside `WHERE id IN (SELECT ... QUALIFY ...)`. Wrap the window query in a derived table and select from it.
- **3706 on `COUNT(DISTINCT *)`** — count the rows of `(SELECT DISTINCT * FROM t)`.
- **A delete by identifier removes more rows than expected** — an identical row was loaded twice, so both copies carry the identifier. Rebuild with SELECT DISTINCT first.
- **NULL keys are reported as one large duplicate group** — all NULLs share a partition. Filter `key IS NOT NULL`.
- **2802 "Duplicate row error"** — INSERT ... VALUES of a row that already exists in a SET table.
- **2803 "Secondary index uniqueness violation"** — CREATE UNIQUE INDEX on a column that still has duplicates, or more than one NULL.
- **5583 "Invalid Input: only Latin letters allowed"** — SOUNDEX on a value with digits or punctuation.
- **3706 on LEVENSHTEIN, JARO or JAROWINKLER** — use EDITDISTANCE, or the StringSimilarity operator.

## Practices

- Decide first what makes two rows the same entity: every column, a business key, or a near match.
- Count before and after: `COUNT(key)` equals `COUNT(DISTINCT key)` once the duplicates are gone.
- Flag duplicates and review them before deleting; work on a copy and swap it in when the result is checked.
- End every ORDER BY that picks the survivor with a unique column, otherwise the choice can change between runs.
- Merge instead of delete when the duplicate rows hold values the kept row lacks.
- Compare fuzzy pairs only within a block (same last name, postal code or first letter); a join of every row with every other row grows with the square of the row count.
- Treat fuzzy matches as candidates for review, not as proof.
- After cleaning, declare the key unique (a unique primary or secondary index) and load with MERGE, which updates a row that exists instead of inserting it again.
