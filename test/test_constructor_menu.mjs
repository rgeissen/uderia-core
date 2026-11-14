#!/usr/bin/env node
/**
 * The Intelligence tabs' "New" menu — a constructor is one press away however many
 * instances the tab lists, and the menu can never disagree with the constructor cards.
 *
 * The cards are built by the SHIPPED createTemplateCard (templateSystem.js) and the
 * SHIPPED constructorMenu.js is executed over them under the shared DOM stub:
 *   - the menu is READ from the cards (one row per card, same order, name + description),
 *     and choosing a row clicks that card's own Deploy button — so the card's real
 *     handler runs (no second template list, filter or deploy path);
 *   - a "Coming soon" constructor is listed and not runnable;
 *   - keyboard: focus lands in the menu, arrows cycle, Escape returns focus to the button;
 *   - it closes on an outside click, a second press, Tab, or the page scrolling under it;
 *   - the template carries one New button per tab, in the pinned header row, naming a
 *     container that exists.
 */
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';

const read = rel => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const banners = [];
const { doc, body } = installDom({ showAppBanner: (msg) => banners.push(msg), configState: {} });
El.prototype.getBoundingClientRect = function () { return { left: 900, right: 984, top: 400, bottom: 430, width: 84, height: 30 }; };
// the stub parses `disabled` as an attribute only — reflect it to the property, as a browser does
const reflectDisabled = el => el._walk().forEach(n => { if (n.hasAttribute('disabled')) n.disabled = true; });

const { createTemplateCard } = await import(pathToFileURL('static/js/handlers/rag/templateSystem.js').href);

// ── a tab: header row (Import + New) and its constructor-card container ──────
const TEMPLATES = [
    { template_id: 'sql_query_v1', template_type: 'sql_query', display_name: 'SQL Query Constructor - Database Context', description: 'Two-phase strategy from the database schema.' },
    { template_id: 'sql_query_doc_context_v1', template_type: 'sql_query', display_name: 'SQL Query Constructor - Document Context', description: 'Question/SQL pairs from documentation.' },
    { template_id: 'api_request_v1', template_type: 'api_request', display_name: 'API Request Constructor', description: 'Not built yet.', status: 'coming_soon' },
];
const head = new El('div', { class: 'intel-section-head' }); body.appendChild(head);
const importBtn = new El('button', { id: 'import-planner-collection-btn' }); head.appendChild(importBtn);
const newBtn = new El('button', { id: 'new-planner-repository-btn', 'data-ctor-source': 'rag-templates-container', 'aria-expanded': 'false', 'aria-label': 'New planner repository' });
head.appendChild(newBtn);
const grid = new El('div', { id: 'rag-templates-container' }); body.appendChild(grid);
const deployClicks = {};
TEMPLATES.forEach((t, i) => {
    const card = createTemplateCard(t, i, 'planner');
    reflectDisabled(card);
    grid.appendChild(card);
    const d = card.querySelector('.template-deploy-btn');
    d.addEventListener('click', () => { deployClicks[t.template_id] = (deployClicks[t.template_id] || 0) + 1; });
});
const emptyGrid = new El('div', { id: 'kg-constructors-container' }); body.appendChild(emptyGrid);
const emptyBtn = new El('button', { id: 'new-knowledge-graph-btn', 'data-ctor-source': 'kg-constructors-container', 'aria-expanded': 'false' }); body.appendChild(emptyBtn);
const elsewhere = new El('div', { id: 'elsewhere' }); body.appendChild(elsewhere);

await import(pathToFileURL('static/js/handlers/constructorMenu.js').href);
const CM = window.ConstructorMenu;
const click = target => doc.dispatchEvent({ type: 'click', target });
const key = k => { const ev = { type: 'keydown', key: k, preventDefault() { ev.prevented = true; } }; doc.dispatchEvent(ev); return ev; };
const menu = () => body.querySelector('.ctor-menu');
const rows = () => menu() ? menu().querySelectorAll('.ctor-menu-item') : [];
const tick = () => new Promise(r => setImmediate(r));

