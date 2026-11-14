/**
 * The per-LLM-configuration judge-panel opt-out — editor contract.
 *
 * A model can be excellent at answering and useless as a judge. Worse, a member that
 * never responds is not merely absent: every judge waits out its per-call timeout, so
 * one silent model sets the latency of the whole evaluation layer (production shipped
 * exactly that — three 20s timeouts on one turn from a single member). Excluding it is
 * the operator's call, so this is an explicit opt-out, never an auto-eviction.
 *
 * Pinned here because the failure modes are silent by nature:
 *   - a default that stops meaning "participates" retro-actively disables judging for
 *     every configuration that predates the option;
 *   - a save that only writes the key when opting OUT leaves a stored `false` in place,
 *     so the toggle appears to work and the model never comes back;
 *   - a state line that disagrees with the switch beside it (the user reads "On" and
 *     saves "Off").
 *
 * Executes the SHIPPED markup and the SHIPPED paint() — no browser, no copy of either.
 *
 * Run: node test/test_llm_judge_panel_optout.mjs
 */
import fs from 'fs';

let pass = 0;
const failures = [];
function check(name, got, want) {
    if (got === want) { pass++; return; }
    failures.push(`${name}\n      got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}

const SRC = fs.readFileSync(new URL('../static/js/handlers/configurationHandler.js', import.meta.url), 'utf8');
const KEY = 'judge_panel_enabled';

// ── 1 · the default is participation, expressed in the markup ───────────────
// Absent key ⇒ checked. Written as `=== false ? '' : 'checked'` precisely so that a
// config saved before the option existed still joins the panel.
{
    const m = SRC.match(/\$\{config\?\.judge_panel_enabled === false \? '' : 'checked'\}/);
    check('the checkbox defaults to CHECKED unless the config explicitly opted out', !!m, true);
    // The inverse spelling (`=== true ? 'checked' : ''`) would silently make the option
    // opt-IN and disable judging everywhere it is absent.
    check('…and never the opt-in spelling, which would disable judges for existing configs',
        /judge_panel_enabled === true \? 'checked'/.test(SRC), false);
}

// ── 2 · the save writes the key on EVERY save, not only when opting out ─────
{
    // Anchor inside the LLM modal — this file has three `configData` literals (MCP
    // servers and profiles own the others), and the first one is NOT this one.
    const fn = SRC.indexOf('export async function showLLMConfigurationModal');
    check('the LLM configuration modal is where we think it is', fn > 0, true);
    const i = SRC.indexOf('const configData = {', fn);
    const block = SRC.slice(i, SRC.indexOf('};', i));
    check('…and its save payload was located', i > fn, true);
    check('the save payload always carries the key',
        new RegExp(`${KEY}:\\s*judgeToggle \\? !!judgeToggle\\.checked : true`).test(block), true);
    // A conditional spread (`...(off && {key:false})`) is the shape that breaks
    // re-enabling: the key vanishes on the way back and the stored false survives.
    check('…unconditionally — never only in the opt-out branch',
        new RegExp(`\\.\\.\\.\\(.*${KEY}`).test(block), false);
}

// ── 3 · the card badge marks the EXCEPTION only ─────────────────────────────
{
    check('an opted-out config is badged on its card',
        /config\.judge_panel_enabled === false\)\s*\{[\s\S]{0,400}Not a judge/.test(SRC), true);
    // Participation is the norm; badging it would make every card noisier for no signal.
    check('…and a participating one carries no badge',
        /judge_panel_enabled !== false\)\s*\{[\s\S]{0,200}judge/i.test(SRC), false);
}

// ── 4 · the control renders one consistent state (the real paint()) ─────────
{
    const pStart = SRC.indexOf("        const box = modal.querySelector('#llm-modal-judge-panel');");
    const pEnd = SRC.indexOf('        paint();', pStart);
    check('the paint routine is extractable (if this fails, the gate below proves nothing)',
        pStart > 0 && pEnd > pStart, true);
    const body = SRC.slice(pStart, pEnd);

    // A minimal element stub: enough for textContent / style / classList, and it RECORDS
    // what was written so the assertions read the rendered result rather than the source.
    const el = () => ({
        textContent: '', style: {}, _classes: new Set(['hidden']),
        classList: {
            toggle(c, on) { if (on) this._set.add(c); else this._set.delete(c); },
            contains(c) { return this._set.has(c); },
        },
        addEventListener() {},
    });
    const mk = () => {
        const nodes = {};
        for (const id of ['llm-modal-judge-panel', 'llm-judge-card', 'llm-judge-icon',
                          'llm-judge-state', 'llm-judge-switch', 'llm-judge-knob', 'llm-judge-hint']) {
            const n = el();
            n.classList._set = n._classes;
            nodes[id] = n;
        }
        return nodes;
    };

    const render = (checked) => {
        const nodes = mk();
        nodes['llm-modal-judge-panel'].checked = checked;
        const modal = { querySelector: (sel) => nodes[sel.replace('#', '')] || null };
        // eslint-disable-next-line no-new-func
        new Function('modal', `${body}\n        paint();`)(modal);
        return nodes;
    };

    const on = render(true);
    const off = render(false);

    // SCOPE. One panel serves BOTH judge layers — it scores answers after the turn and
    // is what a gate escalates to in flight, where a ruling can withhold the answer.
    // Copy that mentions only scoring understates what switching this off changes.
    check('ON names BOTH layers, not just post-hoc scoring',
        /in flight/i.test(on['llm-judge-state'].textContent)
        && /after the turn/i.test(on['llm-judge-state'].textContent), true);
    check('OFF says it does not judge at all',
        /never judges/i.test(off['llm-judge-state'].textContent), true);
    check('the two states never read the same', on['llm-judge-state'].textContent
        !== off['llm-judge-state'].textContent, true);
    check('the opt-out rationale names the in-flight consequence FIRST — it is the one '
        + 'that changes what a user receives',
        /gate decides whether to withhold an answer/i.test(off['llm-judge-hint'].textContent), true);

    // Progressive disclosure: the rationale is shown only while it is actionable.
    check('the rationale is hidden while the model IS a judge',
        on['llm-judge-hint'].classList.contains('hidden'), true);
    check('…and shown, with real text, once it is opted out',
        off['llm-judge-hint'].classList.contains('hidden') === false
        && off['llm-judge-hint'].textContent.length > 40, true);
    check('…and the rationale reassures that ANSWERING is unaffected',
        /how this model answers is unaffected/i.test(off['llm-judge-hint'].textContent), true);

    // A card that resizes as you toggle is needless motion in a dialog. Both state lines
    // are kept to one line's worth of text, so the row height is stable across the toggle.
    const LINE_BUDGET = 44;   // chars that fit the ~240px description column at 12px
    check('both state lines fit one line, so the card does not jump on toggle',
        on['llm-judge-state'].textContent.length <= LINE_BUDGET
        && off['llm-judge-state'].textContent.length <= LINE_BUDGET, true);
}

// ── 5 · the switch is the shared component, and it is CSS-driven ────────────
// The first version hand-rolled an inline-styled switch painted by JavaScript. Two
// problems: the app already had this visual language (the tri-state toggle), and a
// control painted by script can drift from the checkbox that actually gets saved.
{
    const CSS = fs.readFileSync(new URL('../static/css/main.css', import.meta.url), 'utf8');

    check('the card uses the shared switch component, not a bespoke one',
        /class="sr-only u-switch-input"[\s\S]{0,200}class="u-switch"><span class="u-switch-thumb">/.test(SRC), true);
    check('the component exists in the stylesheet', /^\.u-switch \{/m.test(CSS), true);
    check('…and its state is driven by the checkbox, in CSS',
        /\.u-switch-input:checked ~ \.u-switch \{/.test(CSS)
        && /\.u-switch-input:checked ~ \.u-switch \.u-switch-thumb \{/.test(CSS), true);
    check('…so no script paints the switch (that is how a control drifts from its value)',
        /sw\.style|knob\.style/.test(SRC), false);
    check('…and it is styled for the light theme too',
        /\[data-theme="light"\] \.u-switch \{/.test(CSS), true);

    // Keyboard: the input is visually hidden but still focusable, so the ring must be
    // drawn on the track it controls or the switch is invisible to keyboard users.
    check('the real checkbox stays in the tab order (sr-only, never display:none)',
        /id="llm-modal-judge-panel" class="sr-only u-switch-input"/.test(SRC), true);
    check('…and the focus ring is drawn on the track',
        /\.u-switch-input:focus-visible ~ \.u-switch \{[\s\S]{0,120}box-shadow/.test(CSS), true);
}

// ── 6 · the sr-only root cause ──────────────────────────────────────────────
// `.sr-only` (0,1,0) lost to `input[type="checkbox"]` (0,1,1), which restored
// width/height/position — so the class was a NO-OP for every input in the app and the
// hidden checkbox rendered beside the control meant to replace it (on this card AND on
// the agent-pack tri-state toggle). `clip` could not save it either: clipping applies
// only to absolutely-positioned boxes, and `position` was one of the overridden
// properties. The utility must therefore outrank any element/attribute rule.
{
    const CSS = fs.readFileSync(new URL('../static/css/main.css', import.meta.url), 'utf8');
    const i = CSS.indexOf('.sr-only {');
    check('.sr-only is defined', i > 0, true);
    const rule = CSS.slice(i, CSS.indexOf('}', i));
    for (const prop of ['position', 'width', 'height', 'overflow', 'clip']) {
        check(`.sr-only wins on ${prop} (else a type selector silently un-hides it)`,
            new RegExp(`${prop}:[^;]*!important`).test(rule), true);
    }
    check('…and neutralises the platform checkbox chrome inside the 1px box',
        /appearance:\s*none\s*!important/.test(rule), true);
}

if (failures.length) {
    console.log(`\nFAILED — ${pass} passed, ${failures.length} failed`);
    for (const f of failures) console.log(`  ✗ ${f}`);
    process.exit(1);
}
console.log(`ALL PASS — ${pass} assertions across the judge-panel opt-out editor contract.`);
