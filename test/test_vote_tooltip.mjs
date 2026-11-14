/**
 * Vote-button tooltips (VOTE_SIGNAL_PLAN §11a) — the per-engine reading gate.
 *
 * Executes the SHIPPED VOTE_TIP table and composeVoteTip from ui.js (extracted
 * verbatim, never grepped for strings — a grep passes on a table nothing renders).
 *
 *   node test/test_vote_tooltip.mjs
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (name, got, want = true) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (ok) pass++; else { fail++; console.log(`  ✗ ${name}\n      got=${JSON.stringify(got)} want=${JSON.stringify(want)}`); }
};

const src = readFileSync('static/js/ui.js', 'utf8');
const utip = readFileSync('static/js/handlers/uTip.js', 'utf8');
const css = readFileSync('static/css/trust.css', 'utf8');

// Extract the table + the composer VERBATIM and run them.
const grab = (decl, name) => {
    const re = new RegExp(`(?:^|\\n)(?:export )?${decl} ${name}\\b[\\s\\S]*?\\n\\}`, 'm');
    const m = src.match(re);
    if (!m) throw new Error(`could not extract ${name} from ui.js`);
    return m[0];
};
const tableSrc = (src.match(/const VOTE_TIP = \{[\s\S]*?\n\};/) || [])[0];
if (!tableSrc) throw new Error('could not extract VOTE_TIP from ui.js');
const composeSrc = grab('function', 'composeVoteTip').replace(/^\s*export /m, '');

// eslint-disable-next-line no-new-func
const { VOTE_TIP, composeVoteTip } = new Function(
    `${tableSrc}\n${composeSrc}\nreturn { VOTE_TIP, composeVoteTip };`)();

const CLASSES = ['tool_enabled', 'rag_focused', 'genie', 'llm_only'];

// ── shape: a lead and a quieter note, never a wall of equal sentences ────────
for (const cls of CLASSES) {
    for (const dir of ['up', 'down']) {
        const tip = composeVoteTip(dir, cls);
        check(`${cls}/${dir} returns the two tiers`,
            typeof tip.lead === 'string' && typeof tip.note === 'string', true);
        check(`${cls}/${dir} lead is ONE line`, tip.lead.includes('\n'), false);
        check(`${cls}/${dir} note is ONE line`, tip.note.includes('\n'), false);
        check(`${cls}/${dir} lead is non-empty`, tip.lead.trim().length > 0, true);
        // The screenshot defect: a tooltip so long it reads as a paragraph.
        check(`${cls}/${dir} lead stays scannable (<= 90 chars)`, tip.lead.length <= 90, true);
        check(`${cls}/${dir} note stays a footnote (<= 70 chars)`, tip.note.length <= 70, true);
    }
}

// ── the redundancy that made it ugly must not come back ──────────────────────
// The button already reads "Helpful"/"Unhelpful"; leading with "Mark this answer
// as helpful" restates the control's own label and buries the actual fact.
for (const cls of CLASSES) {
    for (const dir of ['up', 'down']) {
        const { lead } = composeVoteTip(dir, cls);
        const engine = VOTE_TIP[cls][dir];
        if (engine) {
            check(`${cls}/${dir} leads with the CONSEQUENCE, not the button's own label`,
                lead, engine);
            check(`${cls}/${dir} never restates "Mark this answer as…"`,
                /Mark this answer as/.test(lead), false);
        } else {
            // No shipped consequence ⇒ the plain act is the honest lead.
            check(`${cls}/${dir} with no consequence falls back to the plain act`,
                lead, dir === 'up' ? VOTE_TIP.base_up : VOTE_TIP.base_down);
        }
    }
}

// ── the two directions must differ ───────────────────────────────────────────
for (const cls of CLASSES) {
    check(`${cls}: up and down do not say the same thing`,
        composeVoteTip('up', cls).lead !== composeVoteTip('down', cls).lead, true);
}
check('the acts themselves differ', VOTE_TIP.base_up !== VOTE_TIP.base_down, true);
// Genie's MECHANISM is direction-symmetric (propagation carries either rating), and
// that once made its two tooltips byte-identical — two buttons showing the same text
// reads as a bug, so the wording must still differentiate.
check('genie propagates in both directions', CLASSES.includes('genie')
    && /through to the expert/.test(VOTE_TIP.genie.up)
    && /through to the expert/.test(VOTE_TIP.genie.down), true);
check('…and EVERY class differentiates its directions in words',
    CLASSES.every((c) => (VOTE_TIP[c].up || 'x') !== (VOTE_TIP[c].down || 'y')), true);

// ── an unknown engine degrades honestly ──────────────────────────────────────
for (const unknown of [null, undefined, 'conversation_with_tools_typo', 'flow', '']) {
    const tip = composeVoteTip('down', unknown);
    check(`unknown engine (${String(unknown)}) ⇒ the plain act, no invented consequence`,
        tip.lead, VOTE_TIP.base_down);
    check(`unknown engine (${String(unknown)}) still carries the boundary`,
        tip.note, VOTE_TIP.note);
}
// The runtime class `conversation_with_tools` is not a STORED profile type — it
// arrives as llm_only, and must therefore get llm_only's reading.
check('llm_only carries the memory consequence (the conversation lane)',
    /auto-learned/.test(composeVoteTip('down', 'llm_only').lead), true);

// ── the promise rule: every consequence named is one that SHIPPED ────────────
check('Optimize names the champion plan', /plan behind this answer/.test(VOTE_TIP.tool_enabled.down), true);
check('Focus names Corpus Health', /Corpus Health/.test(VOTE_TIP.rag_focused.down), true);
check('Coordinate names expert propagation', /through to the expert/.test(VOTE_TIP.genie.down), true);
check('the note states the preference/measurement split (Rule 3bb)',
    /never changes them/.test(VOTE_TIP.note), true);
// A tooltip must not promise the opt-in judge run, which ships OFF.
check('no tip promises the judge diagnosis (it is opt-in and default OFF)',
    CLASSES.every((c) => !/judge/i.test(VOTE_TIP[c].up + VOTE_TIP[c].down))
    && !/judge/i.test(VOTE_TIP.note), true);

// ── wiring: the platform tooltip, both tiers, aria intact, above the pill ────
check('the vote button hosts the platform tooltip', /feedback-btn u-tip/.test(src), true);
check('…and asks to sit ABOVE, so it cannot cover the capsule link beneath it',
    /feedback-btn u-tip u-tip-above/.test(src), true);
check('…carrying the lead', /setAttribute\('data-tip', tip\.lead\)/.test(src), true);
check('…and the note as the second tier', /setAttribute\('data-tip-note', tip\.note\)/.test(src), true);
check('…with no native title tooltip on the vote button',
    /feedback-btn[\s\S]{0,700}?btn\.title\s*=/.test(src), false);
check('the aria labels are unchanged',
    /aria-label', dir === 'up' \? 'Mark answer as helpful' : 'Mark answer as unhelpful'/.test(src), true);
check('the engine is resolved once per turn, not per button',
    src.indexOf('const voteEngine = _currentTurnEngine();') < src.indexOf('const makeBtn = (dir)'), true);
check('…from the shared tag→type resolver, not a second map',
    /profileTypeForTag as getProfileTypeForTag/.test(src), true);

// ── the renderer honours both tiers and the placement, safely ────────────────
check('uTip renders the note as its own element', /class(?:Name)?\s*=\s*'u-tip-note'/.test(utip), true);
check('…via textContent, never markup', /n\.textContent = note/.test(utip), true);
check('…and clears the layer between tips', /t\.textContent = ''/.test(utip), true);
check('uTip honours an above-placement request', /u-tip-above/.test(utip), true);
check('…and still flips when there is no room above',
    /if \(y < MARGIN\) y = r\.bottom \+ GAP;/.test(utip), true);
// Extract the RULE BLOCK and assert inside it. `#u-tip-layer .u-tip-note[\s\S]*?prop`
// is unbounded — it happily matches a property hundreds of lines later in the
// stylesheet, so both of these passed while the rule had been gutted. A bound is not
// an assertion; the block is.
const noteRule = (css.match(/#u-tip-layer \.u-tip-note\s*\{[^}]*\}/) || [''])[0];
check('the note rule exists', noteRule.length > 0, true);
check('the note is styled quieter than the lead', /color:\s*var\(--text-muted/.test(noteRule), true);
check('…and smaller than it', /font-size:\s*0\.6\d*rem/.test(noteRule), true);
check('…and separated by a hairline, so the hierarchy is visible before reading',
    /border-top:\s*1px solid/.test(noteRule), true);

// ── EXECUTE the real renderer: structure + placement, not just source text ───
// The complaint was visual, so the shape the user actually sees is driven here
// through uTip's own `show()` under a DOM stub — a source grep proves nothing
// about what renders.
{
    const made = [];
    const mkEl = (tag) => {
        const e = {
            tagName: tag.toUpperCase(), className: '', id: '', children: [],
            style: { cssText: '', setProperty() {} },
            attrs: {}, _text: '',
            set textContent(v) { this._text = v; this.children.length = 0; },
            get textContent() { return this._text; },
            setAttribute(k, v) { this.attrs[k] = v; },
            getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
            appendChild(c) { this.children.push(c); return c; },
            getBoundingClientRect: () => ({ left: 0, top: 0, right: 0, bottom: 0, width: 220, height: 60 }),
            get isConnected() { return true; },
        };
        made.push(e);
        return e;
    };
    const listeners = {};
    global.document = {
        createElement: mkEl,
        body: { appendChild() {} },
        documentElement: { classList: { add() {} } },
        addEventListener: (n, fn) => { listeners[n] = fn; },
    };
    global.window = { innerWidth: 1400, innerHeight: 900, addEventListener() {} };

    const mod = readFileSync('static/js/handlers/uTip.js', 'utf8');
    // eslint-disable-next-line no-new-func
    new Function('document', 'window', mod)(global.document, global.window);

    const host = (cls, tip, note, rect) => ({
        classList: { contains: (c) => cls.split(' ').includes(c) },
        getAttribute: (k) => (k === 'data-tip' ? tip : k === 'data-tip-note' ? note : null),
        closest: () => null,
        getBoundingClientRect: () => rect,
    });

    // Drive the real mouseover → show() path (the dwell timer is bypassed by
    // calling the handler and then the timer body via a zero-delay flush).
    const realSetTimeout = global.setTimeout;
    global.setTimeout = (fn) => { fn(); return 0; };
    const target = host('u-tip u-tip-above', 'Retires the plan behind this answer.',
        'Recorded beside the quality scores — never changes them.',
        { left: 600, top: 400, right: 700, bottom: 424, width: 100, height: 24 });
    listeners.mouseover({ target: { closest: () => target } });
    global.setTimeout = realSetTimeout;

    const layer = made.find((e) => e.id === 'u-tip-layer');
    check('the tooltip layer was created', !!layer, true);
    check('…and renders TWO tiers, not one blob', layer.children.length, 2);
    check('…the lead first', layer.children[0].textContent,
        'Retires the plan behind this answer.');
    check('…the note second, in its own class', layer.children[1].className, 'u-tip-note');
    check('…carrying the boundary', layer.children[1].textContent,
        'Recorded beside the quality scores — never changes them.');
    // The screenshot defect: the tip covered "Why trust this answer?" underneath.
    const top = parseFloat(layer.style.top);
    check('…positioned ABOVE the pill (400 - 60 - 7 = 333)', top, 333);
    check('…so it cannot cover what sits below the buttons', top + 60 <= 400, true);
    check('…and horizontally centred on the target', parseFloat(layer.style.left), 540);

    // A plain tip (no note) must render exactly one tier — the shared system is
    // unchanged for every other surface.
    const plain = host('u-tip', 'Just one line.', null,
        { left: 10, top: 10, right: 60, bottom: 30, width: 50, height: 20 });
    listeners.mouseout({ relatedTarget: null });
    global.setTimeout = (fn) => { fn(); return 0; };
    listeners.mouseover({ target: { closest: () => plain } });
    global.setTimeout = realSetTimeout;
    check('a tip with no note renders ONE tier (other surfaces unchanged)',
        layer.children.length, 1);
    check('…and the previous note did not linger', layer.children[0].textContent, 'Just one line.');
}

// ── one action row: the vote pill and the capsule toggle share it ────────────
// They are the same kind of affordance (what you do WITH the answer); stacked, they
// cost a row and split across two alignments.
{
    const capsule = readFileSync('static/js/handlers/answerCapsule.js', 'utf8');
    check('ui.js builds ONE action row with a slot for the capsule',
        /msg-actions-row[\s\S]{0,400}msg-actions-slot/.test(src), true);
    check('…laid out as left slot / right pill',
        /msg-actions-row[^']*flex items-center justify-between/.test(src), true);
    check('the capsule mounts its toggle INTO that slot', /\.msg-actions-slot/.test(capsule), true);
    check('…capturing the button before moving it (or the handler would bind null)',
        capsule.indexOf("const btn = holder.querySelector('.ac-toggle')")
        < capsule.indexOf('slot.appendChild(btn)'), true);
    check('…and keeping the expandable body full-width below the row',
        /slot\.appendChild\(btn\)[\s\S]{0,200}container\.appendChild\(holder\)/.test(capsule), true);
    // Absence-tolerance: the capsule must still work on any render path with no row.
    check('no row ⇒ the capsule falls back to its own line',
        /if \(slot\) \{/.test(capsule), true);
    // The holder keeps the ids the live review-refresh path looks it up by.
    check('the holder still carries the ids the refresh path resolves',
        /holder\.dataset\.acSid/.test(capsule) && /\.answer-capsule-holder/.test(capsule), true);
}

// ── the RAG case browser keeps its own, different vote surface ───────────────
check('the case-browser buttons are untouched by this change',
    /case-feedback-btn/.test(src) && !/case-feedback-btn[^`]*composeVoteTip/.test(src), true);

console.log(fail
    ? `\nFAILED — ${pass} passed, ${fail} failed`
    : `\nALL PASS — ${pass} assertions (a vote button says what the vote will do).`);
process.exit(fail ? 1 : 0);
