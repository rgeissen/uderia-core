// The knowledge graph's counting vocabulary — ONE place (Oct 2026).
//
//   node          any element of a graph: an entity, an attribute, a table, a column, a schema,
//                 a domain, a design decision …
//   relationship  an edge between two nodes
//   entity        a BUSINESS CONCEPT only — a node whose type is `entity` (Incident, Offence,
//                 CaseObject, Lead)
//
// The store calls every node an "entity" (`kg_entities`, `total_entities`, `entities_added`);
// those names are storage, not vocabulary — they count NODES. A count shown to a person goes
// through these functions, so "275 entities" can never again describe 275 nodes of which 15
// are entities. Gate: test/test_kg_vocabulary.mjs.

const _n = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const _plural = (v, one, many) => `${_n(v)} ${_n(v) === 1 ? one : many}`;

/** "1 node" / "275 nodes" — any count of graph elements. */
export function nodesLabel(v) { return _plural(v, 'node', 'nodes'); }

/** "1 entity" / "15 entities" — business concepts only (type `entity`). */
export function entitiesLabel(v) { return _plural(v, 'entity', 'entities'); }

/** "1 attribute" / "9 attributes". */
export function attributesLabel(v) { return _plural(v, 'attribute', 'attributes'); }

/** "1 relationship" / "422 relationships". */
export function relationshipsLabel(v) { return _plural(v, 'relationship', 'relationships'); }

/** The business-concept count of a graph summary that carries per-type counts, or null. */
export function entityCountOf(kg) {
    const t = kg && (kg.entity_types || kg.entityTypes);
    return t && t.entity != null ? _n(t.entity) : null;
}

/** A graph's size: "275 nodes · 15 entities" (the entity count when known), optionally with
 *  its relationships. `kg` is a graph summary (`total_entities` counts NODES). */
export function sizeLabel(kg, opts) {
    const nodes = _n(kg && (kg.total_nodes != null ? kg.total_nodes : kg.total_entities));
    const ents = entityCountOf(kg);
    let out = nodesLabel(nodes) + (ents != null ? ` · ${entitiesLabel(ents)}` : '');
    if (opts && opts.relationships) out += ` · ${relationshipsLabel(kg && kg.total_relationships)}`;
    return out;
}

/** What an import / sync / fork added: "275 nodes and 422 relationships". */
export function addedLabel(nodes, rels) { return `${nodesLabel(nodes)} and ${relationshipsLabel(rels)}`; }

/** An extension's drafts in words: "1 entity · 9 attributes" (other node kinds named by type). */
export function draftBreakdown(nodes) {
    let ents = 0, attrs = 0; const other = {};
    (nodes || []).forEach((n) => {
        const t = n && n.entity_type;
        if (t === 'entity') { ents += 1; attrs += (n.attributes || []).length; }
        else if (t === 'attribute') attrs += 1;
        else if (t) other[t] = (other[t] || 0) + 1;
    });
    const parts = [];
    if (ents) parts.push(entitiesLabel(ents));
    if (attrs) parts.push(attributesLabel(attrs));
    Object.entries(other).forEach(([t, c]) => parts.push(_plural(c, t.replace(/_/g, ' '), `${t.replace(/_/g, ' ')}s`)));
    return parts.join(' · ');
}

if (typeof window !== 'undefined') {
    window.KGVocab = { nodesLabel, entitiesLabel, attributesLabel, relationshipsLabel, entityCountOf, sizeLabel, addedLabel, draftBreakdown };
}
