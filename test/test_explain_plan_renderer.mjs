/**
 * EXPLAIN Plan renderer gate — executes the shipped renderer under the shared DOM stub over
 * specs the REAL Python parser produced from six real Teradata plans. No server, no network.
 */
import './_uderia_bus.mjs';
import { execSync } from 'node:child_process';
import { installDom } from './dom_stub.mjs';

let pass = 0, fail = 0;
const check = (name, cond, detail = '') => { if (cond) { pass++; console.log(`  ok  ${name}`); } else { fail++; console.log(`  FAIL  ${name}${detail ? ' — ' + detail : ''}`); } };

const specs = JSON.parse(execSync(`python3 -c "
import json,sys; sys.path.insert(0,'.')
from components.builtin.explain_plan import plan_parser as P
d=json.load(open('test/fixtures/teradata_explain_plans.json'))
out={}
for k,v in d.items():
    p=P.parse(P.explain_text([{'Explanation':l} for l in v['lines']]))
    out[k]=dict(p, statement=2, sql='SELECT 1')
print(json.dumps(out))
"`, { encoding: 'utf8', maxBuffer: 8 << 20 }));

const { doc } = installDom();
const POC = (await import('../static/js/handlers/panelOpenControl.js')).default;
const R = await import('../components/builtin/explain_plan/renderer.js');

console.log('\n[1] layout');
const L = R.layoutPlan(specs.parallel);
const rowIds = L.rows.map(r => r.map(n => n.id));
check('sources first, then the steps in order, locks and END TRANSACTION left out',
  JSON.stringify(rowIds) === JSON.stringify([['table:DEMO_USER.orders', 'table:DEMO_USER.customers'], ['4'], ['5.1', '5.2'], ['6'], ['7'], ['8']]), JSON.stringify(rowIds));
check('the steps of a parallel group share a row', L.pos['5.1'].y === L.pos['5.2'].y && L.pos['5.1'].x < L.pos['5.2'].x);
check('every row below the last', L.pos['6'].y > L.pos['5.1'].y && L.pos['4'].y > L.pos['table:DEMO_USER.orders'].y);
check('no two nodes overlap', Object.values(L.pos).every((a, i, all) => all.every((b, j) => i === j
  || a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)));
check('the canvas holds every node', Object.values(L.pos).every(p => p.x >= 0 && p.x + p.w <= L.width && p.y + p.h <= L.height));

console.log('\n[2] severity');
const sev = R.stepSeverity(specs.product.findings);
check('the worst finding per step wins', sev['5'] === 'danger' && sev['4'] === 'info', JSON.stringify(sev));

console.log('\n[3] render');
const host = doc.createElement('div'); host.setAttribute('id', 'xp'); doc.body.appendChild(host);
R.renderExplainPlan('xp', { spec: specs.product }, { inPanel: true });
const html = host.innerHTML;
check('the findings are listed, the product join first', html.indexOf('product join') < html.indexOf('no confidence')
  && html.includes('class="xplan-sev danger"'));
check('one graph node per work step', host.querySelectorAll('.xplan-node').length === 4, host.querySelectorAll('.xplan-node').length);
check('the product-join step carries the danger outline', host.querySelectorAll('.xplan-node').find(n => n.dataset.step === '5').className.includes('sev-danger'));
check('a node names the confidence of its estimate', html.includes('rows, no confidence') || html.includes('row, no confidence'));
check('an info finding colours no node', !host.querySelectorAll('.xplan-node').find(n => n.dataset.step === '4').className.includes('sev-'));
check('nodes are reachable by keyboard and named', host.querySelectorAll('.xplan-node').every(n => n.attrs.tabindex === '0' && /^Step \d/.test(n.attrs['aria-label'] || '')));
check('an edge per spool or table read', (html.match(/class="xplan-edge"/g) || []).length === specs.product.edges.length);
const node5 = host.querySelectorAll('.xplan-node').find(n => n.dataset.step === '5');
node5.dispatchEvent({ type: 'click' });
const detail = host.querySelector('.xplan-detail');
check('selecting a step shows its full text', !detail.hidden && detail.innerHTML.includes('Step 5') && detail.innerHTML.includes('product join'));
check('…and marks it selected', node5.className.includes('sel'));
R.renderExplainPlan('xp', { spec: specs.product }, { inPanel: true });   // fresh: nothing selected, so only the finding can select
check('a fresh render selects nothing', host.querySelectorAll('.xplan-node.sel').length === 0);
const btn = host.querySelectorAll('.xplan-find-btn')[0];
btn.dispatchEvent({ type: 'click' });
check('a finding selects its step', host.querySelectorAll('.xplan-node').find(n => n.dataset.step === '5').className.includes('sel'));

