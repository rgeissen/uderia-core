# Teradata data types and casting

CAST with FORMAT, TRYCAST, TO_NUMBER, TO_DATE and TO_CHAR for numbers, dates and timestamps; date and interval arithmetic; UNION typing; AIVector columns.

**Use for:** converting a text column to a number, date or timestamp; finding the rows that will not convert; turning a date or number into display text; date differences and intervals; a comparison or UNION between columns of different types; storing embeddings in a vector column.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the table | `retail_demo` |
| `table` | Table to read | `orders` |
| `numeric_column` | A DECIMAL or INTEGER column | `amount` |
| `date_text_column` | A character column that holds dates as text | `order_date_text` |
| `date_column` | A DATE column | `order_date` |
| `timestamp_column` | A TIMESTAMP column | `order_ts` |
| `vector_table` | Name for a table with a vector column that the statements create | `doc_embeddings` |

## Statements

### numeric_cast — Cast between numeric types

A cast to INTEGER or BIGINT drops the fraction (871.83 gives 871). A cast to a DECIMAL with fewer decimal places rounds (871.83 gives 872); on this system a half rounds to the even digit (12.5 gives 12, 13.5 gives 14). A value that does not fit the target raises Error 2616 or 2617. INTEGER / INTEGER stays INTEGER; TYPE shows the type of any expression.

```sql
SELECT
  {{numeric_column}},
  CAST({{numeric_column}} AS INTEGER) AS as_integer,
  CAST({{numeric_column}} AS BIGINT) AS as_bigint,
  CAST({{numeric_column}} AS DECIMAL(10,0)) AS as_rounded,
  CAST({{numeric_column}} AS FLOAT) AS as_float,
  order_id / 3 AS integer_division,
  CAST(order_id AS DECIMAL(12,2)) / 3 AS decimal_division,
  TYPE(order_id / 3) AS type_of_integer_division
FROM {{database}}.{{table}}
SAMPLE 10;
```

### string_to_number — Convert text to a number

CAST accepts digits, a sign, a decimal point, group commas, an exponent and surrounding blanks; a currency or percent sign needs a FORMAT phrase. TO_NUMBER returns NULL, not an error, for text that is not a number. An empty string casts to 0.

```sql
SELECT
  CAST('  42 ' AS INTEGER) AS padded_integer,
  CAST('12.7' AS INTEGER) AS fraction_dropped,
  CAST('1,234.50' AS DECIMAL(10,2)) AS group_commas,
  CAST('1.5e3' AS FLOAT) AS exponent,
  CAST('$1,234.50' AS DECIMAL(10,2) FORMAT '$Z,ZZ9.99') AS currency,
  CAST('12%' AS DECIMAL(5,0) FORMAT 'ZZ9%') AS percent_sign,
  TO_NUMBER('1,234.50', '9,999.99') AS to_number_with_format,
  TO_NUMBER('12.5x') AS to_number_bad_input,
  CAST('' AS INTEGER) AS empty_string;
```

### date_cast — Convert text to a date

Without FORMAT, CAST reads `YYYY-MM-DD` and `YYYY/MM/DD`. It also reads `YY/MM/DD`: `'24/01/15'` becomes 1924-01-15 here. For any other layout name it in FORMAT (B is a blank, MMM a month abbreviation) or use TO_DATE.

```sql
-- A text column that holds ISO dates, checked against the DATE column it should match
SELECT TOP 10
  {{date_text_column}},
  CAST({{date_text_column}} AS DATE FORMAT 'YYYY-MM-DD') AS as_date,
  CASE WHEN CAST({{date_text_column}} AS DATE FORMAT 'YYYY-MM-DD') = {{date_column}}
       THEN 'same' ELSE 'different' END AS check_against_date_column
FROM {{database}}.{{table}}
ORDER BY order_id;

-- Other layouts
SELECT
  CAST('01/15/2024' AS DATE FORMAT 'MM/DD/YYYY') AS us_date,
  CAST('15.01.2024' AS DATE FORMAT 'DD.MM.YYYY') AS european_date,
  CAST('20240115' AS DATE FORMAT 'YYYYMMDD') AS compact_date,
  CAST('15-JAN-2024' AS DATE FORMAT 'DD-MMM-YYYY') AS month_abbreviation,
  TO_DATE('15-JAN-2024', 'DD-MON-YYYY') AS with_to_date;
```

