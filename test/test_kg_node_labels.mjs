#!/usr/bin/env node
/**
 * KG node LABEL contract — the painted label is the shortest suffix that is unique
 * within the rendered set, never the qualified name truncated from the left.
 *
 * Why this gate exists: KG node names are deliberately QUALIFIED
 * (`fitness_db.Customers.State`) — that qualification is load-bearing for the resolver,
 * the grounding scope, and cross-product collision safety, so it must NOT be changed at
 * the source. Painting it is the defect: a character-capped qualified name renders the
 * prefix every node shares and truncates away the only part that identifies the node.
 * The rules below are the ones a naive "just show the last segment" fix gets wrong.
 *
 * CI-pure: no server, no browser, no network — the real helpers are extracted from the
 * shipped renderer and executed, so a rewrite that drops a rule fails here.
 */
import fs from 'fs';
import path from 'path';

const SRC = path.join(process.cwd(), 'components/builtin/knowledge_graph/renderer.js');
const src = fs.readFileSync(SRC, 'utf8');

function grab(name) {
    const i = src.indexOf(`function ${name}(`);
    if (i < 0) throw new Error(`renderer.js no longer defines ${name}() — the label contract moved`);
    let depth = 0, started = false;
    for (let j = i; j < src.length; j++) {
        if (src[j] === '{') { depth++; started = true; }
        else if (src[j] === '}') { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
    }
    throw new Error(`unbalanced braces extracting ${name}`);
}
const NAMES = ['_kgNameSegments', '_kgSuffix', '_kgIsQualifiedType', '_computeDisplayNames', '_kgDisplayKey', '_kgNodeLabel'];
const TYPESET = (() => {
    const i = src.indexOf('const KG_QUALIFIED_TYPES');
    if (i < 0) throw new Error('renderer.js no longer declares KG_QUALIFIED_TYPES');
    return src.slice(i, src.indexOf(';', src.indexOf(']', i)) + 1);
})();
const M = new Function(TYPESET + '\n' + NAMES.map(grab).join('\n') + `\nreturn {${NAMES.join(',')},KG_QUALIFIED_TYPES};`)();

let pass = 0, fail = 0;
const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    ok ? pass++ : (fail++, console.log(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`));
};

// The real shape of a discovery KG (logical attributes + their physical columns).
const NODES = [
    { name: 'fitness_db.Customers.State', type: 'column' },
    { name: 'Customers.State',            type: 'attribute' },
    { name: 'Customers.City',             type: 'attribute' },
    { name: 'Sales.City',                 type: 'attribute' },
    { name: 'fitness_db.Customers.City',  type: 'column' },
    { name: 'fitness_db.Sales.City',      type: 'column' },
    { name: 'fitness_db.Customers',       type: 'table' },
    { name: 'Customers',                  type: 'entity' },
    { name: 'Customer',                   type: 'business_concept' },
    { name: 'fitness_db',                 type: 'database' },
    { name: 'fitness_db',                 type: 'schema' },
    { name: 'Agents',                     type: 'domain' },
];
const map = M._computeDisplayNames(NODES);
const label = (n, max = 22) => M._kgNodeLabel(n, map, max);

// [1] The leaf is the label when nothing collides.
check('unique leaf is the label (column)',    label(NODES[0]), 'State');
check('unique leaf is the label (attribute)', label(NODES[1]), 'State');
check('a name with no dots is untouched',     label(NODES[11]), 'Agents');

// [2] Uniqueness is per TYPE — the viewer already separates a column from the attribute
// it implements (colour + the type line), so those two must NOT force each other wide.
check('column and its attribute both stay at the leaf',
      [label(NODES[0]), label(NODES[1])], ['State', 'State']);
check('table and entity of the same name both stay at the leaf',
      [label(NODES[6]), label(NODES[7])], ['Customers', 'Customers']);
check('same name across database/schema stays at the leaf',
      [label(NODES[9]), label(NODES[10])], ['fitness_db', 'fitness_db']);

// [3] A REAL collision (same type, same leaf) widens BOTH sides by one segment — and
// only by one: the fix must not fall back to the fully qualified name.
check('same-type leaf collision widens both attributes',
      [label(NODES[2]), label(NODES[3])], ['Customers.City', 'Sales.City']);
check('same-type leaf collision widens both columns (one segment, not the full path)',
      [label(NODES[4]), label(NODES[5])], ['Customers.City', 'Sales.City']);

// [4] Truncation applies to the DISPLAY name, so the identifying end survives.
const LONG = [{ name: 'db.tbl.a_very_long_column_identifier', type: 'column' }];
const longMap = M._computeDisplayNames(LONG);
const longLabel = M._kgNodeLabel(LONG[0], longMap, 22);
check('long leaf is capped', longLabel.length <= 22, true);
check('the cap keeps the leaf, never the prefix', longLabel.startsWith('a_very_long_column'), true);
check('a capped label is marked as capped', longLabel.endsWith('…'), true);

// [4b] A dot is not proof of a hierarchy. Only the types the walkers QUALIFY are
// shortened — a platform-ontology node called `uderia.com` must never read as `com`,
// and an unknown/new type keeps its full name rather than a wrong one.
const ODD = [
    { name: 'uderia.com',   type: 'agent' },
    { name: 'model.json',   type: 'artifact' },
    { name: 'Customers.State', type: 'attribute' },
];
const oddMap = M._computeDisplayNames(ODD);
check('an unqualified type is painted verbatim', M._kgNodeLabel(ODD[0], oddMap, 22), 'uderia.com');
check('an unknown type is painted verbatim',     M._kgNodeLabel(ODD[1], oddMap, 22), 'model.json');
check('a qualified type beside it still shortens', M._kgNodeLabel(ODD[2], oddMap, 22), 'State');
check('the qualified-type set is explicit', M.KG_QUALIFIED_TYPES.has('column') && !M.KG_QUALIFIED_TYPES.has('agent'), true);
check('the fallback path honours the same rule',
      [M._kgNodeLabel({ name: 'a.b', type: 'agent' }, null, 22),
       M._kgNodeLabel({ name: 'a.b', type: 'column' }, null, 22)], ['a.b', 'b']);

// [5] Determinism — the same set always paints the same labels (no ordering luck).
check('deterministic',
      JSON.stringify([...M._computeDisplayNames(NODES)]),
      JSON.stringify([...M._computeDisplayNames(NODES)]));

// [6] Degenerate input degrades, never throws (the renderer must not die on bad data).
check('empty set', [...M._computeDisplayNames([]).keys()], []);
check('a node with no name is skipped', [...M._computeDisplayNames([{ type: 'x' }]).keys()], []);
check('no map falls back to the leaf', M._kgNodeLabel({ name: 'a.b.c', type: 'column' }, null, 22), 'c');
check('trailing dot tolerated', M._kgSuffix('a.b.', 1), 'b');
check('segments', M._kgNameSegments('a.b.c'), ['a', 'b', 'c']);

// [7] The full qualified name is still what the graph is keyed on — the display map is a
// PAINTING concern and must never mutate the node.
const before = JSON.stringify(NODES);
M._computeDisplayNames(NODES);
check('nodes are not mutated', JSON.stringify(NODES), before);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
