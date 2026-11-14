#!/usr/bin/env node
/**
 * The conversation header carries its own New-session button — the gate.
 *
 * The only way to start a session used to be the "+" in the History panel's header, and that
 * panel is collapsed by default: starting a session meant opening a panel first. The action
 * now sits on the conversation itself, in the header's action row after the trust glyph
 * (the conversation's reading leads the row; then the canvas toggle and the panel menu), the way a tab group's own menu carries
 * "new tab in group". It is the SAME action as the sidebar's — one handler, one
 * disabled-while-a-turn-runs rule — never a second path to a session.
 *
 * Asserted here: the button's place and chrome (an sp-action-btn, monochrome stroke icon,
 * labelled), the one handler, the execution-state rule, and the pins. No server.
 *
 * Run: node test/test_conversation_header.mjs
 */
import fs from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };
const read = (p) => fs.readFileSync(p, 'utf8');

const T = read('templates/index.html');
const DOMJS = read('static/js/domElements.js');
const EH = read('static/js/eventHandlers.js');
const UI = read('static/js/ui.js');

// ---- 1. the button, on the conversation header, first in its action row ----
const actionsStart = T.indexOf('<div id="chat-header-actions"');
const actionsEnd = T.indexOf('</header>', actionsStart);
const actions = actionsStart > 0 ? T.slice(actionsStart, actionsEnd) : '';
check('the conversation header has an action row', actions.length > 0);
const btnMatch = actions.match(/<button id="header-new-session-button"[^>]*>[\s\S]*?<\/button>/);
const btn = btnMatch ? btnMatch[0] : '';
check('…and a New-session button in it', !!btn);
check('…after the trust glyph (the conversation\'s reading leads the row), before the canvas toggle and the panel menu',
    !!btn && actions.indexOf('chat-trust-glyph-host') < actions.indexOf('header-new-session-button')
    && actions.indexOf('header-new-session-button') < actions.indexOf('canvas-mode-toggle'));
check('…an sp-action-btn like its neighbours (one chrome for the header\'s actions)', /class="sp-action-btn"/.test(btn));
check('…type=button (never a submit inside the view)', /type="button"/.test(btn));
check('…named for the pointer and the screen reader', /title="New session"/.test(btn) && /aria-label="New session"/.test(btn));
check('…a monochrome stroke icon (currentColor, no fill colour, no emoji)',
    /stroke="currentColor"/.test(btn) && /fill="none"/.test(btn) && !/fill="#|stroke="#/.test(btn)
    && !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(btn));
check('…the same bubble as the header\'s own accent icon, with a plus in it',
    /M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z/.test(btn) && /M12 7v6M9 10h6/.test(btn));
check('the History panel keeps its own button (the header one is in addition, not instead)', /id="new-chat-button"/.test(T));

// ---- 2. one handler, one rule ----
check('domElements exports the handle', /export const headerNewSessionButton = document\.getElementById\('header-new-session-button'\);/.test(DOMJS));
check('the header button runs the SAME handler as the sidebar\'s (handleStartNewSession, never a second path)',
    /DOM\.newChatButton\.addEventListener\('click', handleStartNewSession\);\s*\n\s*if \(DOM\.headerNewSessionButton\) DOM\.headerNewSessionButton\.addEventListener\('click', handleStartNewSession\);/.test(EH));
const execFn = (UI.match(/export function setExecutionState\(isActive\) \{[\s\S]*?\n\}/) || [''])[0];
check('setExecutionState disables it while a turn runs, beside the sidebar\'s button',
    /DOM\.newChatButton\.disabled = isActive;\s*\n\s*if \(DOM\.headerNewSessionButton\) DOM\.headerNewSessionButton\.disabled = isActive;/.test(execFn));
check('…and a disabled button reads disabled (the global rule dims it)', /button:disabled[^{]*\{[^}]*opacity: 0\.5;[^}]*cursor: not-allowed;/.test(read('static/css/main.css')));

// ---- 3. pins — a cached copy of either script would leave the button inert ----
const pinOf = (re) => { const s = new Set((T.match(re) || []).map(m => m.match(/v=([0-9.]+)/)[1])); return s; };
const ehPins = new Set([...read('static/js/main.js').matchAll(/eventHandlers\.js\?v=([0-9.]+)/g)].map(m => m[1]));
check('eventHandlers.js is pinned once, everywhere (the wiring ships)', ehPins.size === 1 && [...ehPins][0] >= '4.194');
const uiPins = new Set([...EH.matchAll(/[^a-zA-Z]ui\.js\?v=([0-9.]+)/g)].map(m => m[1]));
check('ui.js is pinned once, everywhere (the rule ships)', uiPins.size === 1 && [...uiPins][0] >= '1.260');
check('main.js pin moved with its importers', [...pinOf(/main\.js'\) \}\}\?v=[0-9.]+/g)].every(v => v >= '10.110'));

console.log(fail ? `\n${pass} passed, ${fail} FAILED` : `ALL PASS — ${pass} assertions (the conversation header carries New session)`);
process.exit(fail ? 1 : 0);
