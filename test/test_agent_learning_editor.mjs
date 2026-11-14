#!/usr/bin/env node
/**
 * OC-F5 — the agent editor shows an agent's OWN learning repository and never binds it.
 *
 * An Optimize agent with its own planner repository (a collection whose learning_profile_id is
 * the agent's id) learns and reads there; the repository is NOT a binding, so what the agent
 * learns never enters its signature. The editor must therefore:
 *   1. show it as a fixed "LEARNS HERE" row WITHOUT a query/autocomplete toggle (those toggles are
 *      what `ragCollections` / `autocompleteCollections` are collected from);
 *   2. stop force-binding the shared server default for such an agent (and for every NEW agent,
 *      which gets its own repository at creation) — both at render and in the save's
 *      defensive "always include the default" step;
 *   3. never pick an agent's repository as the shared default.
 * Agents without one keep the shared default exactly as before. No browser, no server.
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) { pass++; console.log(`  ✓ ${label}`); } else { fail++; console.log(`  ✗ ${label}`); } };
const src = readFileSync(new URL('../static/js/handlers/configurationHandler.js', import.meta.url), 'utf8');

console.log('\n── §1 who learns into their own repository');
check('an agent learns own when a planner carries its id', /const ownLearningRepos = plannerCollections\.filter\(c => profile && c\.learning_profile_id === profile\.id\);/.test(src));
check('…a NEW agent always does (it is provisioned at creation)', src.includes('const learnsOwn = isEdit ? ownLearningRepos.length > 0 : true;'));

console.log('\n── §2 the own repository is a fixed row, never a binding');
const row = src.slice(src.indexOf('const ownLearningRowHTML'), src.indexOf('const ownLearningNoteHTML'));
check('the row exists and says LEARNS HERE', row.includes('LEARNS HERE') && row.includes('data-own-learning-repository'));
check('…it carries NO query/autocomplete toggle (nothing the save could collect)', !row.includes('data-collection-type'));
check('…its colours are theme variables or translucent accents (no Tailwind colour classes)',
      !/\b(text|bg|border)-(gray|purple|blue|white)-\d/.test(row));
check('the row builder returns it for the agent\'s own repository on the current server only',
      src.includes("if (isPlanner && profile && coll.learning_profile_id === profile.id) {")
      && src.includes("return coll.mcp_server_id === currentMcpServerId ? ownLearningRowHTML(coll) : '';"));

console.log('\n── §3 the shared default is not forced on an agent that learns own');
check('render: the default is "always on" only for an agent without its own repository',
      src.includes('const isDefaultCollection = isPlanner && !learnsOwn && coll.id === defaultCollectionId;'));
check('save: the defensive "include the default" step skips such an agent',
      src.includes('if (!learnsOwn && defaultCollectionId !== null && !selectedRag.includes(defaultCollectionId)) {'));
check('a note says where it will learn when the repository does not exist yet',
      src.includes("'Learns into its own planner repository — created with the agent.'")
      && src.includes("'Learns into its own planner repository — created on its first lesson on this server.'"));

console.log('\n── §4 an agent repository is never the shared default');
const calc = src.slice(src.indexOf('const calculateDefaultCollectionId'), src.indexOf('const calculateDefaultCollectionId') + 600);
check('the default is the oldest planner on the server that is NOT an agent\'s', calc.includes('&& !c.learning_profile_id'));

console.log(`\n${fail ? 'FAIL' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
