# Knowledge Graph Constructor — Declared Model (ISO/IEC 11179)

Builds a Knowledge Graph from a model that stores its own metadata **with it** as an
ISO/IEC 11179 metadata registry — five tables in a catalog database:

| Registry table | 11179 concept | Becomes |
|---|---|---|
| `object_class` | Object Class | logical `entity` (+ physical `table`, linked by `implements`) |
| `data_element` | Data Element | logical `attribute` (+ physical `column`) with definition, PII class, declared vs deployed type |
| `value_domain` | Value Domain | the code-set an attribute draws from |
| `permissible_value` | Permissible Value | decoded values on the attribute (`'120'` = Robbery) |
| `association` | association layer (cf. ISO/IEC 19763) | `foreign_key` (pk_fk_join / bridge), `is_a`, or a `product_bridge` node (type_correspondence) |
| *dictionary* | `DBC.ColumnsV` | the **authoritative** deployed column types — views included |

Deterministic, zero-LLM, zero-token, and re-syncable. The interchange contract is a
recognised standard rather than a bespoke format, and the resulting graph exports to
RDFS/OWL/SKOS/DCAT through the platform's semantic-interchange layer — standard in,
standard out.

## Discovery

A registry is a database holding **all five** tables; the constructor scans the
connection's dictionary and offers exactly those. Each entry carries the core database
its entities deploy into (read from `deployed_object`), so nothing needs typing.

## The type rule

Meaning comes from the registry (`declared_type`); the deployed type comes from the data
dictionary. Because `DBC.ColumnsV` covers views, a zero-copy view layer is type-correct.
A divergence is counted and reported on the build card, never silently reconciled.

## The bridge

An `association` of kind `type_correspondence` is a declared **class-level** correspondence
between two domains that share no per-record key. It becomes a `product_bridge` node
stating its basis and whether both ends resolved — the enrichment renders it as a
conceptual correspondence the agent reads and states, never as a table to `SELECT FROM`
or `JOIN`.

## Reference

The Policing Core Model (`agent_packs/policing-data-products`, source repo
`policing-data-products/src/core-model`) is the reference registry: 15 entities across a
crime and an evidence subject area, decoded through 26 value domains, joined by 10 keyed
relationships, with one type correspondence between them.