check('the steps the graph leaves out are named, so the numbering has no silent gap',
  host.innerHTML.includes('Steps 1–3 lock DEMO_USER.o, DEMO_USER.c for read · step 8 ends the transaction'), (host.innerHTML.match(/xplan-bk">([^<]*)/) || [])[1]);
R.renderExplainPlan('xp', { spec: specs.product }, { inPanel: true });
const keyHtml = (host.innerHTML.match(/class="xplan-key"[\s\S]*?<\/div>/) || [''])[0];
check('the key names each colour the graph uses by its finding', keyHtml.includes('sev-danger') && keyHtml.includes('product join')
  && keyHtml.includes('sev-warn') && keyHtml.includes('estimate without statistics') && keyHtml.includes('selected step'), keyHtml);
R.renderExplainPlan('xp', { spec: specs.upi }, { inPanel: true });
const keyClean = (host.innerHTML.match(/class="xplan-key"[\s\S]*?<\/div>/) || [''])[0];
check('a colour the graph does not use is not in the key', !keyClean.includes('sev-danger') && !keyClean.includes('sev-warn') && keyClean.includes('selected step'), keyClean);
R.renderExplainPlan('xp', { spec: { ...specs.join_agg, steps: specs.join_agg.steps.map(s => ({ ...s, locks: undefined })) } }, { inPanel: true });
check('a plan parsed before locks were recorded still reads right', host.innerHTML.includes('Steps 1–3 take the table locks'));
R.renderExplainPlan('xp', { spec: specs.upi }, { inPanel: true });
check('a plan without locks has no bookkeeping line', !host.innerHTML.includes('xplan-bk'));
check('a clean plan says what was checked and found nothing', host.innerHTML.includes('No product join'));
R.renderExplainPlan('xp', JSON.stringify({ spec: { error: 'No EXPLAIN has run <here>' } }));
check('an error is shown escaped', host.innerHTML.includes('No EXPLAIN has run &lt;here&gt;'));
R.renderExplainPlan('xp', { ...specs.join_agg, _dashboardFill: true });
check('a dashboard tile fills its tile', host.innerHTML.includes('class="xplan fill"'));
R.renderExplainPlan('xp', { spec: { ...specs.product, sql: '<script>x</script>', steps: specs.product.steps.map(s => ({ ...s, text: '<b>t</b>' })) } }, { inPanel: true });
check('statement and step text are escaped', !host.innerHTML.includes('<script>x') && host.innerHTML.includes('&lt;script&gt;'));

console.log('\n[3b] the panel beside the SQL canvas');
{
  const panel = doc.createElement('aside'); panel.setAttribute('id', 'explain-plan-split-panel'); panel.style.display = 'none'; doc.body.appendChild(panel);
  const swBtn = doc.createElement('button'); swBtn.setAttribute('id', 'explain-plan-switcher-btn'); panel.appendChild(swBtn);
  const title = doc.createElement('span'); title.setAttribute('id', 'explain-plan-split-title'); panel.appendChild(title);
  const bar = doc.createElement('div'); bar.setAttribute('id', 'explain-plan-subtoolbar'); panel.appendChild(bar);
  const content = doc.createElement('div'); content.setAttribute('id', 'explain-plan-split-content'); panel.appendChild(content);
  let switcher = null;
  globalThis.window.mountSplitPanelSwitcher = (o) => { switcher = o; return () => {}; };
  // in the answer: the graph only, as a preview, with the shared footer that opens the panel
  const rsrc = (await import('node:fs')).readFileSync('components/builtin/explain_plan/renderer.js', 'utf8');
  R.renderExplainPlan('xp', { spec: specs.product });
  const card = host.querySelector('.xplan-card');
  check('an answer plan is a card: the graph and the footer, nothing else', !!card && card.querySelectorAll('svg').filter(e => !e.matches('.sp-open-ctl-icon')).length === 1
    && host.querySelectorAll('.xplan-find').length === 0 && host.querySelectorAll('.xplan-key').length === 0
    && host.querySelectorAll('.xplan-bk').length === 0 && host.querySelectorAll('.xplan-sql').length === 0 && host.querySelectorAll('.xplan-head').length === 0,
    JSON.stringify(['svg','.xplan-find','.xplan-key','.xplan-bk','.xplan-sql','.xplan-head'].map(q => host.querySelectorAll(q).length)));
  {
    const html = card.querySelector('.xplan-card-preview').innerHTML;
    const work = specs.product.steps.filter(x => !['LOCK', 'END TRANSACTION', 'PARALLEL'].includes(x.op)).length;
    const stepGroups = html.match(/<g class="xplan-node[^"]*" data-step="[^"]*">(.*?)<\/g>/g) || [];
    check('…every step of the preview is drawn: its box and its two lines inside a closed group',
      stepGroups.length === work && stepGroups.every(g => /<rect /.test(g) && (g.match(/<text/g) || []).length === 2), `${stepGroups.length} of ${work}`);
  }
  check('…the preview is neutral (its colour key lives in the panel)', !/sev-(danger|warn)/.test(card.innerHTML));
  const ctl = card.querySelector('.sp-open-ctl');
  const label = () => (card.querySelector('.sp-open-ctl-label') || {}).textContent;
  check('…its nodes are not tab stops and the preview is decoration (the shared control is the one control)',
    !/<g class="xplan-node[^"]*" data-step="[^"]*" tabindex/.test(card.innerHTML)
    && card.querySelector('.xplan-card-preview').getAttribute('aria-hidden') === 'true' && card.getAttribute('role') === null);
  check('…the footer is the shared open control every component card uses', !!ctl && ctl.dataset.spOpen === 'explain_plan'
    && ctl.getAttribute('aria-controls') === 'explain-plan-split-panel' && label() === 'View in Canvas' && ctl.getAttribute('aria-expanded') === 'false');
  check('…no corner button for the dashboard pin to cover', !/xplan-open-panel|in-answer/.test(rsrc));
  await POC._onClick(ctl);
  check('the control opens its plan in the side panel', panel.className.includes('xplan-split--open') && content.querySelectorAll('.xplan-find').length === 1);
  check('…and the card says so', label() === 'Expanded in side panel' && ctl.getAttribute('aria-expanded') === 'true');
  check('the panel carries the details the card leaves out', content.querySelectorAll('.xplan-key').length === 1 && content.querySelectorAll('.xplan-node.sev-danger').length > 0);
  await POC._onClick(ctl);
  check('clicking it again closes the panel', !panel.className.includes('xplan-split--open') && label() === 'View in Canvas');
  card.querySelector('.xplan-card-preview').dispatchEvent({ type: 'click' });
  for (let i = 0; i < 5; i++) await Promise.resolve();
  check('clicking the preview is the same gesture as the control', panel.className.includes('xplan-split--open') && label() === 'Expanded in side panel');
  await POC._onClick(ctl);
  R._resetPanelForTest();
  R.renderExplainPlan('xp', { spec: specs.product }, { inPanel: true });
  check('…the panel view has no card', host.querySelectorAll('.xplan-card').length === 0 && host.querySelectorAll('.xplan-find').length === 1);
  R.renderExplainPlan('xp', { ...specs.product, _dashboardFill: true });
  check('…nor does a dashboard tile (it fills with the graph)', host.querySelectorAll('.xplan-card').length === 0);
  // selecting a step hands it to the caller (the panel's link to the canvas)
  let picked = null;
  R.renderExplainPlan('xp', { spec: specs.product }, { inPanel: true, onSelectStep: s => { picked = s; } });
  host.querySelectorAll('.xplan-node').find(n => n.dataset.step === '4').dispatchEvent({ type: 'click' });
  check('a selected step is handed to the panel with its inputs', picked && picked.id === '4' && (picked.inputs || []).some(i => !i.startsWith('Spool ')), JSON.stringify(picked && picked.inputs));
  // open, re-open the same plan, open a second plan
  check('the panel opens on a plan', R.openExplainPlanPanel({ ...specs.join_agg, origin: 'canvas', connection_name: 'TD prod' }) === true
    && panel.className.includes('xplan-split--open'));
  check('the title names where the plan came from, like the other panels\' instance titles', /^TD prod · \d/.test(title.textContent), title.textContent);
  check('the source sits in the shared toolbar row as a badge', bar.innerHTML.includes('pc-badge') && bar.innerHTML.includes('>SQL canvas<'));
  check('the title is the shared instance switcher, fed from the panel\'s own plans', switcher && typeof switcher.items === 'function' && switcher.items().length === 1);
  R.openExplainPlanPanel({ ...specs.join_agg, origin: 'canvas', connection_name: 'TD prod' });
  check('the same plan opened twice is kept once', R._panelHistory().length === 1);
  const planHtml = () => (content.querySelector('#explain-plan-split-graph') || { innerHTML: '' }).innerHTML;
  check('one plan: nothing to compare yet', content.querySelectorAll('.xplan-cmp').length === 0 && !planHtml().includes('xplan-cmp'));
  R.openExplainPlanPanel({ ...specs.product, origin: 'canvas' });
  check('a second plan is compared with the first, in the Overview segment', planHtml().includes('Compared with the previous plan') && R._panelHistory().length === 2
    && content.querySelectorAll('.xplan-cmp').length === 1 && content.querySelectorAll('.xplan-cmp')[0].closest('.xplan-pane').dataset.pane === 'overview');
  check('…and a plan that just arrived with a comparison opens on the Overview', content.querySelectorAll('.xplan-pane').filter(x => !x.hidden).map(x => x.dataset.pane).join() === 'overview');
  check('…a new product join reads in the danger colour', /class="new">new: product join/.test(planHtml()), (planHtml().match(/<ul>[\s\S]*?<\/ul>/) || [''])[0]);
  check('…the panel draws the graph, not a card', content.querySelectorAll('.xplan-node').length > 0 && content.querySelectorAll('.xplan-card').length === 0);
  check('…and offers the statement back to the canvas, as a shared toolbar button', /class="pc-btn xplan-to-canvas"/.test(bar.innerHTML));
  check('…with Compare as a toggle in the same row', /class="pc-btn xplan-cmp-toggle is-active" aria-pressed="true"/.test(bar.innerHTML));
  check('the switcher lists both plans, newest first', switcher.items().length === 2 && switcher.items()[0].i === 1);
  switcher.onSelect(0);
  check('picking an earlier plan shows it (and nothing to compare it with)', /^TD prod/.test(title.textContent) && content.querySelectorAll('.xplan-cmp').length === 0, title.textContent);
  check('an answer\'s plan offers no Re-explain (its connection is not known)', !bar.innerHTML.includes('xplan-reexplain'));
  check('Copy and Download are in the menu bar', bar.innerHTML.includes('xplan-copy') && bar.innerHTML.includes('xplan-download'));
  R.openExplainPlanPanel({ ...specs.three, origin: 'canvas', connection_id: 'c1', connection_name: 'TD prod', sql: 'SELECT 1' });
  check('a canvas plan with its connection offers Re-explain, naming the connection', /class="pc-btn xplan-reexplain" title="Explain the same statement again on TD prod/.test(bar.innerHTML));
  // the Workspace lists the panel's plans and opens one of them
  const rows = R.panelPlans();
  check('the Workspace rows are the panel\'s plans, newest first, with step and finding counts',
    rows.length === 3 && rows[0].i === 2 && rows[2].i === 0 && rows.every(r => r.steps > 0 && typeof r.findings === 'number' && r.created_at), JSON.stringify(rows.map(r => r.i)));
  check('…labelled as the switcher labels them', rows[0].label === switcher.items()[0].label);
  check('opening a Workspace row shows that plan without adding a new one', R.showPanelPlan(0) === true && R._panelHistory().length === 3
    && switcher.getCurrentId() === 0 && panel.className.includes('xplan-split--open'));
  check('…an index outside the list opens nothing', R.showPanelPlan(9) === false);
  let told = 0;
  UderiaBus.onComponent('explain_plan', () => { told++; });   // the page announcement (components/signals.py)
  R.openExplainPlanPanel({ ...specs.three, origin: 'canvas', connection_id: 'c1', connection_name: 'TD prod', sql: 'SELECT 1' });
  check('re-opening the plan on screen does not tell the Workspace (nothing was added)', told === 0);
  R.openExplainPlanPanel({ ...specs.product, origin: 'canvas', connection_name: 'TD dev' });
  check('a newly added plan tells the Workspace to refresh', told === 1);
  const txt = R.planText({ ...specs.product, sql: 'SELECT x FROM t' });
  check('the copied / downloaded text has the statement, the findings and every step in its own words',
    txt.startsWith('Statement:\nSELECT x FROM t') && txt.includes('Findings:') && txt.includes('5) ' + specs.product.steps.find(s => s.id === '5').text));
  R.closeExplainPlanPanel();
  check('closing the panel', !panel.className.includes('xplan-split--open'));
}

console.log('\n[3c] what changed between two plans');
{
  const c = R.comparePlans(specs.join_agg, specs.product);
  check('findings are matched by kind, not step number', c.added.includes('product_join') && !c.gone.includes('no_confidence'), JSON.stringify(c));
  check('a gone finding is reported', R.comparePlans(specs.product, specs.join_agg).gone.includes('product_join'));
  check('each change carries its severity', c.severity.product_join === 'danger');
  check('step counts and estimated time are compared', c.steps.length === 2 && c.seconds.length === 2);
  check('the same plan twice: no change', R.comparePlans(specs.product, specs.product).added.length === 0 && R.comparePlans(specs.product, specs.product).gone.length === 0);
}

console.log('\n[3d] the canvas side');
{
  const C = await import('../components/builtin/canvas/renderer.js');
  const sql = 'SELECT c.State FROM fitness_db.Customers c JOIN fitness_db.Sales AS s ON c.CustomerID = s.CustomerID';
  const at = (n) => C.planTableRanges(sql, n).map(r => sql.slice(r.from, r.to));
  check('a plan names a table by its alias — the alias resolves to the table', JSON.stringify(at('fitness_db.c')) === '["fitness_db.Customers"]' && JSON.stringify(at('fitness_db.s')) === '["fitness_db.Sales"]');
  check('a qualified name is found as written', JSON.stringify(at('fitness_db.Customers')) === '["fitness_db.Customers"]');
  check('an unqualified statement still finds the table by name', JSON.stringify(C.planTableRanges('SELECT * FROM orders', 'DEMO_USER.orders').map(r => 'SELECT * FROM orders'.slice(r.from, r.to))) === '["orders"]');
  check('a table the statement does not name is not found', C.planTableRanges(sql, 'fitness_db.Products').length === 0);
  const st = (driver, language = 'sql') => ({ language, _activeConnectionId: 'x', _connections: [{ connection_id: 'x', driver }] });
  check('Explain is offered for SQL on a Teradata connection', C.canExplain(st('teradata')) === true);
  check('…not on another database, not without a connection, not for another language',
    C.canExplain(st('postgresql')) === false && C.canExplain({ language: 'sql', _connections: [] }) === false && C.canExplain(st('teradata', 'python')) === false);
  const src = (await import('node:fs')).readFileSync('components/builtin/canvas/renderer.js', 'utf8');
  check('the connection picker names the connection (it shares the database glyph with the navigator)',
    /canvas-conn-pill-label/.test(src) && /className = 'canvas-toolbar-btn canvas-conn-pill'/.test(src));
  check('choosing a connection re-decides the Explain button', (src.match(/_syncExplainBtn\(state\);/g) || []).length >= 3);
}


console.log('\n[5] a large plan in the width it is given (a real 195-line plan: 9 tables, 4 parallel groups, 17 steps)');
{
  const big = specs.large;
  const work = big.steps.filter(s => !['LOCK', 'END TRANSACTION', 'PARALLEL'].includes(s.op));
  const wide = R.layoutPlan(big);
  check('unbounded, the nine tables are one row far wider than any panel (the defect)', wide.width > 2000, wide.width);
  // a parallel group wider than any panel: the real plan's group 4, given twelve members
  const g4 = big.steps.filter(s => s.parallel_group === '4');
  const many = { ...big, steps: big.steps.flatMap(s => s.id === '4.2'
    ? [s, ...Array.from({ length: 10 }, (_, i) => ({ ...g4[i % 2], id: `4.${i + 3}` }))] : [s]) };
  for (const W of [420, 640, 1100]) {
    const L = R.layoutPlan(big, { maxWidth: W });
    check(`at ${W}px every node lies inside the width`, L.width <= W && Object.values(L.pos).every(p => p.x >= 0 && p.x + p.w <= L.width), `${L.width}`);
    check(`at ${W}px no two nodes overlap`, Object.values(L.pos).every((a, i, all) => all.every((b, j) => i === j
      || a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)));
    check(`at ${W}px every work step is drawn`, work.every(s => L.pos[s.id]));
    check(`at ${W}px each parallel group sits inside its own labelled frame`, L.frames.length === 4 && L.frames.every(fr => fr.ids.every(id => {
      const p = L.pos[id]; return p.x >= fr.x && p.x + p.w <= fr.x + fr.w && p.y >= fr.y && p.y + p.h <= fr.y + fr.h; })));
    const M = R.layoutPlan(many, { maxWidth: W });
    const fr = M.frames.find(f => f.ids.length === 12);
    check(`at ${W}px a 12-step parallel group wraps into rows inside ONE frame, within the width`, !!fr && M.width <= W
      && new Set(fr.ids.map(id => M.pos[id].y)).size > 1 && fr.ids.every(id => {
      const p = M.pos[id]; return p.x >= fr.x && p.x + p.w <= fr.x + fr.w && p.y >= fr.y && p.y + p.h <= fr.y + fr.h; }));
    check(`at ${W}px frames do not overlap the rows around them`, M.frames.every(f => Object.values(M.pos).every(p =>
      f.ids.includes(p.node.id) || p.y + p.h <= f.y || p.y >= f.y + f.h)));
  }
  const narrow = R.layoutPlan(big, { maxWidth: 640 });
  check('nine tables do not fit one row at 640px: they move into the steps that read them', narrow.tablesInSteps && !Object.keys(narrow.pos).some(k => k.startsWith('table:')));
  const lane = R.layoutPlan(big, { maxWidth: 640, tables: 'lane' });
  check('…unless the reader asks for the row — then the tables wrap inside the width', !lane.tablesInSteps && big.sources.every(t => lane.pos['table:' + t]) && lane.width <= 640);
  check('a plan whose tables fit keeps them as a row', !R.layoutPlan(specs.product, { maxWidth: 640 }).tablesInSteps);
  check('a node narrows before the layout loses a column', R.layoutPlan(big, { maxWidth: 230 }).nodeW < 236 && R.layoutPlan(big, { maxWidth: 230 }).width <= 230);
  const z = R.fitAllZoom(big, 640, 700);
  const Lz = R.layoutPlan(big, { maxWidth: 640 / z });
  check('"Whole plan" picks a zoom at which the reflowed plan fits the viewport', z < 1 && Lz.height * z <= 700 && Lz.width * z <= 641, `${z} ${Lz.height * z}`);
  check('…and a plan that already fits needs no zoom-out', R.fitAllZoom(specs.upi, 640, 700) === 1);

  const g = R.groupFindings(big.findings);
  const dup = g.find(f => f.kind === 'duplicated');
  check('info findings of one kind fold into one line naming every step', dup && dup.grouped && dup.steps.length === 6
    && g.filter(f => f.kind === 'duplicated').length === 1);
  const pj = g.filter(f => f.kind === 'product_join');
  check('…four product joins are one line: the finding once, its explanation once, a chip per step in execution order',
    pj.length === 1 && pj[0].grouped && pj[0].severity === 'danger' && JSON.stringify(pj[0].steps) === '["5","7.1","8","10"]' && /every row of one input/.test(pj[0].why), JSON.stringify(pj));
  check('…and the lines stay worst first', g.map(f => ({ danger: 0, warn: 1, info: 2 })[f.severity]).every((v, i, a) => !i || a[i - 1] <= v));
  check('…a kind with one finding keeps its own sentence', g.filter(f => f.kind === 'hot_step').every(f => !f.grouped && f.text));

  R.renderExplainPlan('xp', { spec: { ...big, sql: 'SELECT * FROM DBC.UserRoleRightsV' } }, { inPanel: true, graphWidth: 640 });
  const tabs = host.querySelectorAll('.xplan-tab').map(t => t.dataset.seg);
  check('the panel is split into segments: Overview · Graph · Steps · Statement', JSON.stringify(tabs) === '["overview","graph","steps","statement"]', JSON.stringify(tabs));
  const shown = () => host.querySelectorAll('.xplan-pane').filter(x => !x.hidden).map(x => x.dataset.pane);
  check('one segment is shown at a time', shown().length === 1);
  check('each tab names its pane (tabs and panels are linked for assistive tech)', host.querySelectorAll('.xplan-tab').every(t => {
    const pane = host.querySelector('#' + t.getAttribute('aria-controls')); return pane && pane.getAttribute('aria-labelledby') === t.getAttribute('id'); }));
  const svgHtml = host.querySelector('.xplan-graph').innerHTML;
  const svgW = Number((svgHtml.match(/<svg width="(\d+)"/) || [])[1]);
  check('the graph is drawn for the width it is given', svgW > 0 && svgW <= 640, svgW);
  check('the parallel group is drawn as a labelled frame', /class="xplan-par"[\s\S]*?in parallel · steps/.test(svgHtml));
  check('a step that reads base tables names them when the tables are folded in', /reads DBC\./.test(svgHtml));
  check('a step carries a bar for its share of the estimated time', /class="tbar"/.test(svgHtml));
  check('the overview folds the 6 duplications into one line of step chips',
    host.querySelector('.xplan-find').children.length === R.groupFindings(big.findings).length && host.querySelectorAll('.xplan-chip').length >= 6);
  const chip = host.querySelectorAll('.xplan-chip').find(c => c.closest('.xplan-find'));
  chip.dispatchEvent({ type: 'click' });
  check('a step chip opens the Graph segment on that step', shown().join() === 'graph' && host.querySelector('.xplan-node.sel')?.dataset.step === chip.dataset.step);
  const sel = host.querySelector('.xplan-node.sel').dataset.step;
  const on = host.querySelectorAll('.xplan-edge').filter(e => e.className.includes('on'));
  check('selecting a step lights the edges into and out of it, and marks the steps at their other end',
    on.length > 0 && on.every(e => e.dataset.to === sel || e.dataset.from === sel)
    && host.querySelectorAll('.xplan-node').filter(n => n.className.includes('rel')).length > 0, `${on.length}`);
  check('…and the selected step is described beside the graph', !host.querySelector('.xplan-detail').hidden && host.querySelector('.xplan-detail').innerHTML.includes(`Step ${sel}`));
  const order = work.map(s => s.id);
  host.querySelector('.xplan-node.sel').dispatchEvent({ type: 'keydown', key: 'ArrowDown', preventDefault() {} });
  check('ArrowDown moves the selection to the next step in execution order', host.querySelector('.xplan-node.sel').dataset.step === order[order.indexOf(sel) + 1]);
  host.querySelector('.xplan-dprev').dispatchEvent({ type: 'click' });
  check('…and the detail\'s previous button moves it back', host.querySelector('.xplan-node.sel').dataset.step === sel);
  host.querySelector('.xplan-node.sel').dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });
  check('Escape clears the selection', host.querySelectorAll('.xplan-node.sel').length === 0 && host.querySelector('.xplan-detail').hidden);
  host.querySelector('.xplan-zoom-out').dispatchEvent({ type: 'click' });
  const svg2 = host.querySelector('.xplan-graph').innerHTML;
  const dims = h => (h.match(/<svg width="(\d+)" height="(\d+)" viewBox="0 0 (\d+) (\d+)"/) || []).slice(1).map(Number);
  const [w1, h1, vw1] = dims(svgHtml), [w2, h2, vw2, vh2] = dims(svg2);
  check('zooming out lays the plan out for more room and draws it smaller (more of it on screen)', host.querySelector('.xplan-zoom-v').textContent === '80%'
    && h2 < h1 && w2 <= 640 && Math.abs(w2 - vw2 * 0.8) <= 1 && Math.abs(h2 - vh2 * 0.8) <= 1, (svg2.match(/<svg[^>]*>/) || [''])[0]);
  {
    const M = { ...big, steps: big.steps.flatMap(s => s.id === '4.2' ? [s, ...Array.from({ length: 6 }, (_, i) => ({ ...s, id: `4.${i + 3}` }))] : [s]) };
    const a = R.layoutPlan(M, { maxWidth: 640 }), b = R.layoutPlan(M, { maxWidth: 640 / 0.5 });
    check('…a wide parallel group gets more columns at 50% (the layout reflows, it does not just scale)', b.cols > a.cols && b.height * 0.5 < a.height);
  }
  host.querySelectorAll('.xplan-tbl').find(b => b.dataset.tables === 'lane').dispatchEvent({ type: 'click' });
  check('the Tables switch draws the tables as a row again', /class="xplan-src"/.test(host.querySelector('.xplan-graph').innerHTML)
    && host.querySelectorAll('.xplan-tbl').find(b => b.dataset.tables === 'lane').className.includes('on'));
  host.querySelectorAll('.xplan-tbl').find(b => b.dataset.tables === 'steps').dispatchEvent({ type: 'click' });

  host.querySelectorAll('.xplan-tab').find(t => t.dataset.seg === 'steps').dispatchEvent({ type: 'click' });
  check('the Steps segment lists every step but the parallel markers', host.querySelectorAll('tr.xplan-row').length + host.querySelectorAll('tr.bk').length
    === big.steps.filter(s => s.op !== 'PARALLEL').length);
  host.querySelectorAll('.xplan-sort').find(b => b.dataset.sort === 'time').dispatchEvent({ type: 'click' });
  const firstRow = host.querySelectorAll('tr.xplan-row')[0] || host.querySelectorAll('tr.bk')[0];
  const maxSec = Math.max(...big.steps.map(s => Number(s.est_seconds) || 0));
  check('…sorted by time, the slowest step comes first', Number(big.steps.find(s => s.id === firstRow.dataset.step).est_seconds) === maxSec);
  const only = host.querySelector('.xplan-only-find'); only.checked = true; only.dispatchEvent({ type: 'change', target: only });
  check('…"only steps with a finding" leaves exactly those', host.querySelectorAll('tr.xplan-row').every(r => R.stepSeverity(big.findings)[r.dataset.step]));
  host.querySelectorAll('tr.xplan-row')[0].dispatchEvent({ type: 'click' });
  check('…a row opens the step in the Graph', shown().join() === 'graph' && !!host.querySelector('.xplan-node.sel'));
  const tStat = host.querySelectorAll('.xplan-tab').find(t => t.dataset.seg === 'steps');
  tStat.dispatchEvent({ type: 'keydown', key: 'ArrowRight', preventDefault() {} });
  check('the tabs move with the arrow keys', shown().join() === 'statement' && host.querySelector('.xplan-sql').innerHTML.includes('DBC.UserRoleRightsV'));
  R.renderExplainPlan('xp', { spec: big });
  check('the preview card lays a large plan out for an answer\'s width (a wrapped frame, no 3000px row)',
    Number((host.innerHTML.match(/viewBox="0 0 (\d+)/) || [])[1]) <= 780 && /xplan-par/.test(host.innerHTML));
}

console.log('\n[4] theme');
const src = (await import('node:fs')).readFileSync('components/builtin/explain_plan/renderer.js', 'utf8');
const css = src.slice(src.indexOf('st.textContent = `'), src.indexOf('`;', src.indexOf('st.textContent = `')));
check('colours come from theme variables (a literal only as a var() fallback)',
  (css.match(/#[0-9a-fA-F]{3,6}\b/g) || []).length === (css.match(/var\(--[\w-]+, #[0-9a-fA-F]{3,6}\)/g) || []).length);

console.log(`\n${pass} passed, ${fail} failed`);
console.log(fail ? `RESULT: FAILURES — ${fail} of ${pass + fail}` : `ALL PASS — ${pass} assertions (the plan renderer over six real Teradata plans)`);
process.exit(fail ? 1 : 0);
