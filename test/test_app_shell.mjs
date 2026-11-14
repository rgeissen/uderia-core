#!/usr/bin/env node
/**
 * The app is a fixed shell pinned to the viewport — the iPad "the whole page moves" gate.
 *
 * Laid out in the document flow, the app was something iPad Safari could drag, bounce or pan:
 * the top bar went off-screen, a white band showed below, with or without the resource panel
 * open. Sizing it (100vh → 100dvh) and putting the page back after Safari moved it (a scroll
 * guard) treated the symptom; the cause is that the app was IN the scrollable document at all.
 * The body is now a fixed box — not part of anything Safari scrolls — placed and sized by the area
 * the browser actually shows (appViewport.js → --app-top/--app-h), ALWAYS: the on-screen keyboard
 * and the strip iPadOS reserves for a hardware keyboard's shortcut bar both shrink the shell
 * instead of covering the composer, and a pan of the visible area is put back (a threshold that
 * ignored the strip let the browser pan the page up by it and show white beneath); the guard is gone.
 *
 * Asserted here: the shell, no height or vh unit on it, no class on <body> that would override
 * it, no page-scroll guard left behind, and the support readout only behind its flag. No server.
 *
 * Run: node test/test_app_shell.mjs
 */
import fs from 'node:fs';
import vm from 'node:vm';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };
const read = (p) => fs.readFileSync(p, 'utf8');

const CSS = read('static/css/main.css');
const T = read('templates/index.html');

const htmlRule = (CSS.match(/\nhtml \{[^}]*\}/) || [''])[0];
const bodyRule = (CSS.match(/\nbody \{[^}]*\}/) || [''])[0];
check('the body is the app shell: fixed, spanning the width, placed and sized by the visible area',
    /position: fixed;/.test(bodyRule) && /left: 0;/.test(bodyRule) && /right: 0;/.test(bodyRule)
    && /top: var\(--app-top, 0px\);/.test(bodyRule) && /height: var\(--app-h, 100%\);/.test(bodyRule));
check('…never a vh/dvh unit: on iOS 100vh is the largest viewport and overshoots the visible one', !/\d+d?vh/.test(bodyRule));
const A = read('static/js/appViewport.js');
const headEnd = T.indexOf('</head>');
check('the visible area is published before the body is laid out (appViewport.js in <head>, pinned)',
    /<script src="\/static\/js\/appViewport\.js\?v=[0-9.]+"><\/script>/.test(T)
    && T.indexOf('appViewport.js') > 0 && T.indexOf('appViewport.js') < headEnd);
check('…from visualViewport, following the keyboard and Safari\'s pans',
    /root\.setProperty\('--app-h', h\)/.test(A) && /root\.setProperty\('--app-top', top\)/.test(A)
    && /vv\.addEventListener\('resize', settle\)/.test(A) && /vv\.addEventListener\('scroll', settle\)/.test(A));
check('…leaving a pinch-zoom alone (variables cleared, the shell is the window)',
    /if \(vv\.scale > 1\.01\) return clear\(\);/.test(A)
    && /function clear\(\) \{ root\.removeProperty\('--app-h'\); root\.removeProperty\('--app-top'\); \}/.test(A));
check('…while a field is focused the shell is the visible area; with none it is the whole window (the layout height, no 100vh probe)',
    !/100vh/.test(A) && /const layoutH = html\.clientHeight \|\| 0;/.test(A)
    && /const shellH = focused \|\| !layoutH \? vv\.height : layoutH - offset;/.test(A) && !/--keyboard-strip/.test(A));
check('…and a pan of the visible area is put back (the browser revealing a field, a drag into the keyboard\'s room)',
    /window\.scrollTo\(0, 0\);/.test(A) && /vv\.offsetTop > 0\.5 \|\| vv\.offsetLeft > 0\.5 \|\| window\.scrollY > 0\.5 \|\| window\.scrollX > 0\.5/.test(A)
    && /window\.addEventListener\('scroll', settle\)/.test(A) && /document\.addEventListener\('focusin', settle\)/.test(A));
