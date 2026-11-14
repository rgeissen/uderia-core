# Vector distance search

TD_VectorDistance: nearest neighbours by cosine, Euclidean or Manhattan distance — which input is the target, what TopK limits.

**Use for:** finding the items most similar to a given item or query vector; similarity search over embeddings stored as numeric columns; pairwise nearest neighbours within one table; comparing distance measures.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the vectors | `retail_demo` |
| `table` | Table with one row per item: an id and the vector components as numeric columns | `product_vectors` |
| `id_column` | Item id | `product_id` |
| `feature_columns` | The vector components, as a column range or a list (`'e1','e2',...`) | `[e1:e6]` |
| `query_id` | Id of the item whose neighbours are wanted | `1` |
| `top_k` | Neighbours to keep per target row | `5` |

## Statements

### nearest_to_one_item — The k items closest to a query

TopK keeps the k nearest REFERENCE rows for each TARGET row. Put the query in `TargetTable` and the catalogue in `ReferenceTable`. The other way round — catalogue as target, query as reference — every target row has exactly one reference, so TopK limits nothing and all rows come back with their distance to the query. The query item itself is returned at distance 0. Lower distance is more similar.

```sql
SELECT reference_id AS similar_{{id_column}}, distance
FROM TD_VectorDistance(
  ON (SELECT * FROM {{database}}.{{table}} WHERE {{id_column}} = {{query_id}}) AS TargetTable PARTITION BY ANY
  ON {{database}}.{{table}} AS ReferenceTable DIMENSION
  USING
    TargetIDColumn('{{id_column}}')
    TargetFeatureColumns('{{feature_columns}}')
    RefIDColumn('{{id_column}}')
    RefFeatureColumns('{{feature_columns}}')
    DistanceMeasure('Cosine')
    TopK({{top_k}})
) AS t
WHERE reference_id <> {{query_id}}
ORDER BY distance;
```

### nearest_with_attributes — Neighbours joined back to their attributes

```sql
SELECT t.reference_id AS {{id_column}}, v.category, CAST(t.distance AS DECIMAL(10,4)) AS cosine_distance
FROM TD_VectorDistance(
  ON (SELECT * FROM {{database}}.{{table}} WHERE {{id_column}} = {{query_id}}) AS TargetTable PARTITION BY ANY
  ON {{database}}.{{table}} AS ReferenceTable DIMENSION
  USING
    TargetIDColumn('{{id_column}}')
    TargetFeatureColumns('{{feature_columns}}')
    RefIDColumn('{{id_column}}')
    RefFeatureColumns('{{feature_columns}}')
    DistanceMeasure('Cosine')
    TopK({{top_k}})
) AS t
JOIN {{database}}.{{table}} AS v ON v.{{id_column}} = t.reference_id
ORDER BY t.distance;
```

### all_nearest_neighbours — Every item's nearest neighbours within one table

Without a `ReferenceTable` the table is compared with itself; each item's list includes the item itself at distance 0, so ask for k + 1 and remove it. Cost grows with the square of the row count.

```sql
SELECT target_id AS {{id_column}}, reference_id AS neighbour_id, distance
FROM TD_VectorDistance(
  ON {{database}}.{{table}} AS TargetTable PARTITION BY ANY
  USING
    TargetIDColumn('{{id_column}}')
    TargetFeatureColumns('{{feature_columns}}')
    DistanceMeasure('Cosine')
    TopK(2)
) AS t
WHERE target_id <> reference_id
ORDER BY target_id;
```

### compare_measures — Cosine, Euclidean and Manhattan in one call

One row per measure and neighbour; TopK applies per measure. Cosine ignores vector length; Euclidean and Manhattan do not, so they rank differently on raw vectors.

```sql
SELECT distancetype, reference_id, distance
FROM TD_VectorDistance(
  ON (SELECT * FROM {{database}}.{{table}} WHERE {{id_column}} = {{query_id}}) AS TargetTable PARTITION BY ANY
  ON {{database}}.{{table}} AS ReferenceTable DIMENSION
  USING
    TargetIDColumn('{{id_column}}')
    TargetFeatureColumns('{{feature_columns}}')
    RefIDColumn('{{id_column}}')
    RefFeatureColumns('{{feature_columns}}')
    DistanceMeasure('Cosine', 'Euclidean', 'Manhattan')
    TopK(3)
) AS t
ORDER BY distancetype, distance;
```

### cosine_by_hand — The same cosine distance in plain SQL

Cosine distance = 1 − (a·b) / (|a| |b|). On the example data the five smallest values and their order match `nearest_to_one_item`.

```sql
SELECT TOP {{top_k}} b.{{id_column}},
  1 - (a.e1*b.e1 + a.e2*b.e2 + a.e3*b.e3 + a.e4*b.e4 + a.e5*b.e5 + a.e6*b.e6)
      / (SQRT(a.e1**2 + a.e2**2 + a.e3**2 + a.e4**2 + a.e5**2 + a.e6**2)
         * SQRT(b.e1**2 + b.e2**2 + b.e3**2 + b.e4**2 + b.e5**2 + b.e6**2)) AS cosine_distance
FROM {{database}}.{{table}} AS a, {{database}}.{{table}} AS b
WHERE a.{{id_column}} = {{query_id}}
ORDER BY 2;
```

## Common errors

- **TopK returns every row** — The query is in `ReferenceTable` and the catalogue in `TargetTable`; TopK limits references per target. Fix: swap the two inputs.
- **The query item is its own best match** — The reference set contains the query. Fix: filter `reference_id <> query_id` (or `target_id <> reference_id` for the self-comparison).
- **Error 3706 Data Type "VECTOR" does not match a Defined Type name** — This system has no VECTOR column type. Fix: store the components as numeric columns and name them in `TargetFeatureColumns`.
- **Different rankings for Euclidean and cosine** — Expected on vectors of different lengths. Fix: normalize to unit length first (vector_normalize) if Euclidean should rank like cosine.

## Practices

- Name the same feature columns, in the same order, for target and reference.
- Keep TopK small: the self-comparison form computes every pair.
- Check one result with the plain-SQL formula when adopting a new measure or input.
