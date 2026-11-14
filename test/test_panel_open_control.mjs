/**
 * The open-in-panel control gate — the ONE button every component card uses to open its
 * side panel (static/js/handlers/panelOpenControl.js).
 *
 *  [1] the rule, executed: a button reads "Expanded in side panel" exactly when the panel
 *      shows ITS item; single-panel components ('*') match any open panel; the panel's
 *      report re-derives every button, so two cards can never both read "Expanded".
 *  [2] the click, executed: open when this card's item is not shown (incl. switching from
 *      another item), close only when it is.
 *  [3] accessibility: a real <button> with aria-expanded, aria-controls and an aria-label
 *      that names the item; the label text follows the state, the icon is kept.
 *  [4] every component card uses it — and none keeps its own: the nine renderers render
 *      the shared markup, register open/close, and report what the panel shows when it
 *      opens and closes; none of the old per-component buttons survives.
 *  [6] a row's other acts ("Pin to dashboard") join the card's button row as a secondary
 *      pill of the same family; the emptied footer is removed; rows without the control keep it.
 *  [5] the server-rendered flow-run card emits the shared markup (executed), escaped;
 *      a card persisted before the control existed is upgraded on insertion (ui.js).
 *
 * No server, no network.
 */
import './_uderia_bus.mjs';
import { readFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { installDom } from './dom_stub.mjs';

let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
    if (cond) { pass++; console.log(`  ok  ${name}`); }
    else { fail++; console.log(`  FAIL  ${name}${detail ? ' — ' + String(detail).slice(0, 240) : ''}`); }
};

const { doc } = installDom();
const POC = (await import('../static/js/handlers/panelOpenControl.js')).default;

function mount(html) {
    const host = doc.createElement('div');
    host.innerHTML = html;
    doc.body.appendChild(host);
    return host.querySelector('.sp-open-ctl');
}
const label = (b) => (b.querySelector('.sp-open-ctl-label') || {}).textContent;

console.log('\n[1] the rule');
{
    const a = mount(POC.html({ component: 'canvas', id: 'c1', accent: '#06b6d4', panelId: 'canvas-split-panel', name: 'Report A' }));
    const b = mount(POC.html({ component: 'canvas', id: 'c2', accent: '#06b6d4', panelId: 'canvas-split-panel', name: 'Report B' }));
    const f = mount(POC.html({ component: 'flow', id: 'c1', panelId: 'flow-split-panel' }));
    check('closed panel: every card reads View in Canvas', label(a) === 'View in Canvas' && label(b) === 'View in Canvas');
    POC.shown('canvas', 'c1');
    check('the panel shows c1: only c1\'s card reads Expanded', label(a) === 'Expanded in side panel' && label(b) === 'View in Canvas');
    check('…another component\'s card with the same id is untouched', label(f) === 'View in Canvas');
    POC.shown('canvas', 'c2');
    check('switching item moves the mark — never two Expanded cards', label(a) === 'View in Canvas' && label(b) === 'Expanded in side panel');
    POC.shown('canvas', null);
    check('closing resets every card', label(a) === 'View in Canvas' && label(b) === 'View in Canvas');
    check('a card rendered while its item is open renders Expanded at once', (() => {
        POC.shown('canvas', 'c1');
        const c = mount(POC.html({ component: 'canvas', id: 'c1' }));
        const ok = label(c) === 'Expanded in side panel' && c.getAttribute('aria-expanded') === 'true';
        POC.shown('canvas', null);
        return ok;
    })());
    const s1 = mount(POC.html({ component: 'scheduler', id: '*' }));
    const s2 = mount(POC.html({ component: 'scheduler' }));
    POC.shown('scheduler', '*');
    check('a single-panel component (\'*\'): every card reads Expanded while the panel is open', label(s1) === 'Expanded in side panel' && label(s2) === 'Expanded in side panel');
    POC.shown('scheduler', null);
    check('…and none once it closes', label(s1) === 'View in Canvas' && label(s2) === 'View in Canvas');
    check('isExpanded truth table', !POC.isExpanded('kg', 'x') && (POC.shown('kg', 'x'), POC.isExpanded('kg', 'x'))
        && !POC.isExpanded('kg', 'y') && POC.isExpanded('kg', '*') && (POC.shown('kg', null), !POC.isExpanded('kg', '*')));
    let evt = null;
    doc.addEventListener('uderia:panel-state', (e) => { evt = e.detail; });
    POC.shown('memory', '*');
    check('a report is announced (uderia:panel-state) for anything else that follows the panel', evt && evt.component === 'memory' && evt.id === '*');
    POC.shown('memory', null);
    window.__componentAccents = { dashboard: '#abcdef' };
    const d = POC.html({ component: 'dashboard', id: 'd', accent: '#ec4899' });
    check('the live manifest accent wins over the renderer\'s fallback', d.includes('--sp-accent:#abcdef'));
    delete window.__componentAccents;
    check('…the fallback applies without it', POC.html({ component: 'dashboard', id: 'd', accent: '#ec4899' }).includes('--sp-accent:#ec4899'));
}