check('the document behind the shell wears the theme background (the keyboard\'s room is never a white band)',
    /background: var\(--bg-primary, #141619\);/.test(htmlRule));
check('…and writing nothing when nothing changed (no style churn on every pan event)',
    /if \(root\.getPropertyValue\('--app-h'\) !== h\)/.test(A));
check('…and neither it nor the document is a scroll container',
    /overflow: hidden;/.test(bodyRule) && /overflow: hidden;/.test(htmlRule)
    && /overscroll-behavior: none;/.test(bodyRule) && /overscroll-behavior: none;/.test(htmlRule));
const bodyTag = (T.match(/<body[^>]*>/) || [''])[0];
check('no class on <body> overrides the shell (h-screen, a height, a position)',
    !!bodyTag && !/\b(h-screen|h-\[|min-h-|relative|static|absolute|sticky)\b/.test(bodyTag));
check('no page-scroll guard is left behind (it treated the symptom)',
    !/pageScrollGuard/.test(T) && !fs.existsSync('static/js/pageScrollGuard.js'));
const D = read('static/js/viewportDiag.js');
check('the support readout is loaded and is a no-op without ?scrolldiag=1, never taking a tap',
    /<script src="\/static\/js\/viewportDiag\.js\?v=[0-9.]+"><\/script>/.test(T)
    && /!\/\[\?&\]scrolldiag=1\\b\/\.test\(location\.search\)\) return;/.test(D) && /pointer-events:none/.test(D));

// ── behaviour: the script against a visualViewport stub (keyboard, shortcut bar, pan, close, zoom) ──
{
    const props = new Map(), listeners = {}, raf = [], scrolled = [];
    const vv = { height: 820, offsetTop: 0, offsetLeft: 0, scale: 1, addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); } };
    const win = { visualViewport: vv, scrollX: 0, scrollY: 0, addEventListener: (t, f) => { (listeners['w:' + t] = listeners['w:' + t] || []).push(f); },
        scrollTo: (x, y) => { scrolled.push([x, y]); win.scrollX = x; win.scrollY = y; vv.offsetTop = y; vv.offsetLeft = x; } };
    const ctx = { window: win, requestAnimationFrame: (f) => { raf.push(f); return raf.length; }, setTimeout: () => 0, clearTimeout: () => {},
        document: { addEventListener: (t, f) => { (listeners['d:' + t] = listeners['d:' + t] || []).push(f); }, documentElement: {
            style: { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k), getPropertyValue: (k) => props.get(k) || '' } } } };
    vm.runInNewContext(A, ctx);
    const fire = (t) => { (listeners[t] || []).forEach(f => f()); while (raf.length) raf.shift()(); };
    check('at load the shell is the visible area (820): published, not left to the page', props.get('--app-h') === '820px' && props.get('--app-top') === '0px');
    vv.height = 753; fire('resize');
    check('a hardware keyboard\'s shortcut strip (67 px) shrinks the shell like a keyboard: the composer sits above it', props.get('--app-h') === '753px');
    vv.offsetTop = 67; fire('scroll');
    check('…the browser pans the page up by the strip to reveal the field: the pan is put back at once',
        scrolled.length === 1 && scrolled[0][0] === 0 && scrolled[0][1] === 0 && vv.offsetTop === 0 && props.get('--app-top') === '0px');
    vv.height = 470; fire('resize');
    check('the on-screen keyboard opens (visible 470): the shell becomes the space above it', props.get('--app-h') === '470px' && props.get('--app-top') === '0px');
    win.scrollTo = (x, y) => { scrolled.push([x, y]); };                   // a pan the browser keeps (nothing moves)
    vv.offsetTop = 120; fire('scroll');
    check('a pan that survives the put-back: the shell follows it, so the app still covers exactly the visible area', props.get('--app-top') === '120px' && scrolled.length === 2);
    vv.offsetTop = 0; vv.height = 820; fire('resize');
    check('the keyboard closes: the shell is the full visible area again, at the top', props.get('--app-h') === '820px' && props.get('--app-top') === '0px');
    fire('d:focusin'); fire('w:scroll');
    check('a field taking focus and the document moving are both reasons to re-apply (listeners wired)', !!listeners['d:focusin'] && !!listeners['d:focusout'] && !!listeners['w:scroll']);
    vv.scale = 1.6; vv.height = 300; vv.offsetTop = 40; const before = scrolled.length; fire('resize');
    check('a pinch-zoom is left alone: the variables are cleared and the pan is the user\'s (no scrollTo)', !props.has('--app-h') && !props.has('--app-top') && scrolled.length === before);
}

