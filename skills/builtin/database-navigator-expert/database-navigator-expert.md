# Database Navigator Expert

The navigator does the mechanical work: it reads the dictionary, ranks search hits, infers roles and keys, flags columns, masks personal data and writes a sample query. Your job is **judgement**: when to look, how to read what it found, and how to turn it into a correct, small query.

---

## When to look

- **Before your first SQL against a table this session.** A guessed column name costs a failed round trip; a `describe` costs nothing.
- **When a name is uncertain.** `search` with one identifier fragment (`customer`, `incdate`, `order_line`) — never a whole question. Read the ranked hits; an exact name beats a prefix beats a substring.
- **When you need a join.** `suggest_joins` first. A *declared* key is a fact; an *inferred* one is a hypothesis with its reason and confidence — say so if you use it.
- **Not** to explore for its own sake. Describe the tables the question needs and stop.

---

## Reading a description

| What it says | What it means for your query |
|---|---|
| role **fact** | one row per event; aggregate its measures, group by its keys or dates |
| role **dimension** | descriptive rows referenced by facts; join to it for names and categories |
| role **bridge** | keys only; it links two tables — never aggregate it on its own |
| role **lookup** | code + description; join it to decode a `code` column |
| role **log / staging** | operational or transient; prefer the governed table unless the user asks |
| `row_count · statistics` | approximate and dated; use `count` for an exact figure only when it matters |
| `key` / `identifier` flag | group and join on these; `identifier` without `key` is a name pattern, not a declared key |
| `measure` flag | numeric and summable; check the name before summing a rate or a percentage |
| `code` flag | short coded value; ask the lookup (or the ontology's decoded values) before filtering on a literal |
| `text_date` flag | a date stored as text — compare and order it as text or CAST it with the sampled FORMAT; string order is not date order |
| `pii` flag | personal data; never select it unless the user asked for it, and never try to unmask a preview |
| `unavailable: …` | this source cannot supply that fact; do not infer it |

---

## Writing the query

1. Start from the **sample query** the description gives you and keep only the columns you need.
2. Join on **declared** keys with every key column (a composite key joined on one column multiplies rows).
3. Filter coded columns with the code, not the label, unless a lookup or decoded values are on hand.
4. Prefer the table the ontology describes (the description carries its business meaning) over a look-alike in a staging database.
5. Keep the first query small; widen only once the shape of the data is confirmed.

[[gate:navigator_query]]---

## Running your own query (`query`)

You may run one read-only SELECT per call against the databases this agent is allowed to query. You read at most a few hundred rows, so let the database do the work.

| Do | Why |
|---|---|
| Describe the tables first, then query with the exact column names | a guessed name costs a failed call and a correction round |
| Aggregate in the statement — `COUNT`, `SUM`, `AVG`, `GROUP BY` | adding rows up yourself is slow and error-prone; the database is exact |
| For "top N", sort and limit in SQL | returning everything to find the maximum wastes the row budget |
| Write for the source's dialect (the result names it) | `LIMIT` vs `TOP`, date functions and quoting differ between databases |
| When the result says it was bounded, narrow or aggregate and ask again | a total or an average over a truncated result is wrong |
| Query each database separately and combine the results in your answer | one statement reaches one source; there is no cross-database join |
| Say which source (and statement) a figure came from | the result carries both; they make the figure checkable |

When a query is refused, read the reason: `not_read_only` means the statement writes — rewrite it as a SELECT; `source_not_allowed` lists the sources you may use. Do not resend a refused statement unchanged.

You read personal data unmasked so you can answer; do not repeat it in your reply unless the user asked for it.

### On a MongoDB source

A MongoDB source takes `collection` and `pipeline` instead of `sql`. The same rules apply in its own terms:

| Do | Why |
|---|---|
| Describe the collection first | there is no declared schema — describe lists the fields sampled from its documents, with how often each appears |
| Filter early with `$match`, then `$group` / `$sort` / `$limit` | the database does the counting; you read a bounded number of documents |
| Reach nested fields with dot paths (`"address.city"`) and arrays with `$unwind` | a nested value is not a separate table |
| Join collections with `$lookup` | there is no SQL join; one pipeline may read several collections of the same database |

`writable_login` means the connection's login can write and its owner has not allowed agent queries on it — tell the user; do not retry.

[[/gate]]---

## Anti-patterns

| Don't | Why |
|---|---|
| Guess a column from a table name | the dictionary is one call away and exact |
| Join two tables because they share `id` | generic names collide; use a declared key or an inferred join with a stated reason |
| Sum a column just because it is numeric | keys and codes are numeric too; the `measure` flag is the signal |
| Run `COUNT(*)` on every large table you meet | the statistics row count answers most questions at no cost |
| Read a masked value as the real value | `J•••` is a mask, not data |