### timestamp_cast — Convert text to a timestamp, and between date and timestamp

Without FORMAT, CAST reads `YYYY-MM-DD HH:MI:SS` with an optional fraction, `T` separator or zone offset; the seconds are required. In FORMAT, B is a blank and T the AM/PM marker. A DATE cast to TIMESTAMP gets the time 00:00:00.

```sql
SELECT
  CAST('2024-01-15T14:30:00' AS TIMESTAMP(0)) AS iso_timestamp,
  CAST('2024-01-15 14:30:00.123456' AS TIMESTAMP(6)) AS with_fraction,
  CAST('2024-01-15 14:30:00+02:00' AS TIMESTAMP(0) WITH TIME ZONE) AS with_zone,
  CAST('01/15/2024 14:30' AS TIMESTAMP(0) FORMAT 'MM/DD/YYYYBHH:MI') AS us_no_seconds,
  CAST('01/15/2024 02:30:05 PM' AS TIMESTAMP(0) FORMAT 'MM/DD/YYYYBHH:MI:SSBT') AS us_12_hour,
  TO_TIMESTAMP('01/15/2024 02:30 PM', 'MM/DD/YYYY HH:MI AM') AS with_to_timestamp;

SELECT TOP 5
  {{timestamp_column}},
  CAST({{timestamp_column}} AS DATE) AS ts_to_date,
  CAST({{timestamp_column}} AS TIME(0)) AS ts_to_time,
  CAST({{date_column}} AS TIMESTAMP(0)) AS date_to_ts
FROM {{database}}.{{table}}
ORDER BY order_id;
```

### try_cast — Find the rows that will not convert

TRYCAST returns NULL where CAST would raise an error. It takes only CHAR or VARCHAR input (Error 9134 otherwise) and no FORMAT phrase (Error 3706), so it reads the layouts a CAST without FORMAT reads.

```sql
SELECT
  TRYCAST('2024-02-30' AS DATE) AS impossible_date,
  TRYCAST('01/15/2024' AS DATE) AS wrong_layout,
  TRYCAST('12.5x' AS DECIMAL(10,2)) AS bad_number,
  TRYCAST(' 12.5 ' AS DECIMAL(10,2)) AS good_number;

-- How many values of a text column are not dates
SELECT
  COUNT(*) AS total_rows,
  SUM(CASE WHEN {{date_text_column}} IS NULL THEN 1 ELSE 0 END) AS null_text,
  SUM(CASE WHEN {{date_text_column}} IS NOT NULL AND TRYCAST({{date_text_column}} AS DATE) IS NULL
           THEN 1 ELSE 0 END) AS not_a_date
FROM {{database}}.{{table}};
```

### date_arithmetic — Differences between dates and between timestamps

DATE - DATE is the number of days as an INTEGER. `CAST(date AS INTEGER)` is not a day number: it is (year - 1900) * 10000 + month * 100 + day (2024-03-29 gives 1240329), so never subtract two such values. TIMESTAMP - TIMESTAMP needs an interval unit after the parentheses (Error 5407 without it), with a precision wide enough for the result (Error 7453 otherwise; the default is 2 digits, the maximum 4).

```sql
SELECT TOP 10
  {{date_column}},
  {{date_column}} - DATE '2024-01-01' AS days_since,
  ({{date_column}} - DATE '2024-01-01') MONTH(4) AS months_since,
  {{date_column}} + 30 AS plus_30_days,
  ADD_MONTHS({{date_column}}, 1) AS plus_1_month,
  CAST({{date_column}} AS INTEGER) AS internal_integer,
  ({{timestamp_column}} - TIMESTAMP '2024-01-01 00:00:00') DAY(4) TO SECOND AS elapsed,
  CAST((({{timestamp_column}} - TIMESTAMP '2024-01-01 00:00:00') DAY(4)) AS INTEGER) AS elapsed_days,
  {{timestamp_column}} + INTERVAL '90' MINUTE AS plus_90_minutes
FROM {{database}}.{{table}}
ORDER BY order_id;
```