// ── the keyboard closes in Chrome on iPad (a WKWebView): the put-back is overridden while the keyboard
//    animates and no event follows — the series of re-applies must still bring the page back ──
{
    const props = new Map(), listeners = {}, raf = [], timers = [];
    let now = 0, animatingUntil = 0;
    const vv = { height: 820, offsetTop: 0, offsetLeft: 0, scale: 1, addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); } };
    const win = { visualViewport: vv, scrollX: 0, scrollY: 0, addEventListener: (t, f) => { (listeners['w:' + t] = listeners['w:' + t] || []).push(f); },
        scrollTo: (x, y) => { if (now < animatingUntil) return; win.scrollX = x; win.scrollY = y; vv.offsetTop = y; } };   // overridden mid-animation
    const ctx = { window: win, requestAnimationFrame: (f) => { raf.push(f); return raf.length; },
        setTimeout: (f, ms) => { timers.push({ at: now + ms, f }); return timers.length; }, clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].f = null; },
        document: { addEventListener: (t, f) => { (listeners['d:' + t] = listeners['d:' + t] || []).push(f); }, documentElement: {
            style: { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k), getPropertyValue: (k) => props.get(k) || '' } } } };
    vm.runInNewContext(A, ctx);
    const flush = () => { while (raf.length) raf.shift()(); };
    const advance = (ms) => { const end = now + ms; for (;;) { const due = timers.filter(t => t.f && t.at <= end).sort((a, b) => a.at - b.at)[0]; if (!due) break; now = due.at; const f = due.f; due.f = null; f(); flush(); } now = end; };
    const fire = (t) => { (listeners[t] || []).forEach(f => f()); flush(); };
    // the keyboard opens: WebKit pans the page up 300 px to reveal the composer, mid-animation
    animatingUntil = 400; vv.height = 470; win.scrollY = 300; vv.offsetTop = 300; fire('resize'); fire('scroll');
    check('Chrome/iPad: while the keyboard animates the put-back is overridden — the shell follows the pan meanwhile',
        props.get('--app-h') === '470px' && props.get('--app-top') === '300px');
    advance(1300);
    check('…and once the opening animation is over the page is put back by the series, with no further event', win.scrollY === 0 && props.get('--app-top') === '0px');
    // the keyboard closes: one resize mid-animation, the page left moved up (WKWebView), then silence
    now = 5000; animatingUntil = 5450; win.scrollY = 300; vv.offsetTop = 300; vv.height = 820; fire('resize');
    check('…the keyboard closes and the one event comes mid-animation: the put-back is overridden', win.scrollY === 300);
    advance(1300);
    check('…no event follows, yet the series brings the page back: not moved up, the shell the whole screen',
        win.scrollY === 0 && vv.offsetTop === 0 && props.get('--app-top') === '0px' && props.get('--app-h') === '820px');
    check('…every viewport event starts the series; the keyboard\'s hide key (no focusout) is caught by the next touch',
        /vv\.addEventListener\('resize', settle\)/.test(A) && /vv\.addEventListener\('scroll', settle\)/.test(A)
        && /document\.addEventListener\('focusout', settle\)/.test(A) && /document\.addEventListener\('touchend', schedule, \{ passive: true \}\)/.test(A)
        && /const SETTLE_MS = \[60, 180, 400, 750, 1200\];/.test(A));
}

