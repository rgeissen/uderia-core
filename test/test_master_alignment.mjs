#!/usr/bin/env node
/**
 * Master switches ↔ profile-editor alignment: a control whose master is OFF
 * says so — and a control whose machinery keeps running is never falsely
 * marked.
 *
 * WHY THIS GATE EXISTS
 * Operator question, generalised into a deep assessment: "if obda is disabled
 * (master switch) shouldn't the associated profile controls be marked as
 * deactivated?" The assessment found the existing `_applyMasterBanners`
 * covered only 2 of the gated cards (the inbound guardrail and the Judges
 * cards were unmarked while genuinely inert), pointed at a STALE admin path
 * ("Operations → Agent Evaluation & Grounding" — the panel moved to Trust),
 * and did not grey anything. It also established the two RUNTIME truths the
 * marking must not blur:
 *
 *   DEACTIVATED — grounding / guardrails (both cards) / judges / recommender:
 *     the runtime short-circuits on the master, so the stored modes are inert.
 *     Marked, greyed (0.55), still EDITABLE (deactivated, not locked).
 *   DEACTIVATED (OBDA-READ) — the data surface: OBDA-READ IS ADDITIVE (operator-restated
 *     charter #15) — master off gates the WHOLE lane at the one parse seam
 *     (contracts unrendered, query_product refuses, enforcement inert), so the
 *     surface block is deactivated + greyed like its siblings. (An earlier
 *     revision of this gate pinned an "observability only" model — that
 *     described the pre-fix GAP, found live in session d51cf073.)
 *   NO MASTER — honesty (incl. grainJudge), the non-surface authority
 *     controls: never marked at all.
 *
 * This gate EXECUTES the shipped `_applyMasterBanners` under a DOM stub that
 * throws on selectors it does not know (a permissive stub measures nothing).
 *
 * Run: node test/test_master_alignment.mjs      (no server, no network)
 */
import { readFileSync } from 'fs';

