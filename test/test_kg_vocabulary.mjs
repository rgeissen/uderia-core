// The knowledge graph's counting vocabulary (static/js/kgVocabulary.js) — and that NO screen
// counts nodes as "entities" again.
//
// Found Oct 2026 on production: the Policing Core Model's card read "275 entities" — 275 nodes,
// of which 15 are entities (business concepts); Govern offered "Publish 10 drafts" for one entity
// and its nine attributes; the answer chip said "12 entities" for twelve graph nodes. The store
// calls every node an "entity" (kg_entities, total_entities, entities_added) and ~35 labels in ten
// screens carried that storage word to people.
//
//   §A  the vocabulary: node (any element) · relationship (edge) · entity (type `entity` only)
//   §B  no user-facing label counts anything as "entities" outside the vocabulary module
//
// Run: node test/test_kg_vocabulary.mjs
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import * as V from '../static/js/kgVocabulary.js';

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 400) : ''}`); }
};

console.log('[§A] the vocabulary');
check('nodes and entities are different words', V.nodesLabel(275) === '275 nodes' && V.entitiesLabel(15) === '15 entities'
  && V.nodesLabel(1) === '1 node' && V.entitiesLabel(1) === '1 entity');
check('a graph\'s size names both — nodes first, entities from its type counts',
  V.sizeLabel({ total_entities: 275, entity_types: { entity: 15, attribute: 120, table: 15 } }) === '275 nodes · 15 entities');
check('…the stored field `total_entities` is read as what it is: a NODE count',
  V.sizeLabel({ total_entities: 275 }) === '275 nodes');
check('…with relationships when asked', V.sizeLabel({ total_entities: 2, entity_types: { entity: 1 }, total_relationships: 1 }, { relationships: true })
  === '2 nodes · 1 entity · 1 relationship');
check('an edge is never a node: what an import added is nodes AND relationships', V.addedLabel(286, 436) === '286 nodes and 436 relationships');
check('an extension\'s drafts in words: an entity with its attributes',
  V.draftBreakdown([{ entity_type: 'entity', attributes: new Array(9).fill({}) }]) === '1 entity · 9 attributes');
check('…an attribute on a base entity is an attribute, other kinds are named by type',
  V.draftBreakdown([{ entity_type: 'attribute' }, { entity_type: 'design_decision' }]) === '1 attribute · 1 design decision');
check('…nothing to say is the empty string (the caller falls back to a node count)', V.draftBreakdown([]) === '');

console.log('[§B] no screen counts anything as "entities" outside the vocabulary');
const roots = ['static/js', 'components/builtin'];
const files = [];
const walk = (d) => { for (const f of readdirSync(d)) { const p = join(d, f); if (statSync(p).isDirectory()) walk(p); else if (p.endsWith('.js')) files.push(p); } };
roots.forEach(walk);
// a number (or an interpolated count) followed by the word entity/entities, or a hand-rolled plural
const PATTERNS = [/\$\{[^}]+\}\s*entit(?:y|ies)\b/, /'entity',\s*'entities'/, /entit\$\{/, /\b\d+ entities\b/];
const offenders = [];
for (const f of files) {
  if (f.endsWith('kgVocabulary.js')) continue;
  readFileSync(f, 'utf8').split('\n').forEach((line, i) => {
    const code = line.replace(/\/\/.*$/, '').trim();
    if (!code || code.startsWith('*') || code.startsWith('/*')) return;     // comments say what code did
    if (PATTERNS.some(p => p.test(code))) offenders.push(`${f}:${i + 1}: ${code.slice(0, 120)}`);
  });
}
check(`no label counts as "entities" by hand (${files.length} files scanned)`, offenders.length === 0, offenders);
const vocabUsers = files.filter(f => /kgVocabulary\.js\?v=/.test(readFileSync(f, 'utf8')));
check('the screens that show graph counts speak through the vocabulary',
  ['knowledgeGraphPanelHandler.js', 'marketplaceHandler.js', 'workspacePanelHandler.js', 'ui.js', 'kgTheatre.js',
   'ontologyPanelHandler.js', 'kgConstructorModal.js', 'kgDeclaredModelModal.js', 'kgAndpSemanticModal.js', 'renderer.js']
    .every(n => vocabUsers.some(f => f.endsWith(n))), vocabUsers);
const gov = readFileSync('static/js/handlers/knowledgeGraphPanelHandler.js', 'utf8');
check('Govern says what it publishes in words, never "N drafts"',
  gov.includes('Publish ${esc(mi.unWords)}') && !/Publish \$\{un\} draft/.test(gov));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
