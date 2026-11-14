#!/usr/bin/env node
/**
 * The page bus — ONE registry of event names, ONE target, ONE prefix (Oct 2026).
 *
 * Components talk over `UderiaBus` (static/js/uderiaBus.js). This gate holds the contract that
 * keeps a name from drifting between its emitter and its listener — the failure the bus was
 * built against (three events fired into the void, one listener waited for an event nobody sent,
 * five naming styles over two targets):
 *
 *   1. nothing dispatches a CustomEvent on document or window itself, and nothing listens on
 *      them for a bus name — every site goes through UderiaBus.emit / on / off / onComponent / announce
 *   2. every emitted name and every listened name is registered
 *   3. every registered name has an emitter (on the page, or relayed from a server notice) and,
 *      unless marked `public` (a hook for what follows), a listener
 *   4. one change event for every component: the server notice `component_changed` becomes
 *      `uderia:component-changed`, no component keeps a private "…-changed" name
 *   5. every component manifest declares its `signals` (emits · listens · announces · consumes_refs),
 *      and what it declares is what its renderer does — no more, no less
 *   6. the bus itself: DOM listener semantics, off() removes, onComponent filters and unsubscribes,
 *      announce() dedupes the page's own echo, an unregistered name warns and still delivers
 *   7. own or elsewhere: CLIENT_ID on every same-origin fetch, back on the notice, `own` on the event —
 *      the canvas, the flow and the dashboard follow a write made elsewhere and never overwrite a hand
 *
 * No server, no browser. Run: node test/test_component_bus.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import './_uderia_bus.mjs';

let pass = 0, fail = 0;
const check = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + String(detail).slice(0, 300) : ''}`); } };
const read = (p) => fs.readFileSync(p, 'utf8');
const walk = (dir, out = []) => { for (const e of fs.readdirSync(dir, { withFileTypes: true })) { const p = path.join(dir, e.name); if (e.isDirectory()) { if (!['dist', 'node_modules'].includes(e.name)) walk(p, out); /* dist/: local build output, git-ignored, never served */ } else if (p.endsWith('.js')) out.push(p); } return out; };

const BUS_SRC = read('static/js/uderiaBus.js');
const FILES = [...walk('static/js'), ...walk('components/builtin')].filter(p => !p.endsWith('uderiaBus.js'));
const EVENTS = UderiaBus.EVENTS;
const NAMES = Object.keys(EVENTS);