// ── 1. the menu is read from the cards ───────────────────────────────────────
const list = CM.constructors('rag-templates-container');
ok('one constructor per card, in card order', eq(list.map(c => c.id), TEMPLATES.map(t => t.template_id)), JSON.stringify(list.map(c => c.id)));
ok('…named and described by the card\'s own text', eq(list.map(c => c.name), TEMPLATES.map(t => t.display_name)) && eq(list.map(c => c.desc), TEMPLATES.map(t => t.description)), JSON.stringify(list.map(c => [c.name, c.desc])));
ok('…carrying the card\'s own icon markup', list.every(c => /<path|<circle/.test(c.icon)));
ok('a "Coming soon" card is listed but has nothing to run', list[2].deploy === null && !!list[0].deploy && !!list[1].deploy);
ok('an unknown container reads as no constructors', eq(CM.constructors('no-such-container'), []));

// ── 2. open ──────────────────────────────────────────────────────────────────
ok('nothing is rendered until the button is pressed', !menu());
click(newBtn);
ok('pressing New opens a menu on <body> (nothing in the pane can clip it)', !!menu() && menu().parentNode === body && menu().getAttribute('role') === 'menu');
ok('…one row per card, same order, same names', eq(rows().map(r => r.querySelector('.ctor-menu-name').textContent), TEMPLATES.map(t => t.display_name)));
ok('…each with its description', eq(rows().map(r => r.querySelector('.ctor-menu-desc').textContent), TEMPLATES.map(t => t.description)));
ok('…the coming-soon row is disabled and says so in words', rows()[2].disabled === true && rows()[2].querySelector('.ctor-menu-soon').textContent === 'Coming soon' && !rows()[0].disabled);
ok('the button reports the open state', newBtn.getAttribute('aria-expanded') === 'true');
ok('keyboard focus lands on the first runnable row', doc.activeElement === rows()[0]);
ok('the menu sits under the button, right edges aligned', menu().style.top === '436px' && menu().style.left === (984 - 380) + 'px', `${menu().style.top} ${menu().style.left}`);
ok('a footer points at the cards (details and defaults stay there)', !!menu().querySelector('.ctor-menu-foot'));

// ── 3. keyboard ──────────────────────────────────────────────────────────────
let ev = key('ArrowDown');
ok('ArrowDown moves to the next runnable row', doc.activeElement === rows()[1] && ev.prevented === true);
key('ArrowDown');
ok('…skipping the disabled row (on to the footer)', doc.activeElement === menu().querySelector('.ctor-menu-foot'));
key('ArrowDown');
ok('…and wraps to the first', doc.activeElement === rows()[0]);
key('ArrowUp');
ok('ArrowUp wraps to the last', doc.activeElement === menu().querySelector('.ctor-menu-foot'));
key('Home'); ok('Home goes to the first row', doc.activeElement === rows()[0]);
key('End');  ok('End goes to the last', doc.activeElement === menu().querySelector('.ctor-menu-foot'));
ev = key('Escape');
ok('Escape closes the menu and returns focus to the button', !menu() && doc.activeElement === newBtn && newBtn.getAttribute('aria-expanded') === 'false' && ev.prevented === true);
ev = key('ArrowDown');
ok('with no menu open the keys are not ours', !ev.prevented);

// ── 4. choosing a row IS the card's Deploy ───────────────────────────────────
click(newBtn);
rows()[1].click();
await tick();
ok('choosing a row clicks THAT card\'s Deploy button, once', eq(deployClicks, { sql_query_doc_context_v1: 1 }), JSON.stringify(deployClicks));
ok('…so the card\'s real handler ran (its own not-initialised check answered)', banners.length === 1, JSON.stringify(banners));
ok('…and the menu is gone', !menu() && newBtn.getAttribute('aria-expanded') === 'false');
click(newBtn);
rows()[2].click();
ok('a disabled row deploys nothing', eq(deployClicks, { sql_query_doc_context_v1: 1 }));
click(newBtn);

