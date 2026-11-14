#!/usr/bin/env node
/**
 * Version-pin gate — ONE version per module, repo-wide.
 *
 * The `?v=` pins are cache-busters rooted at index.html: a module whose CONTENT
 * changed but whose pin (anywhere) did not is served STALE from the browser cache,
 * and two different pins for one module load two live instances (forked module
 * state — the split-brain the changelog fought for ui.js/eventHandlers, which then
 * recurred for notifications and conversationAgentHandler, and burned three live
 * debugging rounds when a stale main.js kept the whole old graph running through
 * a server restart). This gate makes the class un-shippable:
 *   1. every module referenced with ?v= carries exactly ONE version everywhere;
 *   2. every ES-module importer chain is rooted at a pinned entry in index.html
 *      (a changed inner pin is only delivered if its importer's pin changed too —
 *      enforced socially by rule 1 + the bump-the-root habit; the gate pins the
 *      mechanical half).
 */
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };

// BOTH reference forms: the ES-import form (`name.js?v=1.2`) AND the Jinja tag
// form (`filename='js/name.js') }}?v=1.2`) — the gate's first version saw only
// the import form and passed a real index-vs-importer fork (notifications 4.5/4.6).
const raw = execSync(
    "grep -rhoE \"[A-Za-z0-9_/]+\\.js('\\) \\}\\})?\\?v=[0-9.]+\" static/js templates components/builtin",
    { encoding: 'utf8' }).trim().split(/\n+/);
const mods = new Map();
for (const ref of raw) {
    const m = ref.match(/([A-Za-z0-9_]+\.js)(?:'\) \}\})?\?v=([0-9.]+)/);
    if (!m) continue;
    const [, name, ver] = m;
    if (!mods.has(name)) mods.set(name, new Set());
    mods.get(name).add(ver);
}
check('at least the core modules are pinned', mods.size >= 10);
for (const [name, vers] of [...mods.entries()].sort()) {
    check(`${name} has ONE pin (${[...vers].join(' vs ')})`, vers.size === 1);
}
// ── MIXED: pinned at one importer, bare at another ───────────────────────────────
// `./x.js?v=1` and `./x.js` are two URLs, so the browser loads two live instances —
// and a bare import is cached for 12 h, so it keeps serving the old file. Found when
// componentRenderers.js was imported bare by all three importers: the new EXPLAIN
// renderer never reached a returning browser, and pinning only some importers would
// have split its renderer registry in two. A module pinned anywhere is pinned everywhere.
// Only .js files are importers — a README's example `import` is prose, not a second URL.
const bare = execSync(
    "grep -rhoE --include='*.js' \"(from|import\\() ?'\\.{1,2}/[A-Za-z0-9_/]+\\.js'\" static/js components/builtin || true",
    { encoding: 'utf8' }).trim().split(/\n+/).filter(Boolean)
    .map(r => (r.match(/([A-Za-z0-9_]+\.js)'/) || [])[1]).filter(Boolean);
// Only ES-import pins count: a classic <script> tag is a separate instance from any
// module import anyway (profileColors.js is loaded both ways on purpose).
const esPinned = new Set(raw.filter(r => !r.includes("') }}")).map(r => (r.match(/([A-Za-z0-9_]+\.js)\?v=/) || [])[1]).filter(Boolean));
const mixed = [...new Set(bare.filter(n => esPinned.has(n)))].sort();
check(`no module is pinned at one importer and bare at another (${mixed.join(', ') || 'none'})`, mixed.length === 0);

// The cache ROOT: main.js must itself be pinned in index.html — an unpinned or
// never-bumped root serves the whole old graph through any number of restarts.
const idx = execSync("grep -c 'js/main.js.*?v=' templates/index.html", { encoding: 'utf8' }).trim();
check('main.js is version-pinned in index.html', Number(idx) >= 1);

// ── FRESHNESS, not just consistency ─────────────────────────────────────────────
// This gate passed while `qualityDimensions.js` changed FOUR times and its pin stood
// still at 6.5: consistency was intact (one pin everywhere), it was simply the wrong
// pin. A returning browser then serves the cached old module through any number of
// restarts, and the symptom is "the fix didn't take" — the exact failure this file
// exists for, one level up. A `sed` bump that matches nothing exits 0, so nothing said.
//
// The rule: if a module's CONTENT differs from HEAD, its pin must differ too.
//
// NB `readFileSync`, not `execSync('cat ...')`: index.html is >1MB and blew execSync's
// default buffer with ENOBUFS, which the first version of this block swallowed into a
// permissive pass — a gate that cannot fail is worse than no gate.
let changed = [];
let idxHead = '';
try {
    changed = execSync('git diff --name-only HEAD -- static/js static/css components/builtin',
        { encoding: 'utf8' }).trim().split('\n').filter(Boolean);
    idxHead = execSync('git show HEAD:templates/index.html',
        { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
} catch {
    changed = [];   // no git / no HEAD — the check is inert, never a false failure
}
if (changed.length) {
    const idxNow = readFileSync('templates/index.html', 'utf8');
    // A module is pinned in EITHER form: the Jinja <script> tag in index.html, or an
    // ES-import elsewhere in static/js. The first version of this block read only the
    // Jinja form, so an import-only module (skillHandler, skillsPanelHandler, …) hit
    // `now === null` and was SILENTLY SKIPPED — it shipped stale pins while the gate
    // reported ALL PASS. Both forms are now checked; a module in neither is inert.
    const jsNow = execSync("cat $(git ls-files 'static/js/**/*.js' 'static/js/*.js' 'components/builtin/*/renderer.js')",
        { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    const jsHead = execSync(
        "for f in $(git ls-files 'static/js/**/*.js' 'static/js/*.js' 'components/builtin/*/renderer.js'); do git show HEAD:$f; done",
        { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    for (const f of changed) {
        const base = f.split('/').pop();
        const esc = base.replace(/\./g, '\\.');
        const jinja = new RegExp(esc + "'\\) \\}\\}\\?v=([0-9.]+)");
        const esimp = new RegExp(esc + "\\?v=([0-9.]+)");
        const now = (idxNow.match(jinja) || [])[1] || (jsNow.match(esimp) || [])[1] || null;
        const head = (idxHead.match(jinja) || [])[1] || (jsHead.match(esimp) || [])[1] || null;
        if (now === null) continue;   // pinned nowhere — nothing to bump
        check(`${base} changed since HEAD, so its pin moved (${head} -> ${now})`,
              now !== head);
    }
}

console.log(fail ? `\n${pass} passed, ${fail} FAILED`
                 : `\nALL PASS — ${pass} assertions (one version per module, everywhere).`);
process.exit(fail ? 1 : 0);
