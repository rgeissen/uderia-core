/**
 * Panel bars gate — every component panel's header and menu bar speaks ONE icon language.
 *
 *  [1] the shared glyphs (static/js/handlers/panelIcons.js): monochrome line drawings,
 *      stroke = currentColor, no colour of their own, no size in the markup.
 *  [2] one glyph, one function: a database CONNECTION and the database NAVIGATOR never
 *      share a drawing; the navigator's identity (manifest + panel header) is its own glyph.
 *  [3] one function, one glyph: the renderers take Copy / Download / Close / the
 *      connection and navigator glyphs from the shared set — no private copies survive
 *      (the copies are how the canvas, plan and dashboard panels drifted apart).
 *  [4] industrial style: no icon in a panel renderer carries a hex colour (the connection
 *      picker once coloured each driver differently).
 *  [5] one size, one stroke, one shape: the bar rule sizes every header/menu-bar icon;
 *      the global `button[aria-label]` rule no longer outranks a component's own shape;
 *      the canvas info badge is the platform badge; the flow's profile picker has the
 *      bar's height and radius; a fullscreen button is named "Fullscreen".
 *
 * No server, no network.
 */
import { readFileSync } from 'node:fs';
import { ICON } from '../static/js/handlers/panelIcons.js';

let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
    if (cond) { pass++; console.log(`  ok  ${name}`); }
    else { fail++; console.log(`  FAIL  ${name}${detail ? ' — ' + String(detail).slice(0, 240) : ''}`); }
};
const read = (p) => readFileSync(p, 'utf8');
const body = (svg) => svg.replace(/^<svg[^>]*>/, '').replace(/<\/svg>$/, '');

