# Vector normalization

TD_VectorNormalize: scale vectors to unit length or to fractions of their sum, what that changes for distance search and what it does not.

**Use for:** making Euclidean distance rank like cosine; storing embeddings at unit length once instead of at every query; turning count vectors into proportions.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the vectors | `retail_demo` |
| `table` | Table with an id and the vector components as numeric columns | `product_vectors` |
| `id_column` | Item id, carried through to the output | `product_id` |
| `feature_columns` | The vector components, as a column range or a list | `[e1:e6]` |
| `output_table` | Table that receives the normalized vectors | `product_vectors_unit` |
| `query_id` | Id of the item whose neighbours are wanted | `1` |

## Statements

### unit_vectors — Scale every vector to length 1

`IDColumns` are passed through; `TargetColumns` are replaced by their scaled values. The check computes the length of each result, which is 1.

```sql
SELECT n.{{id_column}},
  SQRT(n.e1**2 + n.e2**2 + n.e3**2 + n.e4**2 + n.e5**2 + n.e6**2) AS length_after
FROM TD_VectorNormalize(
  ON {{database}}.{{table}} AS InputTable PARTITION BY ANY
  USING
    IDColumns('{{id_column}}')
    TargetColumns('{{feature_columns}}')
    Approach('UNITVECTOR')
) AS n
ORDER BY n.{{id_column}};
```

### persist_unit_vectors — Normalize once and store the result

```sql
CREATE MULTISET TABLE {{database}}.{{output_table}} AS (
  SELECT * FROM TD_VectorNormalize(
    ON {{database}}.{{table}} AS InputTable PARTITION BY ANY
    USING
      IDColumns('{{id_column}}')
      TargetColumns('{{feature_columns}}')
      Approach('UNITVECTOR')
  ) AS n
) WITH DATA
UNIQUE PRIMARY INDEX ({{id_column}});

SELECT COUNT(*) AS vectors_stored FROM {{database}}.{{output_table}};
```

### what_changes — Cosine is unchanged, Euclidean now ranks like cosine

Cosine distance ignores length, so normalizing does not change it. Euclidean distance on unit vectors equals √(2 × cosine distance), so it ranks neighbours exactly as cosine does — on the raw example vectors it ranks a different item third.

```sql
SELECT distancetype, reference_id, distance
FROM TD_VectorDistance(
  ON (SELECT * FROM TD_VectorNormalize(
        ON {{database}}.{{table}} AS InputTable PARTITION BY ANY
        USING IDColumns('{{id_column}}') TargetColumns('{{feature_columns}}') Approach('UNITVECTOR')) AS q
      WHERE {{id_column}} = {{query_id}}) AS TargetTable PARTITION BY ANY
  ON (SELECT * FROM TD_VectorNormalize(
        ON {{database}}.{{table}} AS InputTable PARTITION BY ANY
        USING IDColumns('{{id_column}}') TargetColumns('{{feature_columns}}') Approach('UNITVECTOR')) AS c) AS ReferenceTable DIMENSION
  USING
    TargetIDColumn('{{id_column}}')
    TargetFeatureColumns('{{feature_columns}}')
    RefIDColumn('{{id_column}}')
    RefFeatureColumns('{{feature_columns}}')
    DistanceMeasure('Cosine', 'Euclidean')
    TopK(3)
) AS t
ORDER BY distancetype, distance;
```

### fractions — Each component as a share of the vector's sum

`Approach('FRACTION')` divides every component by the sum of the components, so they add up to 1. Meant for non-negative vectors such as counts; with negative components the "shares" can be negative or larger than 1.

```sql
SELECT n.{{id_column}}, n.e1, n.e5,
  n.e1 + n.e2 + n.e3 + n.e4 + n.e5 + n.e6 AS sum_after
FROM TD_VectorNormalize(
  ON {{database}}.{{table}} AS InputTable PARTITION BY ANY
  USING
    IDColumns('{{id_column}}')
    TargetColumns('{{feature_columns}}')
    Approach('FRACTION')
) AS n
ORDER BY n.{{id_column}};
```

## Common errors

- **Normalizing to fix cosine results** — Cosine distance does not depend on vector length; normalizing changes nothing for it. Fix: look elsewhere (the input columns, the target/reference roles).
- **Euclidean neighbours that disagree with cosine** — The vectors have different lengths. Fix: normalize to unit length first.
- **Fractions outside 0..1** — `FRACTION` on vectors with negative components. Fix: use `UNITVECTOR` for embeddings.

## Practices

- Normalize stored embeddings once (persist_unit_vectors) and normalize each query vector the same way at search time.
- Keep the original vectors if their length carries meaning (for example a count or a magnitude).
