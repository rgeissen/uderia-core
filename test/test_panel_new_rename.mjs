// New + Rename in the shared split-panel switcher, the canvas's autosave rule, the canvas
// Explain target, and the navigator revealing what the canvas asked it to describe.
// Executes the shipped modules under the shared DOM stub (no browser, no network).
import { installDom, El } from './dom_stub.mjs';
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (name, cond, detail = '') => { if (cond) pass++; else { fail++; console.log('FAIL:', name, detail); } };

const { doc } = installDom();
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
El.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, right: 100, bottom: 20, width: 100, height: 20 }; };
globalThis.window.innerWidth = 1200; globalThis.window.innerHeight = 800;
globalThis.window.addEventListener = () => {}; globalThis.window.removeEventListener = () => {};
doc.removeEventListener = () => {};

const { mountSplitPanelSwitcher } = await import('../static/js/handlers/splitPanelChrome.js');
const tick = () => new Promise(r => setImmediate(r));

function makeBtn() {
    const b = new El('button', { class: 'sp-switcher' });
    b.innerHTML = '<span class="sp-switcher-name">Alpha</span>';
    doc.body.appendChild(b);
    return b;
}
const menuEl = () => doc.body.children.find(c => c.classList.contains('sp-switcher-menu')) || null;
const lines = () => (menuEl() ? menuEl().querySelectorAll('.sp-switcher-new') : []);
async function openMenu(btn) { btn.click(); await tick(); await tick(); }
async function typeAndSubmit(line, value) {
    const input = line.querySelector('input'); input.value = value;
    line.querySelector('.sp-switcher-ok').click(); await tick(); await tick();
}

