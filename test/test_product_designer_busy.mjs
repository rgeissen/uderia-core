#!/usr/bin/env node
/**
 * The data-product designer must say what it is doing — and must never
 * imply an act it did not perform.
 *
 * WHY THIS GATE EXISTS
 * A LIVE validation compiles a probe plan and executes a dozen queries at the
 * warehouse. That takes seconds. Reported from a real walkthrough: the button
 * did not change, so the operator could not tell a running probe from a click
 * that never registered — and the honest reading of a frozen button is "it is
 * broken", which invites a second click and a second round of probes.
 *
 * The fix is ONE wrapper shared by every async action in the dialog, never a
 * per-button copy (four hand-rolled copies is how three of them end up without
 * one). This gate EXECUTES that wrapper — a source grep passes a mutation that
 * deletes the restore, which is exactly the failure that strands a button.
 *
 * Run: node test/test_product_designer_busy.mjs      (no server, no network)
 */
import { readFileSync } from 'fs';

const SRC = 'static/js/handlers/knowledgeGraphPanelHandler.js';
const js = readFileSync(SRC, 'utf8');

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
    if (cond) { pass++; }
    else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`); }
};

/* ---------------------------------------------------------------- extract */
function sliceFn(marker) {
    const i = js.indexOf(marker);
    if (i < 0) return null;
    let depth = 0, j = js.indexOf('{', i);
    for (let k = j; k < js.length; k++) {
        if (js[k] === '{') depth++;
        else if (js[k] === '}') { depth--; if (depth === 0) return js.slice(i, k + 1); }
    }
    return null;
}

const src = sliceFn('async function withBusy(btn, label, fn)');
check('the shared busy wrapper exists', !!src,
      'withBusy is the single seam every async action routes through');
if (!src) { console.log(`\n${pass} passed, ${fail} failed`); process.exit(1); }

/* ------------------------------------------------------------- run it for real */
const cardAttrs = new Map();
const card = {
    setAttribute: (k, v) => cardAttrs.set(k, v),
    removeAttribute: (k) => cardAttrs.delete(k),
};
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const withBusy = new Function('card', 'esc', `${src}; return withBusy;`)(card, esc);

const mkBtn = (label = 'Validate') => ({
    innerHTML: label, disabled: false, isConnected: true, dataset: {},
});

/* [1] it names the work and marks the button busy WHILE the action runs */
{
    const b = mkBtn();
    let seen = null;
    await withBusy(b, 'Probing MyTeradata_dbc…', async () => {
        seen = { html: b.innerHTML, disabled: b.disabled, busy: b.dataset.dpBusy,
                 cardBusy: cardAttrs.get('aria-busy') };
    });
    check('the button is disabled while the action runs', seen.disabled === true);
    check('the label NAMES the work in progress', /Probing MyTeradata_dbc/.test(seen.html),
          `got ${JSON.stringify(seen.html)} — "working" is not enough; a live pass and a `
          + 'structural pass are different acts and the operator started one of them');
    check('a motion affordance is rendered', /kg-dp-spin/.test(seen.html),
          'a static label alone still reads as frozen over several seconds');
    check('the card is marked aria-busy while the action runs', seen.cardBusy === 'true');
}

/* [2] it restores afterwards — the mutation that strands a button forever */
{
    const b = mkBtn();
    await withBusy(b, 'Validating…', async () => {});
    check('the button is re-enabled after a successful action', b.disabled === false);
    check('the original label is restored', b.innerHTML === 'Validate');
    check('the busy marker is cleared', b.dataset.dpBusy === undefined);
    check('the card aria-busy is cleared', cardAttrs.get('aria-busy') === undefined);
}

/* [3] a THROWING action must still restore — this is the real-world case
       (network down mid-probe) and the one a happy-path test never reaches */
{
    const b = mkBtn();
    let threw = false;
    try { await withBusy(b, 'Probing…', async () => { throw new Error('network down'); }); }
    catch { threw = true; }
    check('a throwing action does not strand a busy button', b.disabled === false && !b.dataset.dpBusy,
          'the restore must live in a finally, not after the await');
    check('a throwing action clears the card busy state', cardAttrs.get('aria-busy') === undefined);
    check('the error still propagates (never swallowed)', threw === true);
}

/* [4] re-entrancy: a second click during a live probe must be a NO-OP, not a
       second round of warehouse queries */
{
    const b = mkBtn();
    let runs = 0;
    let release; const held = new Promise(r => { release = r; });
    const first = withBusy(b, 'Probing…', async () => { runs++; await held; });
    await withBusy(b, 'Probing…', async () => { runs++; });   // the double click
    check('a double click does not fire the action twice', runs === 1,
          `ran ${runs}× — a second live validation doubles the probe load`);
    release(); await first;
}

/* [5] a node the re-render already replaced must not be touched */
{
    const b = mkBtn();
    await withBusy(b, 'Saving…', async () => { b.isConnected = false; });
    check('a replaced node is left alone', b.innerHTML.includes('kg-dp-spin'),
          'restoring a detached node is harmless but pointless; the guard also '
          + 'documents that the happy path re-renders');
}

/* ------------------------------------------------- every action routes through it */
for (const [cls, label] of [['kg-dp-validate', 'validate'], ['kg-dp-sign', 'sign'],
                            ['kg-dp-edit', 'edit'], ['kg-dp-save', 'save']]) {
    const at = js.indexOf(cls === 'kg-dp-save' ? 'saveBtn.onclick' : `'.${cls}'`);
    const win = js.slice(at, at + 700);
    check(`the ${label} action routes through withBusy`, /withBusy\(/.test(win),
          'a per-button copy is how the other three end up without one');
}

/* the affordance must be theme-aware and respect reduced motion */
check('the spinner takes its colour from the button (theme-aware)',
      /\.kg-dp-spin\{[^}]*currentColor/.test(js),
      'a hardcoded hex breaks in one of the two themes');
check('reduced motion is respected',
      /prefers-reduced-motion[^}]*\}\s*\.kg-dp-spin\{animation:none|prefers-reduced-motion:reduce\)\{\.kg-dp-spin\{animation:none/.test(js.replace(/\s+/g, ' ').replace(/ \{/g, '{')),
      'an infinite spin with no reduced-motion fallback');
check('Close stays reachable while an action runs',
      /aria-busy="true"\]\s*button:not\(\[data-dp-busy\]\):not\(\.kg-dp-close\)/.test(js),
      'a slow probe must always be abandonable');

/* ================================================================
   [B] The designer must never imply an act it did not perform.

   Reported from a real walkthrough: edit a signed product → 🟠 changed →
   revert → Validate → the row reads 🟢 trusted. The operator concluded
   "the revalidation automatically signed it". It did not — validation has no
   sign call on any path, and the attestation table was unchanged. But the
   content had returned to the signed bytes, so the EXISTING signature matched
   again, and NOTHING on screen said so.

   A trust surface implying it signed on your behalf is the worst misreading it
   can invite, so the wording rule is extracted PURE and executed here.
   ================================================================ */
const nsrc = sliceFn('function _dpValidateNotice(report, trust, productName, liveNote)');
check('the validate wording is an extractable rule', !!nsrc,
      'inline in the handler it can only be grepped, and a grep passes the mutation');

if (nsrc) {
    const notice = new Function(`${nsrc}; return _dpValidateNotice;`)();
    const SIGNED_AT = 1786889037.274924;

    /* [B1] the re-affirmed case — the one that read as an auto-sign */
    const reaff = notice({ signable: true, mode: 'live' },
                         { state: 'trusted', signed_at: SIGNED_AT }, 'support_load', null);
    check('a re-affirmed product NAMES the act that did not happen',
          /nothing was signed/i.test(reaff.text),
          `got: ${reaff.text}`);
    check('a re-affirmed product explains WHY it is green',
          /byte-identical|already signed/i.test(reaff.text),
          'without the reason, "nothing was signed" reads as a contradiction of the green chip');
    check('a re-affirmed product names WHICH signature applies',
          reaff.text.includes(new Date(SIGNED_AT * 1000).toLocaleString()),
          'the timestamp is what lets an operator verify the claim against the record');
    check('a re-affirmed product does not claim to be merely "signable"',
          !/is signable/.test(reaff.text),
          'that is the pre-signature wording and reads as "no signature exists"');

    /* [B2] the ordinary case is untouched */
    const fresh = notice({ signable: true, mode: 'live' },
                         { state: 'unsigned', signed_at: null }, 'support_load', null);
    check('a not-yet-signed product still reads as signable',
          /support_load is signable/.test(fresh.text));
    check('a not-yet-signed product never claims a signature exists',
          !/already signed|nothing was signed/i.test(fresh.text),
          'the honest reading for an unsigned product is that it is ready to be signed');

    /* [B3] a changed product must not be re-affirmed by wording */
    const changed = notice({ signable: true, mode: 'live' },
                           { state: 'changed', signed_at: SIGNED_AT }, 'support_load', null);
    check('a CHANGED product is not described as re-affirmed',
          !/nothing was signed/i.test(changed.text),
          'changed means the content does NOT match the signature — saying otherwise inverts it');

    /* [B4] a blocked gate still leads with the blockers */
    const blocked = notice({ signable: false, blockers: ['V7: 3 duplicate group(s) found'] },
                           { state: 'unsigned' }, 'support_load', null);
    check('a blocked gate reports its blockers', blocked.kind === 'err'
          && /3 duplicate group\(s\)/.test(blocked.text));

    /* [B5] mode honesty survives — structural must never claim live verification */
    const struct = notice({ signable: true, mode: 'structural' },
                          { state: 'trusted', signed_at: SIGNED_AT }, 'support_load', null);
    check('a structural pass never claims empirical verification',
          !/empirically verified/.test(struct.text) && /declarations trusted/.test(struct.text),
          'live vs structural is attested content; the words must not blur it either');

    /* [B6] a missing timestamp degrades, never fabricates */
    const noTs = notice({ signable: true, mode: 'live' },
                        { state: 'trusted' }, 'support_load', null);
    check('an absent signing time is omitted, not invented',
          /nothing was signed/i.test(noTs.text) && !/Invalid Date|NaN|undefined/.test(noTs.text),
          `got: ${noTs.text}`);
}

/* [B7] the disabled Sign button must say WHY it is disabled — "the offered act
   is the act that is owed"; a greyed control with no reason reads as broken */
{
    const at = js.indexOf('class="kg-dp-sign"');
    const btn = js.slice(at, at + 1200);
    check('the Sign button carries a reason', /title="/.test(btn));
    check('an already-signed product says there is nothing to sign',
          /nothing to sign/i.test(btn));
    check('a changed product is told to re-prove, not to re-sign',
          /Re-validate to re-prove/i.test(btn),
          'there is no path to green that skips validation, and the control should say so');
    check('an unvalidated product is told to validate first',
          /Validate first|validate first/.test(btn));
}

/* [B9] a product can be DELETED from the designer — the UI gap an operator hit:
   the REST endpoint existed and the dialog offered no way to reach it */
{
    check('the Delete action exists', /kg-dp-delete/.test(js));
    const at = js.indexOf("'.kg-dp-delete'");
    const win = js.slice(at, at + 1600);
    check('delete routes through the shared busy wrapper', /withBusy\(/.test(win),
          'a warehouse-state mutation with no busy affordance is the Validate bug again');
    check('delete requires a second, armed click', /dataset\.armed/.test(win)
          && /Really delete\?/.test(win),
          'destructive, and the platform bans native confirm() dialogs');
    check('delete calls the REST endpoint with the kg scope',
          /method: 'DELETE'/.test(win) && /kg_id=/.test(win));
    check('the outcome names the attestation consequence',
          /attestation is revoked/.test(win),
          'deleting a signed contract revokes its signature — saying so is the '
          + 'difference between an act and a surprise');
    const btn = js.slice(js.indexOf('class="kg-dp-delete"'), js.indexOf('class="kg-dp-delete"') + 700);
    check('the button states the consequence before the click', /title="/.test(btn)
          && /seal is unaffected/.test(btn));
}

/* [B8] the server must hand the client the state it left behind — the client
   cannot otherwise tell a fresh signature from a re-affirmed one */
{
    const py = readFileSync('src/trusted_data_agent/api/rest_routes.py', 'utf8');
    const at = py.indexOf('async def obda_product_validate');
    // slice on the REAL boundary (the next route decorator), never a character
    // count — a distance bound is not an assertion: it silently stops covering
    // the thing it names as soon as the function grows past it.
    const end = py.indexOf('@rest_api_bp.route', at);
    const seg = py.slice(at, end > at ? end : py.length);
    check('the endpoint body was located, not a fixed-size guess', end > at && seg.length > 500);
    check('the validate endpoint returns the resulting trust state',
          /_resp\["trust"\]\s*=/.test(seg));
    check('the validate endpoint still never signs',
          !/sign_product|sign_off|record_attestation/.test(seg),
          'validation must remain side-effect-free on the attestation table');
    const prod = readFileSync('src/trusted_data_agent/core/trust/products.py', 'utf8');
    check('the product badge carries when it was signed',
          /"signed_at": signed_at/.test(prod));
}

console.log(fail
    ? `\nFAIL — ${fail} of ${pass + fail} assertions failed.`
    : `\nALL PASS — ${pass} assertions (the designer says what it is doing).`);
process.exit(fail ? 1 : 0);
