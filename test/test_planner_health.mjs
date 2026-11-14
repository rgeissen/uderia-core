#!/usr/bin/env node
/**
 * Planner Health gate (UI) — the planner twin of Corpus Health.
 *
 * The panel's markup is a pure function of the manifest (`renderPlannerHealth`), so this
 * gate reads it without a DOM or a server: the chips, the two acts (Remove · Acknowledge)
 * offered exactly where they are owed, the excerpt escaped, the theme contract (CSS
 * variables, no Tailwind colour classes, no emoji), and the wiring — the inspect view
 * shows the panel for planner repositories only, the Trust Circle names the case in its
 * evidence slot and routes a planner blocker to Planner Health, the module follows a
 * write made elsewhere through the registered bus event.
 */
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ''}`); } };
const read = (f) => readFileSync(f, 'utf8');

globalThis.localStorage = { getItem: () => 'tok' };
const { renderPlannerHealth } = await import(pathToFileURL('static/js/handlers/plannerHealth.js').href);

const OPEN = { case_id: 'case_bbbb', user_query: 'How many exhibits belong to each robbery?', blocking: ['stacked-statement'], caveats: [],
    argument: 'sql', excerpt: "SELECT 'A' FROM x; SELECT '<script>' FROM y;", acknowledged: false, ack: null, is_most_efficient: true, timestamp: '2026-10-15T08:01:09+00:00' };
const ACKED = { ...OPEN, case_id: 'case_cccc', user_query: 'quoted semicolon', acknowledged: true, ack: { labels: ['stacked-statement'], reason: 'inside a string literal', by: 'u', at: '2026-11-03T10:00:00+00:00' } };
const NOTED = { case_id: 'case_dddd', user_query: 'recent orders', blocking: [], caveats: ['sql-comment'], argument: 'sql', excerpt: 'SELECT * FROM o -- last week', acknowledged: false, ack: null, is_most_efficient: false, timestamp: null };
const verdict = (band, note) => ({ status: 'x', band, signable: band !== 'blocked', note });

console.log('[1. the empty planner]');
{
    const h = renderPlannerHealth({ cases: 0, findings: [], open: 0, acknowledged: 0, caveat_only: 0, verdict: verdict('blocked', 'empty') }, 5);
    ok('says there are no champion cases', /no champion cases/.test(h));
    ok('offers no act', !/data-ph-act/.test(h));
}

console.log('[2. an open finding]');
{
    const m = { cases: 11, findings: [OPEN], open: 1, acknowledged: 0, caveat_only: 0, pack_owned: false,
        verdict: verdict('blocked', 'champion cases contain unsafe SQL / injection (stacked-statement) — 1 case: “How many exhibits belong to each robbery?”') };
    const h = renderPlannerHealth(m, 525);
    ok('counts the scanned cases', /11 cases scanned/.test(h));
    ok('names the blocking count in the danger vocabulary', /tc-danger">1 case block sign-off/.test(h));
    ok('the section is labelled as what it is', /Blocking sign-off — champion cases the gate refuses/.test(h));
    ok('names the case by its question', /“How many exhibits belong to each robbery\?”/.test(h));
    ok('…with its label and state', /stacked-statement/.test(h) && /tc-danger">unresolved/.test(h));
    ok('…and the champion mark', /tc-warn">champion/.test(h));
    ok('offers Remove on the case', /data-ph-act="remove" data-case-id="case_bbbb"/.test(h));
    ok('offers Acknowledge on an OPEN finding', /data-ph-act="ack" data-case-id="case_bbbb"/.test(h));
    ok('offers Show (select the row)', /data-ph-act="show" data-case-id="case_bbbb"/.test(h));
    ok('the excerpt is rendered…', /SELECT 'A' FROM x; SELECT/.test(h));
    ok('…escaped (a script tag in SQL never becomes markup)', /&lt;script&gt;/.test(h) && !/<script>/.test(h));
    ok('…and names the argument that tripped the scanner', /sql · <\/span>/.test(h));
    ok('the gate verdict is quoted — the panel never invents its own', /gate: <span[^>]*>blocked<\/span> — champion cases contain unsafe SQL/.test(h));
    ok('says which findings gate a signature', /Only\s+<span[^>]*>blocking findings<\/span>/.test(h));
    ok('no pack note when not pack-owned', !/pack-owned/.test(h));
    ok('no Quarantine — a champion case has no kept-but-excluded state', !/uarantine/.test(h));
}

console.log('[3. acknowledged and noted]');
{
    const m = { cases: 3, findings: [ACKED, NOTED], open: 0, acknowledged: 1, caveat_only: 1, pack_owned: true,
        verdict: verdict('partial', '3 case(s) scanned · noted: sql-comment, 1 finding acknowledged by the owner') };
    const h = renderPlannerHealth(m, 7);
    ok('no open findings reads pass', /tc-pass">no open findings/.test(h));
    ok('counts acknowledged and noted', /tc-pass">1 acknowledged/.test(h) && /tc-warn">1 noted/.test(h));
    ok('an acknowledged case: state chip, reason and date shown', /tc-pass">acknowledged/.test(h) && /acknowledged 2026-11-03: inside a string literal/.test(h));
    ok('…Acknowledge is NOT offered again', !/data-ph-act="ack" data-case-id="case_cccc"/.test(h));
    ok('…Remove still is', /data-ph-act="remove" data-case-id="case_cccc"/.test(h));
    ok('a noted case reads noted and offers no Acknowledge', /tc-warn">noted/.test(h) && !/data-ph-act="ack" data-case-id="case_dddd"/.test(h));
    ok('the sections are labelled', /Acknowledged — the owner's recorded judgement/.test(h) && /Noted — does not block/.test(h));
    ok('a pack-owned repository is told the next update restores a seed', /pack-owned/.test(h));
    ok('gate band partial is quoted', /gate: <span[^>]*>partial<\/span>/.test(h));
}

console.log('[4. theme + icon contract]');
{
    const src = read('static/js/handlers/plannerHealth.js');
    const h = renderPlannerHealth({ cases: 2, findings: [OPEN, ACKED], open: 1, acknowledged: 1, caveat_only: 0, verdict: verdict('blocked', 'n') }, 1);
    ok('no Tailwind colour classes in the markup', !/\b(text|bg|border)-(red|green|gray|purple|orange|slate|amber)-\d/.test(h));
    ok('colours come from CSS variables', /var\(--trust-danger/.test(h) && /var\(--trust-pass/.test(h) && /var\(--text-muted/.test(h));
    ok('no emoji anywhere in the module', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(src));
    ok('the token is the one key', /localStorage\.getItem\('tda_auth_token'\)/.test(src) && !/jwt_token|auth_token'/.test(src.replace(/tda_auth_token/g, '')));
    ok('never a native dialog', !/window\.(confirm|prompt|alert)\(/.test(src));
    ok('the themed confirm and prompt are used', /showConfirmBanner/.test(src) && /showPromptModal/.test(src));
}

console.log('[5. wiring — one surface, one event, one reading]');
{
    const ui = read('static/js/ui.js'), idx = read('templates/index.html'), aph = read('static/js/handlers/agentPerformanceHandler.js');
    ok('the inspect view carries the Planner Health container', /id="planner-health"/.test(idx) && /id="planner-health-live"/.test(idx));
    ok('ui.js shows it for planner repositories only', /ph\.classList\.toggle\('hidden', !isPlanner\)/.test(ui));
    ok('…and loads the module on first use, pinned', /import\('\.\/handlers\/plannerHealth\.js\?v=[0-9.]+'\)/.test(ui));
    ok('…fetching the manifest for the inspected collection', /m\.fetchPlannerHealth\(collectionId\)/.test(ui));
    ok('the rows view exposes its refresh and its selection to the panel', /window\.ragRefreshInspectedRows = /.test(ui) && /window\.ragSelectCase = /.test(ui));
    const src = read('static/js/handlers/plannerHealth.js');
    ok('the panel reads the gate\'s manifest route', /\/api\/v1\/rag\/collections\/\$\{collectionId\}\/scan-manifest/.test(src));
    ok('Remove is the existing delete route', /rows\/\$\{encodeURIComponent\(caseId\)\}`,\s*\{ method: 'DELETE'/.test(src));
    ok('Acknowledge posts a reason to the acknowledge route', /acknowledge-finding`/.test(src) && /JSON\.stringify\(\{ reason: reason\.trim\(\) \}\)/.test(src));
    ok('follows a write made elsewhere through the registered bus event', /UderiaBus\.on\('uderia:trust-event'/.test(src) && /'planner_case_removed'/.test(src) && /'planner_finding_ack'/.test(src));
    ok('…scoped to the inspected collection', /String\(d\.collection_id\) !== String\(live\.dataset\.collectionId\)/.test(src));
    ok('the bus event it listens to is registered', /'uderia:trust-event':\s*\{ detail:/.test(read('static/js/uderiaBus.js')));
    ok('the Trust Circle evidence names the case from the blocker\'s finding', /blockerFindings/.test(aph) && /b\.finding/.test(aph) && /\.\.\.blockerFindings\.slice\(0, 2\)/.test(aph));
    ok('…and points a planner blocker at Planner Health', /remove the case or acknowledge its finding under Planner Health/.test(aph));
    ok('the blocker route names the right surface per kind', /kind === 'planner' \? 'Planner Health' : 'Corpus Health'/.test(aph));
    ok('the two acts have labels', /planner_finding_ack: '/.test(aph) && /planner_case_removed: '/.test(aph));
}

console.log(fail ? `\n${pass} passed, ${fail} FAILED` : `\nALL PASS — ${pass} assertions (Planner Health: the gate's own reading, the owed acts, one surface).`);
process.exit(fail ? 1 : 0);