// ── Chrome on iPad: the keyboard is hidden with the field still focused (the ◞ strip), and the reduced
//    visible height stays reported after it is gone — even across reloads in the tab ──
{
    const props = new Map(), listeners = {}, raf = [];
    const doc = { body: {}, activeElement: null, addEventListener: (t, f) => { (listeners['d:' + t] = listeners['d:' + t] || []).push(f); },
        documentElement: { clientHeight: 820, style: { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k), getPropertyValue: (k) => props.get(k) || '' } } };
    let blurred = 0;
    const input = { tagName: 'TEXTAREA', readOnly: false, disabled: false, blur: () => { blurred++; doc.activeElement = doc.body; (listeners['d:focusout'] || []).forEach(f => f()); } };
    doc.activeElement = doc.body;
    const vv = { height: 820, offsetTop: 0, offsetLeft: 0, scale: 1, addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); } };
    const win = { visualViewport: vv, scrollX: 0, scrollY: 0, addEventListener: () => {}, scrollTo: () => {} };
    let clock = 0;
    const run = () => vm.runInNewContext(A, { window: win, document: doc, requestAnimationFrame: (f) => { raf.push(f); return raf.length; },
        setTimeout: () => 0, clearTimeout: () => {}, getComputedStyle: () => ({ position: 'static' }), Date: { now: () => clock } });
    run();
    const fire = (t) => { (listeners[t] || []).forEach(f => f()); let n = 0; while (raf.length && n++ < 20) raf.shift()(); };
    doc.activeElement = input; fire('d:focusin'); vv.height = 470; fire('resize');
    check('the on-screen keyboard (350 px) shrinks the shell to the space above it', props.get('--app-h') === '470px' && blurred === 0);
    clock = 1000; vv.height = 760; fire('resize'); clock = 1016; vv.height = 470; fire('resize');
    check('…a rotation passes through one frame that looks like a hidden keyboard: the field keeps its focus', blurred === 0 && props.get('--app-h') === '470px');
    clock = 2000; vv.height = 740; fire('resize'); clock = 2400; fire('resize');
    check('…hidden with the field still focused (Chrome, the ◞ strip): typing is over — the field is let go and the shell is the whole window',
        blurred === 1 && doc.activeElement === doc.body && props.get('--app-h') === '820px');
    check('…never a full-height shell under the strip while focused (Chrome panned the page up by the strip to reveal the field)',
        !/--keyboard-strip/.test(read('static/css/main.css')) && !/--keyboard-strip/.test(A));
    doc.activeElement = input; fire('d:focusin'); vv.height = 740; fire('resize'); clock = 5000; fire('resize');
    check('a hardware keyboard (only the strip, never a keyboard): the field keeps its focus, the shell stands above the strip',
        blurred === 1 && props.get('--app-h') === '740px');
    doc.activeElement = doc.body; fire('d:focusout');
    check('…the field blurs but Chrome still reports the strip: no field, no keyboard — the whole window', props.get('--app-h') === '820px');
    props.clear(); for (const k of Object.keys(listeners)) delete listeners[k]; run();
    check('…a reload with the stale visible height (the tab keeps it until closed): the whole window at once', props.get('--app-h') === '820px');
}

// ── a field on a modal (fixed to the window, not the shell): the browser's pan is kept while the keyboard
//    would cover it, and put back once the keyboard closes ──
{
    const props = new Map(), listeners = {}, raf = [], scrolled = [];
    const field = { getBoundingClientRect: () => ({ top: 560, bottom: 600 }), parentElement: null };
    const overlay = { _pos: 'fixed', parentElement: null };
    field.parentElement = overlay;
    const body = {}; overlay.parentElement = body;
    const html = { style: { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k), getPropertyValue: (k) => props.get(k) || '' } };
    const doc = { body, documentElement: html, activeElement: field, addEventListener: (t, f) => { (listeners['d:' + t] = listeners['d:' + t] || []).push(f); } };
    const vv = { height: 820, offsetTop: 0, offsetLeft: 0, scale: 1, addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); } };
    const win = { visualViewport: vv, scrollX: 0, scrollY: 0, addEventListener: () => {}, scrollTo: (x, y) => { scrolled.push(y); win.scrollY = y; vv.offsetTop = y; } };
    const ctx = { window: win, document: doc, requestAnimationFrame: (f) => { raf.push(f); return raf.length; }, setTimeout: () => 0, clearTimeout: () => {},
        getComputedStyle: (el) => ({ position: el._pos || 'static' }) };
    vm.runInNewContext(A, ctx);
    const fire = (t) => { (listeners[t] || []).forEach(f => f()); while (raf.length) raf.shift()(); };
    vv.height = 470; vv.offsetTop = 260; win.scrollY = 260; fire('resize'); fire('scroll');
    check('a modal field the keyboard would cover keeps the browser\'s pan (putting it back hid the field being typed into)',
        scrolled.length === 0 && props.get('--app-top') === '260px');
    vv.height = 820; fire('resize');
    check('…the keyboard closes: the field fits, so the pan is put back like any other', scrolled.length === 1 && scrolled[0] === 0 && props.get('--app-top') === '0px');
    overlay._pos = 'static'; vv.height = 470; vv.offsetTop = 260; win.scrollY = 260; fire('resize');
    check('a field in the shell never keeps a pan (the shell is the visible area; its scrollers reveal the field)', scrolled.length === 2);
}