// ── 1. Rename: prefilled with the open instance, refusal keeps the field, success closes ──
{
    const btn = makeBtn();
    const calls = [];
    let answer = 'A canvas named “Beta” already exists';
    mountSplitPanelSwitcher({
        btnEl: btn, items: () => [{ id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta' }],
        idField: 'id', labelField: 'name', getCurrentId: () => 'a', onSelect: () => {},
        onRename: async (id, name) => { calls.push([id, name]); return answer; },
    });
    await openMenu(btn);
    const rn = lines().find(l => l.classList.contains('sp-switcher-rename'));
    check('rename line is offered for the open instance', !!rn && /Alpha/.test(rn.textContent));
    rn.click(); await tick();
    const input = rn.querySelector('input');
    check('rename field is prefilled with the current name', input && input.value === 'Alpha');
    await typeAndSubmit(rn, 'Beta');
    check('onRename got the id and the new name', calls.length === 1 && calls[0][0] === 'a' && calls[0][1] === 'Beta');
    check('a refusal keeps the menu open', !!menuEl());
    const err = rn.querySelector('.sp-switcher-err');
    check('a refusal is shown under the field', err && /already exists/.test(err.textContent));
    check('the refusal is announced', err && err.getAttribute('role') === 'alert');
    check('the field is marked invalid', rn.querySelector('input').classList.contains('is-invalid'));
    answer = true;
    await typeAndSubmit(rn, 'Gamma');
    check('success closes the menu', !menuEl());
    check('success updates the title in place', btn.querySelector('.sp-switcher-name').textContent === 'Gamma');
    // unchanged name: no call
    await openMenu(btn);
    const rn2 = lines().find(l => l.classList.contains('sp-switcher-rename'));
    rn2.click(); await tick(); calls.length = 0;
    await typeAndSubmit(rn2, 'Alpha');
    check('renaming to the same name makes no call', calls.length === 0 && !menuEl());
}

// ── 2. No rename line when nothing is open ──
{
    const btn = makeBtn();
    mountSplitPanelSwitcher({ btnEl: btn, items: () => [{ id: 'a', name: 'Alpha' }], idField: 'id', labelField: 'name',
        getCurrentId: () => null, onSelect: () => {}, onRename: async () => true });
    await openMenu(btn);
    check('no rename line without an open instance', !lines().some(l => l.classList.contains('sp-switcher-rename')));
    menuEl() && menuEl().remove();
}

// ── 3. New with a name: empty refused locally, refusal shown, success closes ──
{
    const btn = makeBtn();
    const made = [];
    let answer = 'Could not create the flow';
    mountSplitPanelSwitcher({ btnEl: btn, items: () => [], idField: 'id', labelField: 'name', getCurrentId: () => null,
        onSelect: () => {}, newLabel: 'New flow', newPrompt: 'Name of the new flow',
        onNew: async (name) => { made.push(name); return answer; } });
    await openMenu(btn);
    const nw = lines().find(l => !l.classList.contains('sp-switcher-rename'));
    nw.click(); await tick();
    await typeAndSubmit(nw, '   ');
    check('an empty name is not sent', made.length === 0);
    check('an empty name marks the field', nw.querySelector('input').classList.contains('is-invalid'));
    await typeAndSubmit(nw, ' Nightly ETL ');
    check('the name is trimmed', made[0] === 'Nightly ETL');
    check('a refusal keeps the field open with the reason', !!menuEl() && /Could not create/.test((nw.querySelector('.sp-switcher-err') || {}).textContent || ''));
    answer = true;
    await typeAndSubmit(nw, 'Nightly ETL');
    check('success closes the menu', !menuEl());
}

// ── 4. New without a prompt routes at once (the KG constructors) ──
{
    const btn = makeBtn();
    let routed = 0;
    mountSplitPanelSwitcher({ btnEl: btn, items: () => [], idField: 'id', labelField: 'name', getCurrentId: () => null,
        onSelect: () => {}, newLabel: 'New knowledge graph…', onNew: () => { routed++; } });
    await openMenu(btn);
    lines()[0].click(); await tick();
    check('New without a prompt calls onNew at once and closes', routed === 1 && !menuEl());
}

// ── 5. Canvas: what counts as the user's edit; what Explain sends ──
const canvas = await import('../components/builtin/canvas/renderer.js');
const tr = (ev) => ({ isUserEvent: (e) => ev === e || ev.startsWith(e + '.') });
check('typing is an edit', canvas.isUserEdit([tr('input.type')]));
check('deleting is an edit', canvas.isUserEdit([tr('delete.backward')]));
check('undo is an edit', canvas.isUserEdit([tr('undo')]));
check('an inline-AI edit is an edit', canvas.isUserEdit([tr('input.ai')]));
check('a navigator insert is an edit', canvas.isUserEdit([tr('input.paste')]));
check('a selection move is not an edit', !canvas.isUserEdit([tr('select.pointer')]));
check('a programmatic change (agent write, restore, preview) is not an edit',
      !canvas.isUserEdit([{ isUserEvent: () => false }]));
check('no transactions is not an edit', !canvas.isUserEdit([]));

const sql = 'SELECT p.ProductType, SUM(d.Quantity) FROM fitness_db.Products p JOIN fitness_db.SaleDetails d ON p.ProductID = d.ProductID GROUP BY p.ProductType';
const stateWith = (from, to) => ({
    getContent: () => sql,
    _editorView: { state: { selection: { main: { from, to, empty: from === to } }, doc: { sliceString: (a, b) => sql.slice(a, b) } } },
});
const at = sql.indexOf('Products');
check('a selected table name is not what Explain sends (the statement is)',
      canvas.explainTarget(stateWith(at, at + 'Products'.length)) === sql);
check('no selection sends the statement', canvas.explainTarget(stateWith(0, 0)) === sql);
const two = 'SELECT 1;\nSELECT * FROM t';
const st2 = { getContent: () => two, _editorView: { state: { selection: { main: { from: 10, to: two.length, empty: false } }, doc: { sliceString: (a, b) => two.slice(a, b) } } } };
check('a selected statement is what Explain sends', canvas.explainTarget(st2) === 'SELECT * FROM t');
const st3 = { getContent: () => 'WITH x AS (SELECT 1) SELECT * FROM x', _editorView: { state: { selection: { main: { from: 0, to: 36, empty: false } }, doc: { sliceString: (a, b) => 'WITH x AS (SELECT 1) SELECT * FROM x'.slice(a, b) } } } };
check('a selected WITH query counts as a statement', canvas.explainTarget(st3).startsWith('WITH'));

// ── 6. Canvas autosave wiring (structural: the seams a browser exercises) ──
const csrc = readFileSync(new URL('../components/builtin/canvas/renderer.js', import.meta.url), 'utf8');
check('the editor saves on a user edit only', /update\.docChanged && isUserEdit\(update\.transactions\)\) _scheduleCanvasSave\(state\)/.test(csrc));
check('autosave records no version', /record_version: false/.test(csrc));
check('leaving records a version', /record_version: true/.test(csrc));
check('switching canvases saves the one being left', /_switchCanvasInPanel\(canvasId\) \{\s*if \(_openCanvasId && _openCanvasId !== canvasId\) await _flushCanvasSave\(_openCanvasId\)/.test(csrc));
check('closing the panel saves the open canvas', /export function closeSplitPanel\(\) \{[\s\S]{0,200}_flushCanvasSave\(_openCanvasId\)/.test(csrc));
check('a re-render drops a pending autosave of that canvas', /if \(canvasId\) _cancelCanvasSave\(canvasId\)/.test(csrc));
check('a page hide saves with keepalive', /pagehide[\s\S]{0,200}_flushCanvasSave\(id, true\)/.test(csrc));
check('a new canvas asks the store to create, never upsert', /mode: 'create'/.test(csrc));
check('a renamed canvas keeps its database connection', /_canvasConnections\.delete\(oldKey\); _canvasConnections\.set\(name\.toLowerCase\(\)\.trim\(\), conn\)/.test(csrc));

// ── 7. Navigator: a described object is revealed in the tree ──
const nsrc = readFileSync(new URL('../components/builtin/database_navigator/renderer.js', import.meta.url), 'utf8');
check('opening on an object selects it with reveal', /_select\(want\.db, want\.name, \(spec && spec\.column\) \|\| null, true\)/.test(nsrc));
const rev = (nsrc.match(/function _revealSelected\(\) \{[\s\S]*?\n\}/) || [''])[0];
check('reveal leaves search mode', /S\.hits = null/.test(rev));
check('reveal opens the database', /S\.open\.add\(db\)/.test(rev));
check('reveal shows a system database', /S\.showSystem = true/.test(rev));
check('reveal drops a filter that hides the object', /delete S\.filters\[db\]/.test(rev));
check('the tree scrolls the selected row into view after it renders', /_scrollToSelected\(\);\n\}\nasync function _toggleDb/.test(nsrc));
check('the scroll waits for the row (objects may still be loading)', /if \(!row\) return;/.test(nsrc));

console.log(fail ? `${pass} passed, ${fail} FAILED` : `ALL PASS — ${pass} assertions`);
process.exit(fail ? 1 : 0);