console.log('\n[1] the shared glyphs');
const keys = Object.keys(ICON);
check('the set names copy, download, close, connection and navigator', ['copy', 'download', 'close', 'connection', 'navigator'].every(k => keys.includes(k)), keys);
for (const k of keys) {
    const s = ICON[k];
    check(`${k}: monochrome — stroke is currentColor, no fill colour, no hex anywhere`,
        /stroke="currentColor"/.test(s) && /fill="none"/.test(s) && !/#[0-9a-f]{3,6}/i.test(s));
    check(`${k}: no size in the markup (the bar decides)`, !/\swidth="\d|\sheight="\d/.test(s.match(/^<svg[^>]*>/)[0]));
}
check('the set is frozen (a renderer cannot redraw a glyph at run time)', Object.isFrozen(ICON));

console.log('\n[2] one glyph, one function');
const bodies = keys.map(k => body(ICON[k]));
check('no two functions share a drawing', new Set(bodies).size === bodies.length);
check('the navigator is not the connection cylinder', body(ICON.navigator) !== body(ICON.connection));
const nav = JSON.parse(read('components/builtin/database_navigator/manifest.json')).icon_svg;
check('the Database Navigator\'s identity (manifest icon) is the navigator glyph', body(nav) === body(ICON.navigator), nav);
const tpl = read('templates/index.html');
const dbnavHead = tpl.slice(tpl.indexOf('id="dbnav-split-panel"'), tpl.indexOf('id="dbnav-split-panel"') + 3000);
check('…and its panel header draws the same glyph', dbnavHead.includes(body(ICON.navigator)));
check('the old cylinder identity is gone from the manifest and the header',
    !nav.includes('M4 5v6c0 1.7 3.6 3') && !dbnavHead.includes('M4 5v6c0 1.7 3.6 3'));

console.log('\n[3] one function, one glyph — no private copies');
const cv = read('components/builtin/canvas/renderer.js');
const xp = read('components/builtin/explain_plan/renderer.js');
const db = read('components/builtin/dashboard/renderer.js');
check('canvas: "Describe in navigator" draws the navigator glyph', /desc\.innerHTML = _picon\('navigator'\)/.test(cv));
check('canvas: the connection picker draws the connection glyph (trigger and every item)',
    /_connIcon = \(cls\) => _picon\('connection'\)/.test(cv) && /const driverIcon = _connIcon\('canvas-conn-item-icon'\)/.test(cv) && /btn\.innerHTML = _connIcon\(''\)/.test(cv));
check('canvas: copy and download come from the shared set', /const _copyIcon = _picon\('copy'\)/.test(cv) && /dlBtn\.innerHTML = _picon\('download'\)/.test(cv));
check('canvas: no private connection / navigator / per-driver icon tables survive',
    !/_CONN_DRIVER_ICONS|_CONN_DB_ICON|_NAV_DESCRIBE_ICON|_NAV_ASK_ICON/.test(cv));
check('plan panel: copy and download come from the shared set', /_picon\('copy'\)/.test(xp) && /_picon\('download'\)/.test(xp) && !/_ICON_COPY|_ICON_DOWNLOAD/.test(xp));
check('dashboard: close and copy come from the shared set', /get close\(\) \{ return _picon\('close'\); \}/.test(db) && /get copy\(\) \{ return _picon\('copy'\); \}/.test(db));
for (const [n, src] of [['canvas', cv], ['explain_plan', xp], ['dashboard', db]])
    check(`${n}: reads the glyphs from window.PanelIcons (loaded by main.js), never a copy`,
        /const _picon = \(k\) => \(\(typeof window !== 'undefined' && window\.PanelIcons && window\.PanelIcons\[k\]\) \|\| ''\)/.test(src));
check('main.js loads the shared set before any panel can render', /import '\.\/handlers\/panelIcons\.js\?v=[\d.]+'/.test(read('static/js/main.js')));
const closes = [...tpl.matchAll(/id="[a-z-]+-close"[^>]*>\s*<svg[^>]*>([\s\S]*?)<\/svg>/g)].map(m => m[1].replace(/\s+stroke-line(cap|join)="round"/g, '').replace(/\s+/g, ' ').trim());
check('every panel header draws Close with the same X', closes.length >= 6 && closes.every(c => /d="M6 18L18 6M6 6l12 12"/.test(c)), closes);

console.log('\n[4] industrial style — no colour in a panel icon');
const renderers = ['canvas', 'dashboard', 'database_navigator', 'explain_plan', 'flow', 'knowledge_graph', 'memory', 'scheduler'];
for (const r of renderers) {
    const src = read(`components/builtin/${r}/renderer.js`);
    const hex = src.match(/(stroke|fill)=['"]#[0-9a-fA-F]{3,6}/g) || [];
    check(`${r}: no icon carries a hex stroke or fill`, hex.length === 0, hex.slice(0, 3));
}

console.log('\n[5] one size, one stroke, one shape');
const sp = read('static/css/split-panels.css');
check('one bar rule sizes every header and menu-bar icon (one size, one stroke)',
    /\.sp-panel \{ --sp-bar-icon: 16px; \}/.test(sp)
    && /\.sp-panel \.sp-header \.sp-action-btn > svg,\n\.sp-panel \[data-sp-subheader\] button > svg:not\(\[class\*="check"\]\) \{[^}]*width: var\(--sp-bar-icon\);[^}]*height: var\(--sp-bar-icon\);[^}]*stroke-width: 2;/.test(sp));
const th = read('static/css/themes.css');
check('the global aria-label button rule stays at element specificity (cannot square a component\'s shape)',
    /button:where\(\[class\*="icon-button"\], \[aria-label\]\) \{/.test(th) && !/^button\[aria-label\]/m.test(th));
check('the canvas info badge is the platform badge (one badge shape)', /badge\.className = 'canvas-info-badge pc-badge'/.test(cv)
    && !/\.canvas-info-badge \{[^}]*border-radius/.test(cv));
const fl = read('components/builtin/flow/renderer.js');
check('the flow\'s profile picker has the bar\'s height and radius', /minHeight: 'var\(--ctl-height, 28px\)', borderRadius: 'var\(--ctl-radius, 8px\)'/.test(fl));
check('a fullscreen button is named Fullscreen everywhere (no "Expand panel")', !/Expand panel/.test(tpl) && !/'Expand panel'/.test(db));

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) { console.log(`FAILURES — ${fail} of ${pass + fail}`); process.exit(1); }
console.log(`ALL PASS — ${pass} assertions (one icon language across the panel bars)`);