// ── the sign-in pages follow the keyboard too (their body was relative + min-height 100vh: never the shell) ──
for (const page of ['templates/login.html', 'templates/register.html']) {
    const P = read(page); const head = P.slice(0, P.indexOf('</head>'));
    check(`${page}: appViewport.js in <head>, the body an auth-page (no min-h-screen, no flex centring)`,
        /<script src="\/static\/js\/appViewport\.js\?v=[0-9.]+"><\/script>/.test(head) && /<body class="auth-page [^"]*">/.test(P)
        && !/<body[^>]*\bmin-h-screen\b/.test(P));
}
check('an auth page is the fixed shell, scrolls inside it, centres with auto margins, and its glows add no scroll room',
    /body\.auth-page \{\s*position: fixed;[^}]*min-height: 0;[^}]*overflow-y: auto;/.test(CSS)
    && /body\.auth-page > div:first-of-type \{ margin-block: auto; flex-shrink: 0; \}/.test(CSS)
    && /body\.auth-page::before, body\.auth-page::after \{ position: fixed; pointer-events: none; \}/.test(CSS));

// ── iOS zooms into a text field under 16px on focus (the cause of "shifted up / cut right and bottom") ──
{
    const iosBlock = (css) => { const i = css.indexOf('@supports (-webkit-touch-callout: none) {'); return i < 0 ? '' : css.slice(i, css.indexOf('}\n}', i) + 3); };
    const blk = iosBlock(CSS);
    check('on iOS every field that can be typed into is 16px (main.css, the iOS-only @supports block)',
        /input:not\(\[type="checkbox"\]\)[\s\S]*textarea, select, \[contenteditable=""\], \[contenteditable="true"\][\s\S]*font-size: 16px !important;/.test(blk));
    for (const page of ['templates/index.html', 'templates/login.html', 'templates/register.html']) {
        check(`${page} loads main.css with a version pin (a cached copy would keep the zoom)`, /css\/main\.css['"]?\)? ?\}?\}?\?v=[0-9.]+/.test(read(page)) || /main\.css\?v=[0-9.]+/.test(read(page)));
    }
    for (const page of ['templates/reset_password.html', 'templates/verify_email.html']) {
        check(`${page} (no main.css) carries the same 16px rule`, /@supports \(-webkit-touch-callout: none\)[\s\S]{0,400}font-size: 16px !important/.test(read(page)));
    }
    const metas = fs.readdirSync('templates').filter(f => f.endsWith('.html')).map(f => read('templates/' + f)).join('\n').match(/<meta name="viewport"[^>]*>/g) || [];
    check('no page disables pinch-to-zoom (no maximum-scale / user-scalable=no — the zoom is fixed at the fields, not by taking zoom away)',
        metas.length > 0 && metas.every(m => !/maximum-scale|user-scalable\s*=\s*(no|0)/i.test(m)));
}

// ── nothing in the shell can be scrolled; the composer is never pushed out; the chat never slides ──
// Found by laying the real app out in WebKit at iPad sizes (test/ipad_layout_probe.py): a prose
// table 2,100 px wide pushed the conversation past the right edge (the wrap was overflow:visible
// and an id-selector rule put the table back to display:table, so nothing scrolled); with the
// keyboard up in landscape (~350 px) the composer's 40vh cap and an open resource panel ran past
// the bottom, every box up to <body> let it, and iOS then scrolled the overflow:hidden shell to
// reveal the focused field — the top cut too.
{
    const rule = (css, sel) => { const i = css.indexOf('\n' + sel + ' {'); return i < 0 ? '' : css.slice(i, css.indexOf('}', i) + 1); };
    check('the shell is overflow:clip (hidden is still a scroll container iOS and scrollIntoView move), with hidden as the pre-16 fallback',
        /overflow: hidden;\s*overflow: clip;/.test(bodyRule));
    check('a collapsed side panel clips and is never scrolled (a step scrolled into view moved its content 310 px)',
        /#status-window\.collapsed, #session-history-panel\.collapsed \{[^}]*overflow: hidden;\s*overflow: clip;/.test(CSS));
    const th = rule(CSS, '#tool-header');
    check('the resource panel yields to the composer: it may shrink, clips, and its drawer scrolls inside it',
        /flex-shrink: 1;/.test(th) && /min-height: 0;/.test(th) && /overflow: clip;/.test(th)
        && /#resource-panel-root \{[^}]*min-height: 0;[^}]*overflow: hidden;/.test(CSS));
    check('the conversation column has an id and no min-h-0 (its minimum is the composer\'s)',
        /<div id="conversation-column" class="flex-1 flex flex-col">\s*<div id="chat-canvas-split"/.test(T));
    check('…its minimum is the composer\'s real height plus a few lines of chat, never more than the column',
        /#main-content > #conversation-column \{\s*min-height: var\(--column-min, 0px\);/.test(CSS));
    check('…and no tablet rule zeroes it again', !/#main-content > \.flex-1 \{[^}]*min-height: 0/.test(CSS));
    check('the chat never scrolls sideways', /\n#chat-container \{\s*overflow-x: hidden;\s*\}/.test(CSS));
    const UIJS = read('static/js/ui.js'), MAIN = read('static/js/main.js');
    check('the field grows only into the room the conversation can give up, and the composer\'s height is published',
        /Math\.min\(parseFloat\(getComputedStyle\(el\)\.maxHeight\) \|\| Infinity, composerRoom\(el\)\)/.test(UIJS)
        && /const CHAT_MIN_PX = 72;/.test(UIJS) && /main\.clientHeight - heightAboveColumn\(main, column\) - \(fixed - el\.offsetHeight\) - CHAT_MIN_PX/.test(UIJS)
        && /return column\.offsetHeight - chat\.offsetHeight;/.test(UIJS)
        && /c\.id === 'tool-header' \? c\.offsetHeight - c\.clientHeight : c\.offsetHeight/.test(UIJS)
        && /export function publishComposerHeight\(\)/.test(UIJS) && /Math\.min\(fixed \+ CHAT_MIN_PX, room\)/.test(UIJS)
        && /setProperty\('--column-min', h\)/.test(UIJS));
    check('…re-fitted when the column changes size (the iOS keyboard fires no window resize) and republished when the composer does',
        /const refit = new ResizeObserver\(\(\) => UI\.autoResizeChatInput\(\)\);/.test(MAIN) && /refit\.observe\(mainContent\)/.test(MAIN) && /refit\.observe\(column\)/.test(MAIN)
        && /new ResizeObserver\(\(\) => UI\.publishComposerHeight\(\)\)\.observe\(chatFooter\)/.test(MAIN));
    check('…and a sideways offset set from code is put back',
        /if \(chatScroller\.scrollLeft\) chatScroller\.scrollLeft = 0;/.test(MAIN));
    const ANS = read('static/css/answer.css');
    const prose = (ANS.match(/\.message-content \.ans-tw-prose \{[^}]*\}/) || [''])[0];
    check('a prose table scrolls sideways inside its own wrap, never past the message',
        /overflow-x: auto;/.test(prose) && /max-width: 100%;/.test(prose) && !/overflow: visible/.test(prose)
        && !/\.ans-tw-prose > table \{ display: block/.test(ANS));
    const POL = read('static/css/polish.css');
    check('the chat log fills the column (in the flex-column chat, mx-auto turned stretching off: it took its content\'s ~670 px floor)',
        /#chat-log \{ min-width: 0; width: 100%; \}/.test(POL));
    check('a word breaks before it runs past a message — the whole bubble (evidence + sources folds), inline code chips too',
        /#chat-log \.message-bubble \{ min-width: 0; max-width: 100%; overflow-wrap: anywhere; \}/.test(POL)
        && /\.message-content :not\(pre\) > code,\s*\.message-content > code \{(?![^}]*white-space: nowrap)[^}]*overflow-wrap: anywhere;/.test(POL));
    const COMPUTE = read('components/builtin/compute/handler.py');
    check('every answer grid has its scroll wrap (the compute row table was bare and clipped by the evidence fold), and a stored bare one scrolls itself',
        /<div class="ans-tw"><table class="ans-t">/.test(COMPUTE) && !/'<table class="ans-t">/.test(COMPUTE)
        && /\.ans-ev-b > table\.ans-t \{ display: block; max-width: 100%; overflow-x: auto; \}/.test(ANS));
    check('the acts strip wraps on a narrow column and never shrinks a control (the vote group clipped its buttons)',
        /\.msg-actions-row, \.msg-actions-row \.msg-actions-slot \{ flex-wrap: wrap;/.test(ANS)
        && /\.msg-actions-row \.ans-ctl-group, \.msg-actions-row \.la-trust, \.msg-actions-row \.tl-act \{ flex: none; \}/.test(ANS));
}

console.log(fail ? `\n${pass} passed, ${fail} FAILED` : `ALL PASS — ${pass} assertions (the app is a fixed shell)`);
process.exit(fail ? 1 : 0);
