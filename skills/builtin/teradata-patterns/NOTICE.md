# Notice

The sections under `library/` are derived from the Teradata patterns of **Loom**
(https://github.com/teradata-labs/loom, directory `patterns/teradata/`),
Copyright 2026 Teradata, licensed under the Apache License, Version 2.0. A copy of that
licence is in `LICENSE-APACHE-2.0.txt`.

`sources.json` names, for every section, the upstream file it started from, the upstream
commit and the SHA-256 of that file.

## Changes made to the upstream material

The sections are not copies. Each upstream pattern was converted from YAML to a Markdown
section and then rewritten so that every SQL statement executes on a live Teradata system:

- statements the database refused were corrected (for example `SELECT *, expr`, `LIMIT`,
  reserved words used as aliases, columns that do not exist in the `DBC` views);
- placeholders that were used but never declared were declared, with example values;
- statements that cannot run on the verification system were removed;
- `analytics/customer_health_scoring` read tables of a specific organisation and was rebuilt
  as a general score on the same idea; `analytics/churn_analysis` assumed a ready-made churn
  column and now derives churn from order history;
- statements and explanatory text were shortened, and claims that the database
  contradicted were corrected or removed.
- the geospatial, hypothesis-testing and vector-search sections were rewritten against small
  fixture tables (`stores`, `regions`, `product_vectors`) instead of the upstream example
  schemas; the geospatial sections use Teradata's WGS 84 SRID (1619) instead of 4326, and
  statements that need an absent product (the `AI_*` functions, `TD_HNSW*`) were not taken.

`verification.json` records, per section, the Teradata version the statements were executed
on, the date, and the SHA-256 of each executed statement block.
`scripts/teradata_patterns.py` in the repository root is the tool that imports, executes and
indexes the sections.