// ── 1. no raw dispatch / listen for bus names ──
console.log('[1. one target, through the bus]');
const rawDispatch = [], rawListen = [];
for (const f of FILES) {
    const t = read(f);
    for (const m of t.matchAll(/(document|window)\.dispatchEvent\(\s*new CustomEvent\(/g)) rawDispatch.push(`${f}:${t.slice(0, m.index).split('\n').length}`);
    for (const m of t.matchAll(/(document|window)\.(add|remove)EventListener\(\s*['"](uderia:|flow:|kg:)/g)) rawListen.push(`${f}:${t.slice(0, m.index).split('\n').length}`);
    for (const m of t.matchAll(/new CustomEvent\(\s*['"](flow:|kg:|openGenieSplitView|profile-modified|default-profile-changed|trust:changed|activeSessionTitleRenamed|canvas-split-mode-changed|scheduler-background-run)/g)) rawDispatch.push(`${f}:${t.slice(0, m.index).split('\n').length} (legacy name)`);
}
check('no document/window.dispatchEvent(new CustomEvent(…)) outside the bus', rawDispatch.length === 0, rawDispatch.join(', '));
check('no document/window listener for a bus name outside the bus', rawListen.length === 0, rawListen.join(', '));
check('the bus is a classic script loaded in <head> before every module and handler',
    /<script src="\/static\/js\/uderiaBus\.js\?v=[0-9.]+"><\/script>/.test(read('templates/index.html'))
    && read('templates/index.html').indexOf('uderiaBus.js') < read('templates/index.html').indexOf('type="module"'));
check('every name carries the one prefix', NAMES.every(n => /^uderia:[a-z0-9-]+$/.test(n)), NAMES.filter(n => !/^uderia:[a-z0-9-]+$/.test(n)).join(', '));

// ── 2 + 3. emitters and listeners, by name ──
console.log('[2. every name registered; 3. every name emitted and heard]');
const emitted = new Map(), listened = new Map();
const add = (map, name, where) => { if (!map.has(name)) map.set(name, []); map.get(name).push(where); };
for (const f of FILES) {
    const t = read(f);
    for (const m of t.matchAll(/UderiaBus\.emit\(\s*(['"])([^'"]+)\1/g)) add(emitted, m[2], f);
    for (const m of t.matchAll(/UderiaBus\.emit\(\s*([A-Z_]+)\b/g)) {   // a module constant (resourceSync)
        const c = t.match(new RegExp(`const ${m[1]} = '([^']+)'`)); if (c) add(emitted, c[1], f);
    }
    if (/UderiaBus\.announce\(/.test(t)) add(emitted, 'uderia:component-changed', f);
    for (const m of t.matchAll(/UderiaBus\.(on|once|off)\(\s*(['"])([^'"]+)\2/g)) add(listened, m[3], f);
    for (const m of t.matchAll(/UderiaBus\.on\(\s*name\b/g)) {           // PROFILE_CONFIG_EVENTS.forEach((name) => UderiaBus.on(name, …))
        for (const c of t.matchAll(/const [A-Z_]+ = '(uderia:[a-z-]+)'/g)) add(listened, c[1], f);
    }
    if (/UderiaBus\.onComponent\(/.test(t)) add(listened, 'uderia:component-changed', f);
}
const unregisteredE = [...emitted.keys()].filter(n => !EVENTS[n]);
const unregisteredL = [...listened.keys()].filter(n => !EVENTS[n]);
check('every emitted name is registered', unregisteredE.length === 0, unregisteredE.join(', '));
check('every listened name is registered', unregisteredL.length === 0, unregisteredL.join(', '));
const noEmitter = NAMES.filter(n => !emitted.has(n));
check('every registered name has an emitter', noEmitter.length === 0, noEmitter.join(', '));
const noListener = NAMES.filter(n => !EVENTS[n].public && !listened.has(n));
check('every registered name has a listener, unless it is a public hook', noListener.length === 0, noListener.join(', '));
check('a public hook is rare and named (panel state, turn complete)', NAMES.filter(n => EVENTS[n].public).length <= 3);
check('every registered name documents its detail fields and its fact', NAMES.every(n => Array.isArray(EVENTS[n].detail) && typeof EVENTS[n].note === 'string' && EVENTS[n].note.length > 10));

// ── 4. one change event ──
console.log('[4. one change event for every component]');
const N = read('static/js/notifications.js');
check('the server notice component_changed is relayed once, as uderia:component-changed with origin server',
    /case 'component_changed': \{[\s\S]{0,900}UderiaBus\.emit\('uderia:component-changed', \{[\s\S]{0,400}origin: 'server'/.test(N)
    && (N.match(/case 'component_changed'/g) || []).length === 1);
check('no files_changed relay remains (the Files component speaks the one notice)', !/case 'files_changed'/.test(N));
const privateChanged = NAMES.filter(n => /-changed$/.test(n) && !['uderia:component-changed', 'uderia:profile-config-changed', 'uderia:components-changed', 'uderia:default-profile-changed', 'uderia:canvas-split-mode-changed', 'uderia:profile-selection-changed', 'uderia:trust-changed'].includes(n));
check('no component keeps a private "…-changed" event (kg-authored, files, actions, products, explain-plans are gone)', privateChanged.length === 0, privateChanged.join(', '));
for (const gone of ['uderia:files-changed', 'uderia:kg-authored-changed', 'uderia:actions-changed', 'uderia:products-changed', 'uderia:explain-plans-changed', 'uderia:kg-created', 'flow:created', 'flow:panel-state']) {
    const still = FILES.filter(f => read(f).includes(`'${gone}'`));
    check(`'${gone}' is not spoken anywhere`, still.length === 0, still.join(', '));
}
const W = read('static/js/handlers/workspacePanelHandler.js');
check('the Workspace follows every component through ONE subscriber, coalesced', /UderiaBus\.onComponent\('\*', \(\) => \{\s*clearTimeout\(_wsChangeTimer\);/.test(W) && !/onComponent\('(files|knowledge_graph|action)'/.test(W));
check('the Python side has the one announcement, and files is its spelling',
    /def announce_component_change\(user_uuid: Optional\[str\], component_id: str, action: str,/.test(read('src/trusted_data_agent/core/ui_notify.py'))
    && /def announce_files_change\([\s\S]{0,400}announce_component_change\(user_uuid, "files", action, file_ids, session_id=session_id\)/.test(read('src/trusted_data_agent/core/ui_notify.py')));

// ── 5. manifests ──
console.log('[5. every component declares its signals, and they are true]');
const SCHEMA = JSON.parse(read('components/schemas/component_manifest_v1.schema.json'));
check('the manifest schema requires `signals` with emits · listens · announces · consumes_refs',
    (SCHEMA.required || []).includes('signals') && JSON.stringify(SCHEMA.properties.signals.required) === JSON.stringify(['emits', 'listens', 'announces', 'consumes_refs']));
const COMPONENT_IDS = [];
for (const d of fs.readdirSync('components/builtin')) {
    const mp = `components/builtin/${d}/manifest.json`;
    if (!fs.existsSync(mp)) continue;
    const m = JSON.parse(read(mp)); const id = m.component_id; COMPONENT_IDS.push(id);
    const sig = m.signals;
    check(`"${id}" declares signals`, sig && Array.isArray(sig.emits) && Array.isArray(sig.listens) && Array.isArray(sig.announces) && 'consumes_refs' in sig);
    if (!sig) continue;
    check(`"${id}" keeps conflicts_with inside signals, never at the root`, !('conflicts_with' in m));
    const rp = `components/builtin/${d}/renderer.js`;
    const t = fs.existsSync(rp) ? read(rp) : '';
    const e = new Set([...t.matchAll(/UderiaBus\.emit\('([^']+)'/g)].map(x => x[1])); if (/UderiaBus\.announce\(/.test(t)) e.add('uderia:component-changed');
    const l = new Set([...t.matchAll(/UderiaBus\.on\('([^']+)'/g)].map(x => x[1])); if (/UderiaBus\.onComponent\(/.test(t)) l.add('uderia:component-changed');
    check(`"${id}" emits exactly what it declares`, JSON.stringify([...e].sort()) === JSON.stringify([...sig.emits].sort()), `renderer ${[...e]} vs manifest ${sig.emits}`);
    check(`"${id}" listens exactly as it declares`, JSON.stringify([...l].sort()) === JSON.stringify([...sig.listens].sort()), `renderer ${[...l]} vs manifest ${sig.listens}`);
    check(`"${id}" declares every name it uses in the registry`, [...sig.emits, ...sig.listens].every(n => EVENTS[n]));
    if (m.instances && m.instances !== 'none' && m.instances !== 'page') {
        check(`"${id}" keeps things, so its store announces (signals.announces non-empty)`, sig.announces.length > 0);
    }
    if (m.instances === 'none') check(`"${id}" keeps nothing, so it announces nothing`, sig.announces.length === 0);
}
check('the four content takers read by reference: dashboard (implicit latest data), canvas, chart, files',
    (() => { const s = (id) => JSON.parse(read(`components/builtin/${id}/manifest.json`)).signals.consumes_refs;
        return JSON.stringify(s('dashboard')) === JSON.stringify({ implicit: 'latest_data' }) && s('canvas') === true && s('chart') === true && s('files') === true; })());
const CH = read('static/js/handlers/componentHandler.js');
check('the component card shows the declaration (Emits · Follows · Announces · Reads by reference)',
    /addRow\('Emits'/.test(CH) && /addRow\('Follows'/.test(CH) && /addRow\('Announces'/.test(CH) && /addRow\('Reads by reference'/.test(CH));
check('the component model serves it to the page', /"signals": _signals\.manifest_signals\(self\.manifest\)/.test(read('src/trusted_data_agent/components/models.py')));

// ── 6. the bus behaves ──
console.log('[6. the bus]');
{
    const got = [];
    const off = UderiaBus.on('uderia:turn-complete', (e) => got.push(e.detail));
    UderiaBus.emit('uderia:turn-complete', { session_id: 's1' });
    check('on() receives the DOM event, the payload in event.detail', got.length === 1 && got[0].session_id === 's1');
    off();
    UderiaBus.emit('uderia:turn-complete', { session_id: 's2' });
    check('the returned unsubscribe removes the listener', got.length === 1);
    const fn = () => got.push('x');
    UderiaBus.on('uderia:turn-complete', fn); UderiaBus.off('uderia:turn-complete', fn);
    UderiaBus.emit('uderia:turn-complete', {});
    check('off(name, fn) removes the handler registered as-is', got.length === 1);
    const files = [], all = [];
    const offF = UderiaBus.onComponent('files', (e) => files.push(e.detail.action));
    const offA = UderiaBus.onComponent('*', (e) => all.push(e.detail.component_id));
    UderiaBus.announce('files', 'kept', ['f1']);
    UderiaBus.announce('flow', 'created', ['fl1']);
    check('onComponent filters by component; "*" hears every component', JSON.stringify(files) === '["kept"]' && JSON.stringify(all) === '["files","flow"]');
    const echo = UderiaBus.emit('uderia:component-changed', { component_id: 'files', action: 'kept', ids: ['f1'], origin: 'server' });
    check('the server echo of the page\'s own announcement within DEDUPE_MS is swallowed', echo === false && files.length === 1);
    const again = UderiaBus.announce('files', 'kept', ['f1']);
    check('…a second announcement from the SAME origin is a second fact (page→page, server→server both pass)', again === true && files.length === 2);
    const other = UderiaBus.announce('files', 'kept', ['f2']);
    check('…a different change is not deduped', other === true && files.length === 3);
    UderiaBus.emit('uderia:component-changed', { component_id: 'files', action: 'changed', ids: [], origin: 'server' });
    const idless = UderiaBus.announce('files', 'changed');
    check('…an id-less announcement is never deduped (nothing names it as the same fact)', idless === true && files.length === 5);
    check('a page announcement carries origin page and the full shape', (() => { let d; const o = UderiaBus.onComponent('memory', (e) => { d = e.detail; }); UderiaBus.announce('memory', 'deleted'); o();
        return d && d.origin === 'page' && Array.isArray(d.ids) && 'aspect' in d && 'session_id' in d && 'profile_id' in d; })());
    offF(); offA();
    const warned = []; const orig = console.warn; console.warn = (m) => warned.push(m);
    let heard = false; UderiaBus.on('uderia:not-a-thing', () => { heard = true; }); UderiaBus.emit('uderia:not-a-thing', {});
    console.warn = orig;
    check('an unregistered name warns once per name and still delivers (never breaks a page)', heard && warned.length === 1 && /unregistered event/.test(warned[0]));
    check('EVENTS is frozen', Object.isFrozen(UderiaBus.EVENTS) && Object.isFrozen(UderiaBus));
    check('the bus resolves document per call (a harness may install its stub after the script loads)', /const target = \(\) => document;/.test(BUS_SRC) && !/const TARGET = document/.test(BUS_SRC));
    check('the test shim is the real bus, not a copy', /uderiaBus\.js/.test(read('test/_uderia_bus.mjs')) && /new Function\(src\)\(\)/.test(read('test/_uderia_bus.mjs')));
}


// ── 7. own or elsewhere ──
// A tab's writes come back as notices like everyone else's. CLIENT_ID (stamped on every same-origin
// fetch by auth.js, carried on the notice, read by the bus) marks the echo of THIS page's own write
// `own`, so an editor can follow a write made elsewhere without overwriting what is being typed —
// the rule that replaced "ignore every content notice" in the canvas, the flow and the dashboard.
console.log('[7. own or elsewhere]');
{
    check('CLIENT_ID is a non-empty string on the frozen bus', typeof UderiaBus.CLIENT_ID === 'string' && UderiaBus.CLIENT_ID.length >= 8);
    check('the change event declares client and own', EVENTS['uderia:component-changed'].detail.includes('client') && EVENTS['uderia:component-changed'].detail.includes('own'));
    const seen = [];
    const off = UderiaBus.onComponent('probe7', (e) => seen.push(e.detail.own));
    UderiaBus.announce('probe7', 'updated', ['a']);
    UderiaBus.emit('uderia:component-changed', { component_id: 'probe7', action: 'updated', ids: ['b'], origin: 'server', client: UderiaBus.CLIENT_ID });
    UderiaBus.emit('uderia:component-changed', { component_id: 'probe7', action: 'updated', ids: ['c'], origin: 'server', client: 'another-tab' });
    UderiaBus.emit('uderia:component-changed', { component_id: 'probe7', action: 'updated', ids: ['d'], origin: 'server' });
    off();
    check('own: the page\'s announcement and the echo of this tab\'s write — not another tab\'s, not a client-less write (REST, a scheduled run)', JSON.stringify(seen) === '[true,true,false,false]', JSON.stringify(seen));
    const auth = read('static/js/auth.js'), nf = read('static/js/notifications.js');
    check('auth.js: the ONE fetch wrapper stamps X-Uderia-Client from UderiaBus.CLIENT_ID, same-origin requests only',
        (auth.match(/window\.fetch = async function/g) || []).length === 1 && /await _fetch\(input, _stampClient\(input, init\)\)/.test(auth)
        && /headers\.set\('X-Uderia-Client', id\)/.test(auth) && /url\.startsWith\('\/'\)/.test(auth));
    check('auth.js: a Request object keeps its own headers plus the stamp', /input instanceof Request \? input\.headers/.test(auth));
    check('notifications.js relays the client with the notice', /client: cc\.client \|\| null/.test(nf));
    check('the bus is the one place own is decided', /d\.own = \(d\.origin \|\| 'page'\) === 'page' \|\| \(!!d\.client && d\.client === CLIENT_ID\);/.test(BUS_SRC) && !/\.own\s*=/.test(nf));

    const cv = read('components/builtin/canvas/renderer.js'), fl = read('components/builtin/flow/renderer.js'), db = read('components/builtin/dashboard/renderer.js');
    const cvFollow = cv.slice(cv.indexOf("UderiaBus.onComponent('canvas'"), cv.indexOf('async function _reloadCanvasFromStore'));
    check('canvas: its own save is not a change (d.own); a deletion closes; an update or restore elsewhere is followed',
        /if \(d\.own \|\| \(d\.action !== 'updated' && d\.action !== 'restored'\)\) return;/.test(cvFollow) && /_canvasChangedElsewhere\(_openCanvasId\)/.test(cvFollow));
    check('canvas: nothing typed → reload in place; an edit pending → "Changed elsewhere" · Reload · Keep mine, never an overwrite',
        /const editing = !!\(pending && \(pending\.timer \|\| pending\.held\)\);\s*if \(!editing\) \{ _reloadCanvasFromStore\(canvasId\); return; \}/.test(cv)
        && /showSplitPanelNotice\('canvas-split-panel', 'Changed elsewhere while you were editing', \{\s*actions: \[\s*\{ label: 'Reload', onAction: \(\) => _reloadCanvasFromStore\(canvasId\) \},\s*\{ label: 'Keep mine', onAction: \(\) => _keepMineCanvas\(canvasId\) \},/.test(cv));
    check('canvas: the pending autosave is HELD while the user decides (it would overwrite the write elsewhere first) — nothing scheduled on a held canvas, Keep mine releases and sends, leaving keeps mine',
        /if \(pending\.timer\) clearTimeout\(pending\.timer\);\s*_canvasSaveTimers\.set\(canvasId, \{ \.\.\.pending, timer: null, held: true \}\);\s*_canvasHeld\.add\(canvasId\);/.test(cv)
        && /if \(_canvasHeld\.has\(state\.canvasId\)\) \{\s*_canvasSaveTimers\.set\(state\.canvasId, \{ timer: null, state, dirtyVersion: true, held: true \}\);\s*return;\s*\}\s*const timer = setTimeout/.test(cv)
        && /async function _keepMineCanvas\(canvasId\) \{\s*_canvasHeld\.delete\(canvasId\);[\s\S]{0,160}await _flushCanvasSave\(canvasId\);/.test(cv)
        && /_canvasSaveTimers\.delete\(canvasId\);\s*_canvasHeld\.delete\(canvasId\);   \/\/ leaving a held canvas keeps mine/.test(cv));
    check('canvas: a reload drops the superseded pending save BEFORE re-reading (flushing it would overwrite the write elsewhere)',
        /async function _reloadCanvasFromStore\(canvasId\) \{\s*_cancelCanvasSave\(canvasId\);[\s\S]{0,220}await _switchCanvasInPanel\(canvasId\);/.test(cv));
    check('canvas: the notice leaves with the panel, with a switch, with a reload', (cv.match(/hideSplitPanelNotice\(/g) || []).length >= 3);
    const flFollow = fl.slice(fl.indexOf("UderiaBus.onComponent('flow'"), fl.indexOf('export function closeFlowSplitPanel'));
    check('flow: own, non-update and version notices pass; an update elsewhere offers Reload and never redraws the canvas by itself',
        /if \(d\.own \|\| d\.action !== 'updated' \|\| d\.aspect\) return;/.test(flFollow) && /onAction: \(\) => _openFlowById\(open\)/.test(flFollow) && !/^\s*_openFlowById\(open\);/m.test(flFollow));
    check('flow: the notice leaves when the panel opens another flow or closes', (fl.match(/hideSplitPanelNotice\(panel\)/g) || []).length >= 2);
    const dbFollow = db.slice(db.indexOf("UderiaBus.onComponent('dashboard'"), db.indexOf('async function _loadAndRenderDashboard'));
    check('dashboard: a refresh START is not a reload; a layout saved HERE is not; a layout saved elsewhere is applied',
        /if \(d\.action === 'refreshed'\) return;/.test(dbFollow) && /if \(d\.aspect === 'layout' && d\.own\) return;/.test(dbFollow) && !/d\.aspect === 'layout'\) return/.test(dbFollow));

    const chrome = read('static/js/handlers/splitPanelChrome.js'), css = read('static/css/split-panels.css'), html = read('templates/index.html');
    check('ONE panel notice — splitPanelChrome.showSplitPanelNotice / hideSplitPanelNotice, on window for renderers; one act or several',
        /export function showSplitPanelNotice\(panel, text, \{ actionLabel, onAction, actions \} = \{\}\)/.test(chrome) && /for \(const a of acts\)/.test(chrome)
        && /window\.showSplitPanelNotice = showSplitPanelNotice;/.test(chrome) && /window\.hideSplitPanelNotice = hideSplitPanelNotice;/.test(chrome));
    check('one line per panel (show hides first), under the .sp-header, with the act and a dismiss',
        /hideSplitPanelNotice\(el\);\s*const line = document\.createElement\('div'\);/.test(chrome) && /header\.insertAdjacentElement\('afterend', line\)/.test(chrome)
        && /sp-notice-action/.test(chrome) && /sp-notice-dismiss/.test(chrome) && /aria-label', 'Dismiss'/.test(chrome));
    check('no renderer builds its own notice line', !/sp-notice/.test(cv) && !/sp-notice/.test(fl) && !/sp-notice/.test(db));
    const noticeCss = css.slice(css.indexOf('.sp-notice {'), css.indexOf('/* ── Shared switcher dropdown menu'));
    check('.sp-notice is themed by variables (text, bg, border, accent) — no Tailwind colour class, no emoji',
        /var\(--text-primary/.test(noticeCss) && /var\(--bg-secondary/.test(noticeCss) && /var\(--border-secondary/.test(noticeCss) && /var\(--teradata-orange/.test(noticeCss)
        && !/[\u{1F300}-\u{1FAFF}]/u.test(chrome));
    check('pins: uderiaBus 1.2 · auth 1.3 · split-panels 1.14 (a content change bumps its pin)',
        /uderiaBus\.js\?v=1\.2"/.test(html) && /auth\.js\?v=1\.3"/.test(html) && /split-panels\.css'\) \}\}\?v=1\.14"/.test(html));
}

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILED'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