const src = readFileSync('static/js/handlers/configurationHandler.js', 'utf8');
const rest = readFileSync('src/trusted_data_agent/api/rest_routes.py', 'utf8');

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
    if (cond) { pass++; }
    else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`); }
};

/* ── extract the shipped function ────────────────────────────────────────── */
function sliceFn(marker) {
    const i = src.indexOf(marker);
    if (i < 0) return null;
    let depth = 0, j = src.indexOf('{', i);
    for (let k = j; k < src.length; k++) {
        if (src[k] === '{') depth++;
        else if (src[k] === '}') { depth--; if (depth === 0) return src.slice(i, k + 1); }
    }
    return null;
}
const fnSrc = sliceFn('async function _applyMasterBanners(modal)');
check('the function is extractable', !!fnSrc);
if (!fnSrc) { console.log('\nFAIL — function missing'); process.exit(1); }

/* ── a faithful DOM stub: unknown selectors THROW ────────────────────────── */
class El {
    constructor(tag, id = '') {
        this.tagName = String(tag).toUpperCase();
        this.id = id; this.name = ''; this.htmlFor = '';
        this.children = []; this.parentElement = null;
        this.style = {}; this.textContent = '';
    }
    appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
    prepend(c) { c.parentElement = this; this.children.unshift(c); return c; }
    insertBefore(c, ref) {
        c.parentElement = this;
        const i = this.children.indexOf(ref);
        this.children.splice(i < 0 ? 0 : i, 0, c);
        return c;
    }
    remove() {
        const p = this.parentElement;
        if (p) p.children = p.children.filter(x => x !== this);
    }
    get nextSibling() {
        const p = this.parentElement;
        if (!p) return null;
        const i = p.children.indexOf(this);
        return p.children[i + 1] || null;
    }
    *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
    closest(sel) {
        if (sel !== 'details') throw new Error(`stub: unknown closest(${sel})`);
        let n = this;
        while (n) { if (n.tagName === 'DETAILS') return n; n = n.parentElement; }
        return null;
    }
    querySelector(sel) {
        // exactly the selectors the shipped function uses — anything else throws
        let m;
        if ((m = sel.match(/^input\[name="([^"]+)"\]$/))) {
            for (const n of this.walk()) if (n.tagName === 'INPUT' && n.name === m[1]) return n;
            return null;
        }
        if ((m = sel.match(/^#([\w-]+)$/))) {
            for (const n of this.walk()) if (n.id === m[1]) return n;
            return null;
        }
        if (sel === 'summary h3') {
            for (const n of this.walk())
                if (n.tagName === 'H3' && n.parentElement?.tagName === 'SUMMARY') return n;
            return null;
        }
        if (sel === 'summary + div, summary ~ div') {
            // the card body: the first div sibling after the summary
            const kids = this.children;
            const si = kids.findIndex(k => k.tagName === 'SUMMARY');
            for (let i = si + 1; i >= 0 && i < kids.length; i++)
                if (kids[i].tagName === 'DIV') return kids[i];
            return null;
        }
        if ((m = sel.match(/^label\[for="([^"]+)"\]$/))) {
            for (const n of this.walk()) if (n.tagName === 'LABEL' && n.htmlFor === m[1]) return n;
            return null;
        }
        throw new Error(`stub: unknown querySelector(${sel})`);
    }
}
const doc = { createElement: (t) => new El(t) };

/* one gated card: details > summary > h3, + body div with two sections + control */
function card(ctlName, opts = {}) {
    const d = new El('details');
    const sum = d.appendChild(new El('summary'));
    sum.appendChild(new El('h3')).textContent = opts.title || ctlName;
    const body = d.appendChild(new El('div'));
    const s1 = body.appendChild(new El('div'));
    const inp = s1.appendChild(new El('input'));
    if (opts.byId) inp.id = ctlName; else inp.name = ctlName;
    body.appendChild(new El('div'));        // a second section (greying target)
    return d;
}
function freshModal() {
    const modal = new El('div');
    modal._cards = {
        grounding: modal.appendChild(card('grounding-mode')),
        out: modal.appendChild(card('guardrail-mode')),
        inbound: modal.appendChild(card('guardrail-inbound-mode')),
        judges: modal.appendChild(card('profile-judge-mode')),
        honesty: modal.appendChild(card('honesty-mode')),          // NO master
        recommender: modal.appendChild(card('profile-recommender-enabled', { byId: true })),
    };
    // the Agent Authority card: deny-writes section + the OBDA-READ surface sub-block
    const auth = modal.appendChild(new El('details'));
    const asum = auth.appendChild(new El('summary'));
    asum.appendChild(new El('h3')).textContent = 'Agent Authority';
    const abody = auth.appendChild(new El('div'));
    const dw = abody.appendChild(new El('div'));
    dw.appendChild(new El('input')).id = 'profile-authority-deny-writes';
    const surfBlock = abody.appendChild(new El('div'));
    const lbl = surfBlock.appendChild(new El('label'));
    lbl.htmlFor = 'profile-authority-surface-products';
    surfBlock.appendChild(new El('input')).id = 'profile-authority-surface-products';
    modal._auth = auth; modal._surfBlock = surfBlock; modal._denyWrites = dw;
    return modal;
}

async function run(modal, masters) {
    const fn = new Function('document', '_fetchDeploymentMasters',
        `${fnSrc.replace('async function', 'async function X').replace('X _applyMasterBanners', '_applyMasterBanners')};
         return _applyMasterBanners;`)(doc, async () => masters);
    await fn(modal);
}
const chipOf = (c) => { try { return c.querySelector('summary h3').children.find(x => x.id?.startsWith('master-off-chip')); } catch { return null; } };
const bannerOf = (c) => {
    const body = c.querySelector('summary + div, summary ~ div');
    return body.children.find(x => x.id?.startsWith('master-off-banner')) || null;
};

const ALL_OFF = { grounding_gate_enabled: false, guardrails_enabled: false,
                  online_eval_enabled: false, obda_enabled: false,
                  recommender_enabled: false };
const ALL_ON = { grounding_gate_enabled: true, guardrails_enabled: true,
                 online_eval_enabled: true, obda_enabled: true,
                 recommender_enabled: true };

/* [1] every genuinely-inert card is marked deactivated + greyed, banner legible */
{
    const modal = freshModal();
    await run(modal, ALL_OFF);
    for (const key of ['grounding', 'out', 'inbound', 'judges', 'recommender']) {
        const c = modal._cards[key];
        const chip = chipOf(c), banner = bannerOf(c);
        check(`[1] ${key}: chip reads deactivated`, chip && chip.textContent === 'deactivated — master off');
        check(`[1] ${key}: banner says stored but DEACTIVATED`,
              banner && banner.textContent.includes('DEACTIVATED'));
        check(`[1] ${key}: banner points at the CURRENT admin home`,
              banner && /Administration → App Config → (Trust|Features)/.test(banner.textContent),
              banner ? banner.textContent.slice(-120) : 'no banner');
        const body = c.querySelector('summary + div, summary ~ div');
        const others = body.children.filter(x => x !== banner);
        check(`[1] ${key}: non-banner sections greyed`, others.length &&
              others.every(x => x.style.opacity === '0.55'));
        check(`[1] ${key}: the banner itself is never greyed`,
              banner && banner.style.opacity !== '0.55');
    }
}

/* [2] honesty has NO master and must never be marked */
{
    const modal = freshModal();
    await run(modal, ALL_OFF);
    check('[2] honesty card carries no chip', !chipOf(modal._cards.honesty));
    check('[2] honesty card carries no banner', !bannerOf(modal._cards.honesty));
    const body = modal._cards.honesty.querySelector('summary + div, summary ~ div');
    check('[2] honesty card is never greyed',
          body.children.every(x => x.style.opacity !== '0.55'));
}

/* [3] the OBDA-READ surface — the ADDITIVE imperative: master off ⇒ the WHOLE lane
       is off (contracts unrendered, query_product refuses, enforcement gated at
       the parse seam), so the surface block is DEACTIVATED like its siblings.
       An earlier marking claimed "still ENFORCES" — that described a gap
       (session d51cf073), not a design, and is now banned. */
{
    const modal = freshModal();
    await run(modal, ALL_OFF);
    const note = modal._surfBlock.children.find(x => x.id === 'master-off-note-obda-surface');
    check('[3] the surface block gets the deactivated note', !!note);
    check('[3] the note states the additive baseline (behaves as if not installed)',
          note && note.textContent.includes('as if OBDA-READ were not installed'));
    check('[3] the note says the surface does NOT confine',
          note && note.textContent.includes('does not confine'));
    check('[3] the note says DEACTIVATED', note && note.textContent.includes('DEACTIVATED'));
    // comment-aware: the function's own comment RECORDS the retired wording as
    // history — prose about the code is not the code (the partition gate's rule)
    const fnCode = fnSrc.split('\n').map(l => l.replace(/\/\/.*$/, '')).join('\n');
    check('[3] the still-ENFORCES claim is dead in RENDERED text',
          !fnCode.includes('still ENFORCES'),
          'that wording described the pre-fix gap as a design');
    check('[3] the surface controls are greyed like every deactivated card',
          modal._surfBlock.children.some(x => x !== note && x.style.opacity === '0.55'));
    check('[3] the note itself stays legible', note && note.style.opacity !== '0.55');
    check('[3] deny-writes (no master — NOT OBDA-READ machinery) is never greyed or marked',
          modal._denyWrites.style.opacity !== '0.55'
          && !modal._auth.querySelector('summary h3').children.length);
    const modal2 = freshModal();
    await run(modal2, { ...ALL_OFF, obda_enabled: true });
    check('[3] OBDA-READ on → no note, no greying',
          !modal2._surfBlock.children.find(x => x.id === 'master-off-note-obda-surface')
          && modal2._surfBlock.children.every(x => x.style.opacity !== '0.55'));
    // recovery: off → on clears the greying on the SAME modal
    const modal3 = freshModal();
    await run(modal3, ALL_OFF);
    await run(modal3, { ...ALL_OFF, obda_enabled: true });
    check('[3] master flipped on: surface greying cleared',
          modal3._surfBlock.children.every(x => x.style.opacity === ''));
}

/* [4] null masters (unknown) render NOTHING — claiming "off" on absent data
       is the same falsehood inverted */
{
    const modal = freshModal();
    await run(modal, null);
    check('[4] unknown masters mark nothing',
          Object.values(modal._cards).every(c => !chipOf(c) && !bannerOf(c)));
}

/* [5] idempotent + recoverable: two passes never stack; a master flipped ON
       between opens removes the marks AND the greying */
{
    const modal = freshModal();
    await run(modal, ALL_OFF);
    await run(modal, ALL_OFF);
    const body = modal._cards.grounding.querySelector('summary + div, summary ~ div');
    check('[5] double apply: exactly one banner',
          body.children.filter(x => x.id?.startsWith('master-off-banner')).length === 1);
    check('[5] double apply: exactly one chip',
          modal._cards.grounding.querySelector('summary h3').children.length === 1);
    await run(modal, ALL_ON);
    check('[5] master flipped on: chip and banner removed',
          !chipOf(modal._cards.grounding) && !bannerOf(modal._cards.grounding));
    check('[5] master flipped on: greying cleared',
          body.children.every(x => x.style.opacity === ''));
}

/* [6] editable, not locked: the shipped function never disables a control */
check('[6] deactivated ≠ disabled — no control is ever locked',
      !fnSrc.includes('pointer-events') && !fnSrc.includes('.disabled = true'));

/* [7] the stale admin path is dead */
check('[7] the pre-Trust-panel path is gone',
      !fnSrc.includes('Operations → Agent Evaluation'),
      'the Agent Evaluation & Grounding card moved to App Config → Trust');

/* [8] the backend serves the two masters the new markings read */
{
    const i = rest.indexOf('def _deployment_masters()');
    const body = rest.slice(i, rest.indexOf('\n@', i));
    check('[8] _deployment_masters serves obda_enabled', body.includes('"obda_enabled"'));
    check('[8] _deployment_masters serves recommender_enabled',
          body.includes('"recommender_enabled"'));
}

console.log(fail
    ? `\nFAIL — ${fail} of ${pass + fail} assertions failed.`
    : `\nALL PASS — ${pass} assertions (masters and profile controls aligned).`);
process.exit(fail ? 1 : 0);
