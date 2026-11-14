# Knowledge Graph Inspector — World-Class Large-Graph Visualization

> **Ontology note (2026-06-27):** KG types were renamed — logical `entity`/`attribute`, physical `table`/`column` (was logical `table`/`column`, physical `physical_table`/`physical_column`). References below to the old names describe the as-built milestone; the live contract is [ONTOLOGY_ARCHITECTURE.md §2.3](ONTOLOGY_ARCHITECTURE.md) + `KG_MODEL_RENAME.md`.

**Status:** Implemented (Stages 1–4 + execution animation + execution theatre) · **Date:** 2026-06-23 · **Last updated:** 2026-10-30

How the Uderia KG inspector renders graphs from a handful of nodes to 25,000+ without
becoming an unreadable hairball — and how the live execution animation works on top.
The renderer (`components/builtin/knowledge_graph/renderer.js`) is shared by the
**inspect view**, the **component split panel**, and inline cards, so everything here
works in all three (the execution animation is a component prerequisite).

## What we learned from leading vendors

| Vendor | Approach | Technique adopted |
|---|---|---|
| Cambridge Intelligence (KeyLines/ReGraph) | the **data funnel** — reduce 1M→~100 before rendering | progressive disclosure; force layout is legible only ~100–150 nodes |
| Neo4j Bloom | scene-based, expand-from-seed | search-first entry; seed + expand; node grouping |
| Linkurious Ogma / Graphistry / Cosmograph | WebGL/GPU render whole graphs (8M / 1M) | the *other* school — macro pattern-spotting; not our path (we're D3/SVG) |

**The decisive insight:** force-directed layout is only legible at ~100–150 nodes, so
the goal is **reduce-then-render** (the funnel), not "render more nodes faster." A
25k-node force dump is the failure mode. We are firmly in the reduce-then-render school.

## The three lenses (progressive disclosure)

### 1. Detail lens — budgeted, type-stratified, dependency-coherent
The default. A bounded sample driven by one engine: **seed → connectivity-expand → fill**
(`GraphStore.get_typed_subgraph(types, max_nodes, focus)`, SQLite + Neo4j):

1. **Seed** — `focus` entity ids (from search/click/zoom/execution) seed the budget;
   else a type-stratified set of the highest-degree nodes per selected type (every type
   represented — fixes "missing types").
2. **Expand** — fill the rest of the budget by BFS outward from the seed, adding the
   nodes most connected to what's already visible → a **connected** subgraph that follows
   real dependencies, not scattered hubs (fixes the hairball).
3. **Fill** — top up any unused budget (a small type's unspent share, disconnected parts)
   with highest-degree leftovers, so the budget is **honoured** (fixes "700 budget but
   only 489 shown").

Controls (toolbar):
- **Type pills** from the FULL catalog (`spec.entity_type_counts`, every type + shown/total
  counts) — toggling **re-queries** a stratified subgraph (server-side), not a client hide.
- **Node-budget slider** (50–2000) — re-queries at the new budget. Force is legible ~150.
- **Honest header** — "showing N of M nodes".

### 2. Overview lens — the schema meta-graph (always complete)
`GraphStore.get_schema_overview()` → one node per entity type (sized by count) + aggregated
type→type relationships (`table —contains→ column`, `metric —measures→ table`, …). Tiny and
**complete regardless of graph size** — the real "see every type and how they connect"
answer. Toolbar "Schema" button; click a type to drill into the detail view filtered to it.

### 3. Focus lens — seed + expand on demand
- **Search Enter** → find across the WHOLE KG: frames in-view matches, else re-queries a
  focus neighbourhood for the searched entity (even outside the sample).
The interaction model is three distinct, conventional gestures (one mechanism each):
- **Hover a node** → *peek*: **neighbour highlight** (the node + its DIRECT in-view neighbours
  bright, connecting edges brighter, everything else dimmed — generic across all types) plus
  its derived dependencies, **transient** (clears on mouse-out). A transient peek does NOT
  pull out-of-budget neighbours in (that would grow the graph on every mouse-move). Yields to
  an active selection.
- **Single-click** → *select / inspect*: focus highlight + a persistent **focus ring**, and
  reveals the node's **derived dependency edges** (the dashed lifted FKs — hover one for the
  join columns). Purely in-place, **no graph growth**. Click it again / the background to
  deselect. A 240 ms timer disambiguates from double-click.
- **Double-click** → *expand / drill in*: select the node, then **pull its COMPLETE real
  neighbourhood into the graph** as nodes — see *Complete-neighbourhood reveal* below.

#### Complete-neighbourhood reveal (consistency guarantee)
A focus/expand gesture must never show a budget-truncated subset of a node's relationships.
So **double-click** calls `_revealComplete(entityId)` → `GET .../node-neighborhood` (the
node's full 1-hop set, uncapped) → `_mergeNeighborhood()` **adds any missing neighbours into
the LIVE graph in place**: append-only (new nodes/links at the array tail → d3 `enter()` =
exactly them), existing nodes **pinned** (frozen `fx/fy`) so only the new ones grow out from
the focus, the viewport never jumps, then pins released after settle. No re-render, no
refocus. It only grows when there are genuinely hidden neighbours (already-complete nodes →
no-op). The node/link **builders and handler-wiring are shared** with the initial render
(`_decorateNodeEnter` / `_decorateLinkEnter` / `_wireNodeHandlers` / per-link `_ensureGradient`),
so merged nodes are first-class (identical visuals + hover/click/drag/tooltip).
- **Zoom** → **pure pan/scale by default**. It never re-queries or re-lays-out the graph
  unless **Smart Autozoom** is on (toolbar toggle, off by default).

#### Smart Autozoom (opt-in — viewport-driven level-of-detail)
A toolbar toggle. When **on**, a debounced zoom-end handler re-allocates the node budget to
the area you're looking at: it takes the currently-visible nodes (screen-projected within the
viewport) as the `focus` and re-queries (`zoom.auto` → `_activeRequery`). **Zoom in** → smaller
visible set → the budget densifies that region (more detail); **zoom out** → larger visible
set → the budget spreads (broader overview). It reads as the area filling in, not a refocus:
the viewport is preserved (`_pendingAutoZoom` re-applies the zoom transform after the render),
the visible nodes carry their positions **keyed by `entity_id`** (a focus re-query returns a
different node set, so index-keyed carry would misplace everything) and are pinned as anchors,
new detail is seeded near the visible centroid, and the centring force is dropped so the layout
stays put. Debounced (420 ms) + in-flight-guarded; programmatic zoom is ignored (no loop);
focus seeds capped at 80 (URL safety) — the sampler expands from them to fill budget. Off =
the default pure pan/scale.

All three feed the **same** `focus` parameter → one engine.

### Relationship projection (link aggregation)
When a type filter hides the nodes a relationship actually runs through, the edge
would silently vanish — e.g. with only `table` selected, the FK dependencies (which
live at `column —foreign_key→ column`) disappear, leaving the tables looking
unrelated. Best practice (KeyLines "aggregate links", Bloom relationship aggregation)
is to **lift each hidden endpoint to its nearest VISIBLE ancestor** via the containment
chain (`*0..6 contains`), aggregate the parallels, and render the result as a **derived**
edge. So:
- `table` only → `column↔column` FKs project to a **derived `table↔table` foreign_key**.
- `domain` only → table/column FKs project to **`domain↔domain`**.
- General across **all** types (`business_concept`, `metric`, `column`, `domain`, `table`)
  — the lift is purely structural (the `contains` hierarchy), not type-specific.

Implementation: `_derive_lifted_edges` (SQLite, recursive multi-level lift with a cycle
guard) / a variable-length `[:KG_RELATIONSHIP*0..6 {relationship_type:'contains'}]`
projection query (Neo4j). Derived edges carry `metadata.derived=True` + an aggregation
`count`. The endpoint passes `derived`/`count` through on each link; the renderer draws
them **dashed** (width scales with `log10(count)`) and labels them `foreign_key ×N`.

**Details-on-demand (gated OFF by default).** The projection is compute-heavy (it walks
the containment chain for every cross-level edge) and render-heavy (every derived edge is
another link), so it is **not** part of the resting view, and revealing it must never
re-lay-out the graph. It is computed server-side via `project_dependencies` (backend) /
`?project=1` (endpoint) and surfaced **in place, edges-only** (no re-query, no re-layout)
two ways:
- **Hover a node** — reveals THAT node's dependencies transiently. The lifted-edge set is
  fetched **lazily once per view** and cached client-side (`_ensureDerived`, pre-warmed when
  a type filter is active so the first hover is instant); hovering filters the cache to the
  edges touching the node and draws them as dashed lines on the current node positions
  (`_drawDerived`/`_clearDerived` on a `kg-derived-layer` group, repositioned via a
  `tick.derived` listener). Mouseleave clears them.
- **Manual toggle** — a dependency-overlay button in the toolbar (shown under a type
  filter) draws/clears **all** cached derived edges persistently, in place. No re-query.

This replaced an earlier zoom-threshold auto-toggle (`zoom.deps`) and a semantic-zoom
node-set re-focus (`zoom.focus`): both re-rendered the graph and read as a jarring
"refocus" on zoom-in. **Zoom is now pure pan/scale** — it never re-queries or re-lays-out.

### Key-constraint styling (PK / FK)
Key relationships are visually distinct from regular structural edges:
- **FK edges** (`column —foreign_key→ column`, `table —foreign_key→ table`) render **solid
  amber** (`#f59e0b`), thicker, vs the neutral node-type gradient used by `contains` and other
  structural edges. Derived/lifted FK edges stay dashed amber-orange (same key colour family).
- **PK is a column property, not an edge** (a primary key is intrinsic to one column, not a
  relationship). It's surfaced as a **gold `PK` badge** (top-left) on the column node;
  **FK columns** get an **amber `FK` badge** (top-right). Both appear in the node tooltip and
  a key-relationship legend (shown when columns are in view).

## Execution animation (trace-driven focus)

Live query execution highlights the KG entities it touches. World-class behaviour:

- **In-view entities** glow immediately (amber activity ring), ordered by event arrival.
- **Off-view entities** (the old silent failure on sampled graphs) are accumulated as the
  running **focus**; a focus neighbourhood is pulled from the server and the touched nodes
  are **replayed in order** (staggered activation = reads as a traversal path). The focus
  re-query **transcends the active type filter** (`types=null`) — the execution trace is
  ground truth, so a touched entity of a filtered-out type (a column touched while the user
  filtered to `table`) must still come into view, never be silently dropped.
- **Focus accumulates** across the run, so the whole touched path stays in view with context.
- **Zero added latency** — the animation is a fire-and-forget browser overlay (execution is
  server-side and never waits on it); the focus re-query is **throttled (≥700 ms) +
  in-flight-guarded + debounced**, so rapid events can't churn the force layout or block
  client event processing. In-view glows stay instant.
- **Component-compatible** — `renderKGFull`/`_graphState` is the shared path, so the
  animation works identically in the split-panel component and the inspect view.

API: `dispatchKGAnimation(eventType, payload)` (from `eventHandlers.js` processStream),
`endKGExecutionAnimations()`. `renderKGFull` publishes `_activeRequery` module-side so the
animation reaches the re-query in any render path. The same `dispatchKGAnimation` also
handles the `grounding_gate` event type (below) — a distinct branch that draws the trust
overlay rather than animating touched nodes.

## Execution theatre (Sep 2026) — the graph explains itself while the query runs

The legacy execution animation glowed the entities a tool call *named*; it said nothing about
what the context engine did or what it was worth. The **execution theatre** replaces it (opt-in:
`localStorage.kg_theatre === '1'`, the mask toggle in the KG panel header; default OFF keeps the
inspector byte-identical) with a live, zero-token explanation told in the model's own vocabulary
— entities, relationships (cardinality, keys) and attributes (key · filter · dimension · measure ·
projected · absent) — on the **Policing Core Model** or whatever model the agent is grounded on.

**Seven beats, ONE reducer for live and reload.** `static/js/handlers/kgTheatre.js` exports a
pure `createReducer()` that folds `kg_stage` notices (understand → seed → expand → inject, emitted
by `core/kg_stage.py` through the per-user notification channel from the enrichment's own walk),
the `kg_enrichment` event (which also carries `_theatre_facts`: seed reasons, provenance, entity
and attribute facts, relationships with their key columns), `tool_intent`/`tool_result` (roles
read from the SQL by `sqlRoles`), `grounding_gate` and `honesty` into one state; `eventsFromTurn`
rebuilds the same event stream from a persisted turn, so **Replay and a plan reload render the
identical story**. When no live stage arrived (an older backend), the four stages are synthesized
from the enrichment and marked `synthetic` in the Inspect rail.

**The value layer, not the activity layer.** Every consequence is read from the trace, never
inferred: *applied* facts (declared key used, a code compared as a string, a decoded label, a
text-date shape), *prevented* statements (a preflight refusal: cross-area join, wrong key, text
date, value, schema — the refused statement's applied facts are retracted), *skipped* and *wasted*
(the graph went unused). The receipt names them in words. The Watch caption follows a relevance
rule, worst first: prevented > block > out-of-scope > honesty > receipt > latest applied > stage.

**Onion.** *Watch* (the consumer default) is one lit path, one caption and one animating mark;
*Inspect* adds the reasoning trail with timings, attribute types, `IMPLEMENTS` shadows and the
facts line. Depth persists (`kg_theatre_depth`).

**The reasoning trail reads from the STAGE, never the reducer.** The reducer runs ahead of the
stage whenever ops are queued — a replay or a reload reduces the whole turn at once, and the
catch-up after a release plays it back — so a trail that read the reducer flipped every row to
*done*, with its final facts, the moment Replay was clicked. ONE view per beat (`beatView`, also
exposed on the api for the gate) drives the trail, the ribbon and the explanation: *known* (the
reducer reduced it), *done* (the stage performed it — or, while held, the whole known turn: a hold
is the story so far and never turns a finished beat back into pending), *queued* ("up next": known,
not yet performed — no facts, no seconds), *absent* ("did not run this turn": not in the turn and
the receipt has landed — never "waiting"), *ahead* (after the hold, dimmed), *current* (the stage's
beat). A beat's facts appear only once the stage performed it; a statement sub-row likewise. Any
known beat is clickable — a done one holds the story there, a queued one jumps to it.

**One clock.** The trail's seconds are the moment each beat reached the stage on the viewer's
clock; the wire's `ts` (the server's clock) is no longer mixed in for the beats that carry none.
A replay or a scrub of the same turn KEEPS the live run's clock (`keepClock`/`restoreClock`) —
re-stamping at the burst read "0.00 s" on every row — and a reloaded turn, which has no clock,
shows no seconds at all (`timesCoherent`: every beat inside a quarter second is an arrival, not
a timing). The beat on stage reads *now*, a live word.

**Hover previews, click holds, leaving returns — ONE mechanism.** A hover used to lay a *spotlight*
(what the beat touched) over whatever frame was on stage while a click *rewound* the stage to the
beat: two answers to two questions, and since the cursor sits on the row it clicks, the picture a
click gave was the held frame PLUS the spotlight — it changed the moment the mouse left the row.
Now `preview(beat)` shows exactly the frame a click would hold (through the same `scrubTo`), the
trail keeps the real hold marked (`held`) beside the previewed row (`preview`, a dotted bar), the
caption reads *preview — click to hold*, and `unpreview()` restores the held frame or the finished
story. Hovering the held beat is a no-op, so selecting and hovering the same beat look identical.
The two things the spotlight showed that the frame lacked moved INTO the frames: Understand's
frame carries its term words forward (`kgt-q-focus`), Verify's the perimeter (`kgt-v-focus`) —
live, on replay and when held. A live turn still playing is never previewed. The Seed frame lights
the entity an attribute seed belongs to (the box its thread lands on), and **the lit box says why**:
its seeded attributes lead its rows (role `seed`, the matching words as the detail), ahead of its
key — a box lit by `offence_code` shows `offence_code`, not only its key. **A word draws one thread
for every seed it reached** (it once drew only its first match, so a word that lit three boxes showed
one thread and the frame could not explain its own lighting); one label per reached attribute names
every word that reached it (`robberies, robbery → offence_code`).

**The SQL reader** (`sqlRoles`, pure) reads table-qualified columns AND: a bare column, placed on the
one table of the statement the graph says carries it (attributes, key or foreign key; two candidates
⇒ left unplaced, never guessed); a `GROUP BY` on a select alias, followed to the alias's expression
(`SUBSTR(incident_date_text, 4, 3) AS month_text … GROUP BY month_text` groups by
`incident_date_text`, credited as the *date shape* fact); and a filter on a function of a column
(`SUBSTR(…, 8, 4) = '2023'`). A table with no alias directly before `JOIN` no longer swallows the
join. **The grain** is the measure's own table, or — for `COUNT(*)` over a join — the finest table,
the one that is not the 1-side of a 1:N to another table of the statement (Incident ⋈ Offence counts
offence rows).

**From Execute on, the frame's subject is the statement.** Inject's frame shows the context as
the agent received it — every injected entity solid. Once a statement has run (`ui.executing`),
the entities it reads (`ui.used`, accumulated across statements) and the joins it takes stay
forward while the injected context it does not read recedes to a *background* reading (entities
0.45, unused relationships 0.3) — live, on replay and when Execute, Verify or the Receipt is
held. This is the distinction the retired hover spotlight used to make on Execute, moved into the
frame where the preview model needs it.

**Key and Explain (optional, remembered).** Two toggles in the caption bar, off by default,
persisted per browser (`kg_theatre_legend`, `kg_theatre_explain`), each a full-width row UNDER
the caption so neither ever covers the stage. **Key** draws one swatch per mark from the tokens the
stage draws with (`LEGEND` is exported data: trail states, entity states, lines, marks, the row
icons) so the key cannot disagree with the stage. **Explain** is one line for the beat on stage —
or the beat or statement a click holds — in three parts from `explainBeat`/`explainStatement`
(pure exports): what it DOES (`BEAT_EXPLAIN`, constant), what it DID on this turn (the state, told
only once performed; a reconstructed beat says so), what it is doing NOW (the same view the row
renders from). The trail row's hover title carries the beat's explanation too.

**Layout and marks — no overlaps by construction.** A layered ER layout (columns = subject areas,
rows = BFS depth from the seeds, barycenter ordering) never overlaps boxes; edges are routed
straight when clear, else bent around obstacles, with the crow's foot on the border and its tines
along the tangent away from the box. Every text goes through ONE collision resolver
(`placeLabels`) over ONE occupancy registry: box rects, marks (barrier pill, perimeter label,
receipt card — kept for the turn), relationship labels (per repaint) and the **thread registry**
(the question→entity threads sampled along their curves plus their labels, kept for the turn).
The registry is what lets the perimeter enclose the thread labels, the receipt card avoid the
threads, and the barrier pill find a free slot on either side of its route. Threads are a *live*
layer: redrawn on every repaint and every zoom tick (their chip end is screen-space, their entity
end is the box dot, or the ignited field node before a model exists), animated only once.

**From the whole graph to the entities in scope.** The theatre opens on the *field* — every node
of the loaded graph at the viewer's live positions and zoom (the viewer may have panned, zoomed
or filtered first; the theatre starts from that exact frame and eases to the neutral fit, never a
cut). The seeds ignite; at the model the in-scope nodes gather into their boxes and the rest recede
to a ring outside the arrangement. The gather animates once; every later re-application (the
arrangement grows as rows unfold) settles immediately. The camera fits areas + marks + thread
labels, leaves the right 242 px to the rail in Inspect, and yields to the viewer's hand for 5 s.

Backend, all additive and fail-open: `core/kg_stage.py` (contextvar sink armed in
`execution_service.run_agent_execution`), extractor `provenance`, `_match_reasons` (one scoring
walk shared with `_match_score`), `_theatre_facts`; a failure routes through `warn_once`.
Frontend seams: `eventHandlers.js` forwards named `tool_*` frames + `user_query` and replays a
historical turn; `notifications.js` forwards `kg_stage`, genie `kg_enrichment` and
`grounding_gate`; `renderer.js` mounts/unmounts the theatre over the graph and consults it before
the legacy animation.

**Inspect is interactive; fit is mandatory.** In *Inspect*, a trail row scrubs the story to that
beat and holds it (click again to continue), hovering a row previews the frame a click would hold (see *Hover previews, click holds* above), and a
seed label or attribute row pins a why-card. A turn with two or more data statements gets a
**per-statement selector**: numbered chips in the statement strip (green applied · red stopped ·
grey plain) and sub-rows under Execute in the trail; clicking either scrubs to the moment that
statement ran — its entities lit, its rows, its barrier if it was refused, its SQL in the strip,
the later statements dimmed. Scrubbing is the same event replay the live path uses (`scrubTo(beat,
stId)` re-dispatches the turn with instant performs and holds before the first op of a later beat or
statement), so a held frame can never disagree with a live one; because a statement performed
mid-scrub knows only the statements before it, the strip is re-derived once the whole turn is known.
Whatever the story shows is on screen: the layout chooses side-by-side or stacked-and-wrapped by
whichever fits the stage at the larger zoom, the camera never magnifies past 1.2× native, never
frames partially, reserves exactly the chrome's measured extent (the question above, the strip
below while one is on stage, the trail rail on the right in Inspect — `railReserve()`, never a
constant), and the stage is re-derived from state on every resize. `test/test_kg_theatre_dom.mjs`
asserts all of it in a real Chrome (camera scale within [0.2, 1.2] and the cap actually reached on
a large stage, subject areas stacked in a narrow panel, question/strip/marks on the visible stage,
the held statement's chips and dimmed successors), with `KGT_ONLY` / `KGT_MODULE` for
mutation-testing the gate itself.

## Trusted-scope perimeter + drift overlay (Jul 2026)

The grounding gate's verdict, rendered **spatially** — the "trust made visible" R1 deliverable
([AGENT_OPS_EVAL_ARCHITECTURE.md §6c.5](AGENT_OPS_EVAL_ARCHITECTURE.md)). When a `grounding_gate`
event flows to `dispatchKGAnimation`, a dedicated branch draws a pure overlay under the nodes
(never touches the force simulation, wiped on any re-render, motion-respecting):

- **Perimeter** — a dashed green boundary (`--trust-pass`) around the graph's node bounding box,
  labelled with the declared scope (`scope_databases`), so the agent's *allowed* data region is
  visible at rest.
- **Drift markers** — each out-of-scope reference (`out_of_scope_dbs ∪ answer_oos_refs`) renders as
  a pulsing red ghost node **outside** the perimeter with a dashed red edge crossing the boundary
  ("out of scope — withheld" on a `block`). The canonical brute-force-drift failure becomes a
  five-second visual. Drift ghosts self-fade; the informative perimeter stays.

Implementation: `renderer.js` `_drawScopePerimeter` / `_spawnDriftMarkers`, reached from the
`grounding_gate` branch of `dispatchKGAnimation`. Best-effort — a malformed event leaves the
graph untouched.

## Default lens + entry-point consistency (Jun 2026)

Two correctness rules make every way of opening a graph render identically:

**Default lens = the logical business model.** `DEFAULT_INSPECT_TYPES = 'database,domain,entity,metric,business_concept,query_recipe,design_decision,quality_metric,naming_standard,product_bridge,data_product'` (`api.js` + `renderer.js`, kept in sync). A large industry-model graph opens on its conceptual map — databases → domains → entities + metrics, with the semantic/authored types (concepts, recipes, design decisions, quality metrics, naming standards, product bridges, data products) — instead of sampling a column-heavy slice. Attributes and the physical/deployment layer (`table`/`column`/`catalog`/`schema`/`ddl_artifact`) are drill-down detail: **off by default, one pill-click (or search / double-click expand) away**. The type-filter pills always render the **full catalog** (`entity_type_counts` from `get_stats()`) with shown/total counts, so every type is togglable at the default node budget regardless of what the current sample contains.

**One canonical loader for every entry point.** The split panel previously rendered a divergent, hand-built sample for the conversation `open`/`visualize` path while Inspect loaded the canonical `/graph-spec` — so the two looked different (different sample, missing tiles, sometimes no edges). `openKGSplitPanel()` is now the single canonical loader: `_fetchCanonicalSpec(kgId, profileId)` fetches `/graph-spec` with the default logical lens, and **every** entry — Inspect, the conversation `open`, the inline "Open in Graph" button, session restore, and the switcher — goes through it, so they are byte-for-byte identical. The handler's hand-built spec (now also carrying the full `entity_type_counts` catalog + `applied_types`) remains only as the graceful fallback when there is no KG identity to re-query or the fetch fails. A spec already fetched canonically is marked `_canonical` so the switcher/reload don't double-fetch.

## Opening on a concept, drafts in context, live redraw (Oct 2026)

- **Open on a concept.** A graph opened ON a node (OBDA-MODEL *Open*, a Workspace extension row, *Open in Graph*) passes `focus_names`; besides seeding the server sample, the renderer now selects that node exactly as a click does and centres the view on it once (`_focusOpenedNames` → `_centreOnNode`, a reading scale, never the neighbourhood's bounding box). Gate: `test/test_kg_open_focus.mjs`.
- **Drafts in context.** Opening the graph from a draft includes the author's unpublished drafts (`graph-spec?include_drafts=1`, kept through lens changes, re-queries and reloads), centred on the draft, drawn dashed and labelled `· draft`. The model view itself stays published-only. Gate: `test/test_kg_draft_views.py`.
- **Live redraw.** An open panel follows its component on the page bus (`UderiaBus.onComponent('knowledge_graph', …)`, the `uderia:component-changed` event announced at the store's write seam): a draft, a landing or a publish on the graph it shows redraws it in place (`preserveKGView()` + `_reloadKGPanel`), view and drafts mode kept, no page refresh. See [COMPONENT_ARCHITECTURE.md → Component Signals](COMPONENT_ARCHITECTURE.md#component-signals--how-components-talk-to-each-other).
- **Nodes vs entities.** Every count shown to people uses one vocabulary (`static/js/kgVocabulary.js`): *node* is any graph element, *relationship* an edge, *entity* only the `entity` type — a card reads "275 nodes · 15 entities". Storage/API names (`kg_entities`, `total_entities`) are unchanged and documented as node counts. Gate: `test/test_kg_vocabulary.mjs`.

## Backend

| Method | Purpose |
|---|---|
| `get_typed_subgraph(types, max_nodes, focus, project_dependencies)` | detail + focus lens sampler (seed→expand→fill); optional projection overlay |
| `get_schema_overview()` | overview lens meta-graph |
| `get_node_neighborhood(entity_id, max_neighbors=500)` | a node's COMPLETE 1-hop neighbourhood (uncapped by render budget) — the consistency guarantee for focus |
| `get_entity_by_name(name)` | resolve `focus_names` (execution knows names, not ids) |

Endpoints: `GET /v1/knowledge-graph/graph-spec` (`types`, `max_nodes`, `focus`, `focus_names`,
`project`, `divisions` / `domains` (scope filters), `include_drafts`; returns full type catalog + shown counts + totals + truncated), `GET .../schema-overview`,
`GET .../node-neighborhood` (`entity_id`; returns the full 1-hop set + `truncated`/`total_degree`).

## Tests

`test/test_kg_typed_subgraph.py` (45 CI-pure assertions): stratification covers every type
(vs biased first-N), type filter, importance-ranked + deterministic, budget fully used,
connected expansion (≥70% one component), focus seeding, schema-overview aggregation,
relationship projection — **gated** (`project_dependencies=False` yields no derived edges;
`=True` lifts table-only → derived `table↔table` FK among visible nodes; domain-level
projection stays among visible domains), and **complete node neighbourhood** (returns focus +
ALL neighbours regardless of budget, honest `total_degree`, complete incident edges). Neo4j
paths are code-reviewed (CI covers SQLite; Neo4j needs a live server).

`test/test_kg_theatre.mjs` (127 CI-pure assertions: `sqlRoles`, `buildModel`, live and synthesized
stages, prevented incl. the ReAct preview, wasted, verdict/honesty relevance, reload replay, the
label layer, beats, the beat explanations and the key's coverage of every mark) + `test/test_kg_theatre_backend.py` (49: extractor provenance,
`_match_reasons` ≡ `_match_score`, `_theatre_facts`, the sink's fail-open/once/task-local rules,
`execution_service` arming and resetting it, the handler emitting all four stages) — both in the
battery. `test/test_kg_theatre_dom.mjs` is the **executed-DOM gate** (manual, needs Chrome + the
d3 CDN, ~40 s, skips honestly without Chrome): mounts the shipped module over the demo turn in
`test/kg_theatre_harness/`, measures every rendered text at the receipt, the prevented beat and
the Inspect depth, and refuses any two that overlap; it also probes the trail MID-replay (done rows
= the beats the stage performed, the rest "up next"), a beat that never ran after the receipt, the
kept live clock, and the Key and Explain rows.

## Deferred / follow-on

- **Visual combos** (collapse a table's columns into one `+N` expandable node): the value
  (legible backbone + on-demand column drill-down) is already delivered by the
  backbone-favouring sampler + the `column` type-toggle + double-click expand; a dedicated
  grouping-node visual is a follow-on if desired.
- **GPU/WebGL** rendering (the other vendor school) — only needed if we ever want whole-graph
  macro views; the funnel covers comprehension.