### format_display — Dates and numbers as display text

`CAST(date AS VARCHAR(10))` follows the session date format (`24/01/15` here). `CAST(date AS VARCHAR(10) FORMAT '...')` is refused (Error 3617): apply the FORMAT to the date and cast the result to VARCHAR (E4 is the day name, M4 the month name), or use TO_CHAR. TO_CHAR pads a number to the width of its format, so TRIM it; the FM prefix removes the padding but also trailing zeros (973.80 gives `973.8`).

```sql
SELECT TOP 5
  CAST({{date_column}} AS VARCHAR(10)) AS session_format,
  CAST(CAST({{date_column}} AS FORMAT 'YYYY-MM-DD') AS VARCHAR(10)) AS iso_date,
  CAST(CAST({{date_column}} AS FORMAT 'MMMBDD,BYYYY') AS VARCHAR(20)) AS short_date,
  CAST(CAST({{date_column}} AS FORMAT 'E4,BM4BDD,BYYYY') AS VARCHAR(40)) AS long_date,
  TO_CHAR({{timestamp_column}}, 'DD.MM.YYYY HH24:MI') AS european_timestamp,
  CAST(CAST({{numeric_column}} AS FORMAT 'ZZZ,ZZZ,ZZ9.99') AS VARCHAR(20)) AS grouped_number,
  TRIM(CAST({{numeric_column}} AS FORMAT '$$$,$$$,$$9.99')) AS currency,
  TRIM(TO_CHAR({{numeric_column}}, '999,999,990.00')) AS with_to_char,
  TO_CHAR(order_id, 'FM00000000') AS zero_padded
FROM {{database}}.{{table}}
ORDER BY order_id;
```

### char_comparison — CHAR padding and comparisons across types

A comparison with `=` ignores trailing blanks, so a CHAR and a VARCHAR column join without a cast. LIKE does not ignore them, and CHAR_LENGTH of a CHAR value is its declared length. A number column compares with a numeric string (`order_id = '17'`); a character column compared with a number is converted value by value and fails on the first value that is not a number (Error 2621).

```sql
SELECT
  CASE WHEN 'abc' = 'abc   ' THEN 'equal' ELSE 'different' END AS trailing_blanks,
  CASE WHEN CAST('abc' AS CHAR(6)) = CAST('abc' AS VARCHAR(10)) THEN 'equal' ELSE 'different' END AS char_vs_varchar,
  CASE WHEN 'abc' LIKE CAST('abc' AS CHAR(6)) THEN 'match' ELSE 'no match' END AS like_with_padding,
  CHAR_LENGTH(CAST('abc' AS CHAR(6))) AS padded_length,
  CHAR_LENGTH(TRIM(CAST('abc' AS CHAR(6)))) AS trimmed_length;
```

### union_types — The first SELECT of a UNION sets the column type

Later SELECTs are converted to the type of the first, without a warning: a longer string is cut to the first length, a decimal under an INTEGER loses its fraction. Cast the first SELECT to the widest type. A number and a string in one position raise Error 3654; cast the number to VARCHAR.

```sql
-- Cut to CHAR(2): the city comes back as two characters
SELECT state AS v FROM {{database}}.customers WHERE customer_id = 1
UNION ALL
SELECT city FROM {{database}}.customers WHERE customer_id = 1;

-- Correct: the first SELECT carries the widest type
SELECT CAST(state AS VARCHAR(50)) AS v FROM {{database}}.customers WHERE customer_id = 1
UNION ALL
SELECT city FROM {{database}}.customers WHERE customer_id = 1;
```

### vector_table — A table with a vector column for embeddings

