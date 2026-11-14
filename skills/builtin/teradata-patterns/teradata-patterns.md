# Teradata patterns — a library of executed statements, read on demand

You have a library of Teradata SQL patterns. It is not in your context: this page lists the
sections, and you read a section with `TDA_SkillLibrary(action='read', anchor='<anchor>')`
when the task needs it. Every SQL statement in the library was executed on a live Teradata
before it was included, so prefer a section's form over a form you remember.

## When to read a section

- Before you write a statement of a kind listed below and you are not certain of the
  Teradata form (a cast with a FORMAT phrase, a query on a `DBC` view, a deduplication, a
  percentile, a statistics collection).
- After the database refuses a statement: read the section for that kind of statement
  before you try again.
- Read the one or two sections the task needs. Do not read the library through.

## How to use a section

- A section holds statements with `{{placeholders}}` and a table that explains each one.
  Replace every placeholder with the real database, table and column names of the user's
  system, taken from the schema you were given or from the catalog.
- The example values (`retail_demo`, `customers`, `orders`, `events`) belong to the test
  data the statements were executed on. They do not exist on the user's system; never
  query them.
- Run the adapted statement through your normal data tool. The library gives you the
  form; it does not run anything.
- A statement that ran on the verification system can still be refused on another one: a
  `DBC` view may need a privilege the login lacks, query logging may be off, a function
  may not be installed. Report the database's error as it is; do not present the pattern
  as a result.
- Statements that create, change or drop objects are in the library because the task
  sometimes needs them. Run one only when the user asked for that change.

## Sections

Each line names a section. When the title is not enough to choose, call
`TDA_SkillLibrary(action='list_sections')` for a one-line description of every section.

<!-- index:begin -->
**analytics**
- `analytics/attribution` — Attribution analysis
- `analytics/churn_analysis` — Customer churn analysis
- `analytics/customer_health_scoring` — Customer health scoring
- `analytics/funnel_analysis` — Funnel analysis
- `analytics/npath` — nPath sequence analysis
- `analytics/sessionize` — Sessionize: group events into sessions

**core**
- `core/data_types_casting` — Teradata data types and casting
- `core/sql_basics` — Teradata SQL basics and dialect differences

**data_quality**
- `data_quality/data_profiling` — Data profiling
- `data_quality/data_validation` — Data validation rules
- `data_quality/duplicate_detection` — Duplicate detection and deduplication
- `data_quality/missing_value_analysis` — Missing value analysis and imputation
- `data_quality/outlier_detection` — Outlier detection

**geospatial**
- `geospatial/geometry_basics` — ST_Geometry basics
- `geospatial/spatial_operations` — Spatial operations
- `geospatial/spatial_relationships` — Spatial relationships

**hypothesis_testing**
- `hypothesis_testing/anova` — One-way ANOVA
- `hypothesis_testing/statistical_tests` — Statistical tests

**performance**
- `performance/catalog_views` — Teradata catalog views (DBC)
- `performance/explain_analysis` — EXPLAIN plan analysis
- `performance/pi_skew_detection` — Primary index skew detection
- `performance/query_tuning` — Query tuning
- `performance/spool_space_analysis` — Spool space analysis
- `performance/statistics_collection` — Statistics collection

**timeseries**
- `timeseries/arima` — ARIMA forecasting
- `timeseries/holt_winters` — Holt-Winters forecasting
- `timeseries/moving_average` — Moving average
- `timeseries/signal_processing` — Signal processing with UAF functions
- `timeseries/uaf_concepts` — UAF call syntax

**vector_search**
- `vector_search/vector_distance` — Vector distance search
- `vector_search/vector_normalize` — Vector normalization
<!-- index:end -->