// ── 5. closing ───────────────────────────────────────────────────────────────
click(newBtn);
ok('a press opens…', !!menu());
click(newBtn);
ok('…and a second press on the same button closes', !menu());
click(newBtn); click(elsewhere);
ok('a click anywhere else closes it', !menu() && newBtn.getAttribute('aria-expanded') === 'false');
click(newBtn); click(menu());
ok('a click inside the menu\'s own padding does not', !!menu());
doc.dispatchEvent({ type: 'scroll', target: menu() });
ok('scrolling INSIDE the menu keeps it', !!menu());
doc.dispatchEvent({ type: 'scroll', target: grid });
ok('the page scrolling under it closes it (it would point at nothing)', !menu());
click(newBtn); key('Tab');
ok('Tab closes it', !menu());
click(newBtn);
let scrolled = 0; grid.scrollIntoView = () => { scrolled++; };
menu().querySelector('.ctor-menu-foot').click();
ok('the footer closes the menu and brings the constructor cards into view', !menu() && scrolled === 1);

// ── 6. one menu at a time; an empty tab says so ──────────────────────────────
click(newBtn); click(emptyBtn);
ok('opening another tab\'s menu replaces the first', body.querySelectorAll('.ctor-menu').length === 1 && newBtn.getAttribute('aria-expanded') === 'false' && emptyBtn.getAttribute('aria-expanded') === 'true');
ok('a tab with no constructor cards says so in words — no rows, no footer', rows().length === 0 && /No constructors/.test(menu().querySelector('.ctor-menu-empty').textContent) && !menu().querySelector('.ctor-menu-foot'));
CM.close();
ok('close() is the programmatic exit', !menu() && emptyBtn.getAttribute('aria-expanded') === 'false');

// ── 7. a card's text is shown as TEXT ────────────────────────────────────────
const hostile = new El('div', { 'data-template-id': 'x' });
const h3 = new El('h3'); h3.textContent = '<img src=x onerror=alert(1)>'; hostile.appendChild(h3);
const dep = new El('button', { class: 'template-deploy-btn' }); hostile.appendChild(dep);
emptyGrid.appendChild(hostile);
click(emptyBtn);
ok('a name containing markup is rendered as text, never parsed', rows().length === 1 && !rows()[0].querySelector('img') && rows()[0].querySelector('.ctor-menu-name').textContent === '<img src=x onerror=alert(1)>');
CM.close();

// ── 8. structure: one source, and the template wires all three tabs ──────────
const src = read('static/js/handlers/constructorMenu.js').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
ok('the module holds no template list, filter or fetch of its own', !/templateManager|getAllTemplates|template_type|fetch\(/.test(src));
ok('…and no deploy path of its own — it clicks the card\'s button', /deploy\.click\(\)/.test(src) && !/openAddRagCollectionModal|openKg\w+Modal|openKnowledgeRepositoryModal/.test(src));
const idx = read('templates/index.html');
const news = [...idx.matchAll(/<button id="(new-[a-z-]+-btn)"[^>]*data-ctor-source="([a-z-]+)"[^>]*>/g)];
ok('the template carries one New button per Intelligence tab', eq(news.map(m => m[2]).sort(), ['kg-constructors-container', 'knowledge-constructors-container', 'rag-templates-container']), JSON.stringify(news.map(m => m[1])));
ok('…each naming a constructor-card container that exists', news.every(m => idx.includes(`id="${m[2]}"`)));
ok('…each announcing itself as a menu button with a label', news.every(m => /aria-haspopup="menu"/.test(m[0]) && /aria-expanded="false"/.test(m[0]) && /aria-label="New [a-z ]+"/.test(m[0])));
ok('…each in the pinned header row, beside that tab\'s Import button', news.every(m => {
    const at = idx.indexOf(m[0]); const row = idx.lastIndexOf('<div class="intel-section-head', at);
    const between = idx.slice(row, at);
    return row > 0 && at - row < 2600 && /<button id="import-[a-z-]+-btn"/.test(between) && (between.match(/intel-section-head/g) || []).length === 1;
}));
ok('the header row is pinned over the pane\'s top padding (no strip for cards to show through)', /\.intel-section-head\s*\{[^}]*position:\s*sticky;[^}]*top:\s*-1\.5rem;[^}]*margin:\s*-1\.5rem[^}]*padding:\s*1\.5rem[^}]*background:\s*var\(--bg-primary/.test(read('static/js/handlers/constructorMenu.js')));
ok('index.html loads the module, pinned', /js\/handlers\/constructorMenu\.js'\) \}\}\?v=\d/.test(idx));

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