console.log('\n[2] the click');
{
    const calls = [];
    POC.register('flow', { open: (id) => { calls.push(['open', id]); POC.shown('flow', id); },
                            close: (id) => { calls.push(['close', id]); POC.shown('flow', null); } });
    const x = mount(POC.html({ component: 'flow', id: 'fx' }));
    const y = mount(POC.html({ component: 'flow', id: 'fy' }));
    await POC._onClick(x);
    check('a closed panel: the click opens this card\'s item', JSON.stringify(calls.at(-1)) === '["open","fx"]' && label(x) === 'Expanded in side panel');
    await POC._onClick(y);
    check('the panel shows another item: the click OPENS this one (it never closes another card\'s item)', JSON.stringify(calls.at(-1)) === '["open","fy"]'
        && label(y) === 'Expanded in side panel' && label(x) === 'View in Canvas');
    await POC._onClick(y);
    check('the panel shows this item: the click closes it', JSON.stringify(calls.at(-1)) === '["close","fy"]' && label(y) === 'View in Canvas');
    // a renderer that registered through the queue (it may load before this module, as the
    // component renderers do) is found on the first click — the defect the browser caught
    let queued = null;
    (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['latecomer', { open: (id) => { queued = id; }, close() {} }]);
    await POC._onClick(mount(POC.html({ component: 'latecomer', id: 'q1' })));
    check('a handler queued by a renderer is used on the first click', queued === 'q1', queued);
    const del = readFileSync('static/js/handlers/panelOpenControl.js', 'utf8');
    check('clicks are delegated from document (static HTML works without wiring)', /document\.addEventListener\('click'/.test(del) && /closest\('\.sp-open-ctl'\)/.test(del));
    check('a card whose renderer is not loaded imports it first', /import\(`\/api\/v1\/components\/\$\{component\}\/renderer`\)/.test(del));
}

console.log('\n[3] accessibility');
{
    const b = mount(POC.html({ component: 'canvas', id: 'acc', panelId: 'canvas-split-panel', name: 'Sales report' }));
    check('a real button', b.tagName === 'BUTTON' && b.getAttribute('type') === 'button');
    check('aria-controls names the panel', b.getAttribute('aria-controls') === 'canvas-split-panel');
    check('aria-expanded and aria-label follow the state, naming the item',
        b.getAttribute('aria-expanded') === 'false' && b.getAttribute('aria-label') === 'Open Sales report in the side panel'
        && (POC.shown('canvas', 'acc'), b.getAttribute('aria-expanded') === 'true' && b.getAttribute('aria-label') === 'Close Sales report side panel'));
    POC.shown('canvas', null);
    check('the icon survives a state change (only the label text is rewritten)', b.querySelectorAll('svg').length === 1);
    const css = readFileSync('static/css/split-panels.css', 'utf8');
    check('a visible keyboard focus ring', /\.sp-open-ctl:focus-visible\s*\{[^}]*outline:/.test(css));
    check('motion respects reduced-motion', /prefers-reduced-motion[\s\S]*sp-open-ctl/.test(css));
    check('theme-aware: colours come from the component accent, no fixed text colour', /\.sp-open-ctl \{[^}]*color: var\(--sp-open-accent\)/.test(css));
}

console.log('\n[4] every component card uses it');
const CARDS = {
    canvas: 'components/builtin/canvas/renderer.js',
    flow: 'components/builtin/flow/renderer.js',
    dashboard: 'components/builtin/dashboard/renderer.js',
    knowledge_graph: 'components/builtin/knowledge_graph/renderer.js',
    scheduler: 'components/builtin/scheduler/renderer.js',
    memory: 'components/builtin/memory/renderer.js',
    database_navigator: 'components/builtin/database_navigator/renderer.js',
    explain_plan: 'components/builtin/explain_plan/renderer.js',
};
const OLD = ['canvas-inline-card-action', 'flow-inline-open-btn', 'kg-inline-open-btn', 'sched-open-panel-btn',
             'mem-open-panel-btn', 'dbnav-open-btn', 'xplan-card-action', 'dash-inline-open-btn', 'View in Navigator'];
for (const [comp, file] of Object.entries(CARDS)) {
    const src = readFileSync(file, 'utf8');
    check(`${comp}: renders the shared control`, new RegExp(`(?:PanelOpenControl|poc)\\.html\\(\\{\\s*component: '${comp}'`).test(src));
    check(`${comp}: registers its open and close through the queue (load order never matters)`,
        new RegExp(`__panelOpenQueue \\|\\| \\[\\]\\)\\.push\\(\\['${comp}'`).test(src) && /PanelOpenControl\.drain\(\)/.test(src)
        && !new RegExp(`PanelOpenControl\\.register\\('${comp}'`).test(src));
    const reports = src.match(new RegExp(`shown\\('${comp}'`, 'g')) || [];
    check(`${comp}: the panel reports what it shows`, reports.length >= 1, reports.length);
    const left = OLD.filter(o => src.includes(o));
    check(`${comp}: no per-component open button left`, left.length === 0, left.join(', '));
    check(`${comp}: no label of its own`, !/['"`]Expanded in side panel \\?(?:u2192|→)/.test(src));
}
{
    // each renderer reports "closed" from its close function
    // comments stripped: prose about a call is not the call
    const code = (file) => readFileSync(file, 'utf8').replace(/^\s*\/\/.*$/mg, '').replace(/([;{}),])\s*\/\/.*$/mg, '$1');
    const closeOf = (file, name) => { const s = code(file); const i = s.indexOf(name); return i < 0 ? '' : s.slice(i, i + 2600); };
    const closes = [
        ['canvas', 'export function closeSplitPanel()', /_reportShown\(null\)/],
        ['flow', 'export function closeFlowSplitPanel()', /_reportFlowShown\(null\)/],
        ['dashboard', 'export function closeDashboardPanel(', /_reportDashShown\(null\)/],
        ['knowledge_graph', 'export function closeKGSplitPanel()', /_reportKGShown\(null\)/],
        ['scheduler', 'export function closeSchedulerSplitPanel()', /_reportSchedShown\(false\)/],
        ['memory', 'export function closeMemorySplitPanel()', /_syncMemCards\(false\)/],
        ['database_navigator', 'export function closeDatabaseNavigatorPanel()', /_syncCards\(false\)/],
        ['explain_plan', 'export function closeExplainPlanPanel()', /_syncPlanCards\(\)/],
    ];
    for (const [comp, fn, re] of closes) check(`${comp}: closing the panel reports it`, re.test(closeOf(CARDS[comp], fn)));
    const opens = [
        ['canvas', 'async function autoPopOutCanvas(spec)', /_reportShown\(_canvasKey\(spec\)\)/, 9000],
        ['flow', 'export function openFlowSplitPanel(spec)', /_reportFlowShown\(_openFlowId/, 3000],
        ['dashboard', 'export function openDashboardPanel(spec)', /_reportDashShown\(_currentDashboardId/, 1200],
        ['knowledge_graph', 'export function openKGSplitPanel(spec)', /_reportKGShown\(_kgKey\(spec\)\)/, 1500],
        ['scheduler', 'export function openSchedulerSplitPanel(spec)', /_reportSchedShown\(true\)/, 1500],
        ['memory', 'export async function openMemorySplitPanel(spec)', /_syncMemCards\(true\)/, 1500],
        ['database_navigator', 'export async function openDatabaseNavigatorPanel(', /_syncCards\(true\)/, 2500],
        ['explain_plan', 'export function openExplainPlanPanel(spec)', /_syncPlanCards\(\)/, 1500],
    ];
    for (const [comp, fn, re, n] of opens) {
        const s = code(CARDS[comp]); const i = s.indexOf(fn);
        check(`${comp}: opening the panel reports it`, i >= 0 && re.test(s.slice(i, i + n)));
    }
    // auto-open never fires while an old answer is replayed
    for (const comp of ['memory', 'scheduler', 'database_navigator', 'dashboard', 'knowledge_graph', 'canvas']) {
        check(`${comp}: auto-open is held back during session replay`, readFileSync(CARDS[comp], 'utf8').includes('__suppressComponentReplayOpen'));
    }
    const nav = readFileSync(CARDS.database_navigator, 'utf8');
    check('navigator: "Open in canvas" is its own button — never relabelled by the panel state', /class="dbnav-canvas-btn/.test(nav) && /Open in canvas →/.test(nav));
    check('the control is loaded by main.js', /import '\.\/handlers\/panelOpenControl\.js\?v=/.test(readFileSync('static/js/main.js', 'utf8')));
}

console.log('\n[5] the flow-run card');
{
    const html = execSync(`python3 -c "
import sys; sys.path.insert(0,'src')
from trusted_data_agent.core.flow_executor import _render_flow_run_card_html as r
print(r('Nightly <ETL>', 'completed', 'run-1', 'flow-9', 10, 20, 0.0012))
"`, { encoding: 'utf8' });
    check('the server emits the shared control', html.includes('class="sp-open-ctl"') && html.includes('data-sp-open="flow"') && html.includes('data-sp-open-id="flow-9"')
        && html.includes('aria-expanded="false"') && html.includes('aria-controls="flow-split-panel"') && html.includes('sp-open-ctl-label">View in Canvas'));
    check('…the flow name is escaped everywhere it appears', !html.includes('<ETL>') && html.includes('Nightly &lt;ETL&gt;'));
    check('…and no button of its own', !html.includes('flow-run-open-btn'));
    const ui = readFileSync('static/js/ui.js', 'utf8');
    check('a card persisted before the control existed is upgraded on insertion', /function _upgradeLegacyOpenButtons/.test(ui) && /querySelectorAll\('\.flow-run-open-btn'\)/.test(ui) && /btn\.replaceWith\(ctl\)/.test(ui));
    check('ui.js keeps no click handler or label sync of its own for that card', !/_syncFlowRunCardButtons|__flowRunCardOpenBound/.test(ui));
    check('the flow-run trace in Live Status opens the flow through the same control (no private opener)',
        /renderFlowRunTrace[\s\S]{0,1500}PanelOpenControl\.html\(\{ component: 'flow'/.test(ui) && !/_openFlowCanvasFromTrace/.test(ui));
    check('a run that is no longer stored says so, and offers no control for it',
        /runMissing \? `<div[^`]*no longer stored/.test(ui) && /flowId && !runMissing && window\.PanelOpenControl/.test(ui)
        && /else if \(resp\.status === 404\) runMissing = true;/.test(readFileSync('static/js/eventHandlers.js', 'utf8')));
    const fl = readFileSync('components/builtin/flow/renderer.js', 'utf8');
    const openFn = fl.slice(fl.indexOf("push(['flow', {"), fl.indexOf("push(['flow', {") + 1400);
    check('a deleted flow is never opened from a stale snapshot: the user is told, nothing opens',
        /if \(r\.status === 404\) \{[\s\S]*?showAppBanner\([^)]*no longer exists[\s\S]*?return;/.test(openFn)
        && openFn.indexOf('r.status === 404') < openFn.indexOf('if (spec) openFlowSplitPanel(spec)'));
}

const CSS6 = readFileSync('static/css/split-panels.css', 'utf8');
console.log('\n[6] a row\'s acts sit together — "Pin to dashboard" joins the card\'s button row');
{
    const row = (withCtl) => {
        const host = doc.createElement('div');
        host.innerHTML = `<details class="ans-ev" data-component="explain_plan"><summary>Plan</summary><div class="ans-ev-b">`
            + (withCtl ? `<div class="card">${POC.html({ component: 'explain_plan', id: 'p1' })}</div>` : '<div class="card"></div>')
            + `<div class="ans-tf"><div class="ans-tf-l"></div><div class="ans-tf-r"><button type="button" class="ans-act ans-pin" data-pin-component="explain_plan" hidden>Pin to dashboard</button></div></div>`
            + `</div></details>`;
        doc.body.appendChild(host);
        return host;
    };
    const h = row(true);
    POC.adoptRowActs(h);
    const pin = h.querySelector('.ans-pin'), ctl = h.querySelector('.sp-open-ctl');
    check('the pin moves onto the card\'s button row, immediately left of the open control',
        pin && pin.parentElement === ctl.parentElement && pin.nextElementSibling === ctl);
    check('the emptied footer is removed — no extra row', !h.querySelector('.ans-tf'));
    check('it takes the secondary pill of the same family and the pin icon', pin.classList.contains('sp-row-act')
        && pin.classList.contains('ans-pin') && !!pin.querySelector('.sp-open-ctl-icon'));
    check('it stays hidden until the Dashboard gate reveals it (state survives the move)', pin.hidden === true);
    POC.adoptRowActs(h);
    check('idempotent: a second pass changes nothing (one icon, one pin)',
        h.querySelectorAll('.ans-pin').length === 1 && pin.querySelectorAll('.sp-open-ctl-icon').length === 1);
    check('…and it sheds the footer text-link class, so the footer\'s style cannot win', !pin.classList.contains('ans-act'));
    check('both pills outrank the global themes.css button rules (0,1,1), so neither is squared',
        /\nbutton\.sp-open-ctl\.sp-open-ctl \{[^}]*border-radius: 999px/.test(CSS6) && /\nbutton\.sp-row-act\.sp-row-act \{[^}]*border-radius: 999px/.test(CSS6));
    const c = row(false);
    POC.adoptRowActs(c);
    check('a row with no open control (a chart, a table) keeps its footer and its text-link act',
        !!c.querySelector('.ans-pin') && !!c.querySelector('.ans-pin').closest('.ans-tf') && !c.querySelector('.sp-row-act'));
    const css = readFileSync('static/css/split-panels.css', 'utf8');
    const rule = (sel) => (css.match(new RegExp('\\n' + sel.replace(/[.\[\]]/g, '\\$&') + ' \\{([^}]*)\\}')) || [])[1] || '';
    const ctlRule = rule('button.sp-open-ctl.sp-open-ctl'), actRule = rule('button.sp-row-act.sp-row-act');
    const same = ['padding', 'border-radius', 'font-size', 'font-weight', 'line-height', 'gap'].every(k => {
        const v = (r) => (r.match(new RegExp('\\b' + k + ':\\s*([^;]+);')) || [])[1];
        return v(ctlRule) && v(ctlRule) === v(actRule);
    });
    check('look and feel: the secondary pill shares the open control\'s metrics (padding, radius, type, gap)', same, actRule);
    check('…uses theme variables only — neutral until hovered, accent on hover and focus',
        /color: var\(--text-muted/.test(actRule) && /border: 1px solid var\(--border-primary/.test(actRule)
        && /\.sp-row-act:hover \{[^}]*var\(--sp-open-accent\)/.test(css) && /\.sp-row-act:focus-visible \{[^}]*outline: 2px solid/.test(css));
    check('…and its inline-flex never overrides [hidden]', /button\.sp-row-act\[hidden\] \{ display: none; \}/.test(css));
    const ui = readFileSync('static/js/ui.js', 'utf8');
    check('a card drawn before the acts were wired still takes them (ui.js calls the adoption after wiring)',
        /function _wirePinActs[\s\S]{0,2500}PanelOpenControl\.adoptRowActs\(root\)/.test(ui));
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) { console.log(`FAILURES — ${fail} of ${pass + fail}`); process.exit(1); }
console.log(`ALL PASS — ${pass} assertions (one open control for every component card)`);