On this system (20.00.24.60) the vector type is the UDT `AIVector` in SYSUDTLIB; the type names VECTOR and Vector32 are refused (Error 3706). The column does not fix the number of elements.

```sql
CREATE MULTISET TABLE {{database}}.{{vector_table}} (
  id INTEGER NOT NULL,
  embedding AIVector
) UNIQUE PRIMARY INDEX (id);
```

### vector_insert — Insert vectors

A vector is written as comma-separated numbers, as a plain string or through the constructor. Brackets and non-numeric tokens are refused (Error 7548).

```sql
INSERT INTO {{database}}.{{vector_table}} (id, embedding)
VALUES (1, '-0.06944,0.080352,0.045963,0.006985,-0.000496');

INSERT INTO {{database}}.{{vector_table}} (id, embedding)
VALUES (2, NEW AIVector('0.5,0.4,0.3,0.2,0.1'));

-- Many rows from a query that builds the string
INSERT INTO {{database}}.{{vector_table}} (id, embedding)
SELECT product_id + 10, TRIM(price / 100) || ',' || TRIM(product_id / 40.0) || ',0.5,0.25,0.125'
FROM {{database}}.products;
```

### vector_read — Read vectors and check their length

```sql
SELECT TOP 5 id, CAST(embedding AS VARCHAR(32000)) AS embedding_text
FROM {{database}}.{{vector_table}}
ORDER BY id;

-- All rows should report the same number of elements
SELECT embedding.LENGTH() AS elements, COUNT(*) AS row_count
FROM {{database}}.{{vector_table}}
GROUP BY 1
ORDER BY row_count DESC;
```

## Common errors

- **3617 "FORMAT '...' does not match the datatype"** — a FORMAT phrase on a VARCHAR target. Write `CAST(CAST(d AS FORMAT 'YYYY-MM-DD') AS VARCHAR(10))` or `TO_CHAR(d, 'YYYY-MM-DD')`.
- **2665 "Invalid date"** — the text does not fit the layout (`CAST('01/15/2024' AS DATE)`) or is not a calendar date (`'2024-02-30'`). Name the layout in FORMAT; find the rows with TRYCAST.
- **6760 "Invalid timestamp"** — the same for timestamps, including text without seconds and no FORMAT.
- **2620 "The format or data contains a bad character"** — a string that is not a number cast to a numeric type. **2621** is the same inside a comparison of a character column with a number; compare with a string literal.
- **2616 / 2617 numeric overflow** — the value does not fit the target (`CAST(99999 AS SMALLINT)`). Widen the target.
- **5407 "Invalid operation for DateTime or Interval"** — TIMESTAMP - TIMESTAMP without a unit. Write `(ts1 - ts2) DAY(4) TO SECOND`.
- **7453 "Interval field overflow"** — the interval precision is too small: `(d1 - d2) MONTH` for 312 months, HOUR(4) for more than 9999 hours. Raise the precision or use a larger unit.
- **3731 "The user must use IS NULL or IS NOT NULL"** — `= NULL` in a predicate.
- **3654 "Corresponding select-list expressions are incompatible"** — a number and a string in the same UNION position.

## Practices

- Store dates as DATE and amounts as DECIMAL; convert text once, when loading, and count with TRYCAST what did not convert.
- Compare dates with `DATE '2024-01-15'` literals, not with strings whose reading depends on the session format.
- Cast one operand to DECIMAL before dividing two integers.
- A cast to a shorter VARCHAR or CHAR cuts the value without an error (`CAST('abcdef' AS VARCHAR(3))` gives `abc`); give the target enough length.
- In a UNION, cast the columns of the first SELECT to the widest type any branch needs.
- After loading vectors, group by `embedding.LENGTH()`; TD_VectorDistance refuses a table whose vectors differ in length (Error 9134).
- To cast several columns of a table at once, `TD_ConvertTo(ON t AS InputTable USING TargetColumns('c') TargetDataType('INTEGER'))` returns the table with those columns converted.
