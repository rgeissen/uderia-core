#!/usr/bin/env node
/**
 * The pricing notice at selection time (OC-F12): the configuration dialog tells the person
 * choosing a model when it would be billed at the fallback rate. Pure — `pricingNotice` is
 * extracted from configurationHandler.js and executed (no DOM, no server).
 */
import { readFileSync } from 'node:fs';
let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) { pass++; console.log(`  ✓ ${label}`); } else { fail++; console.log(`  ✗ ${label}`); } };
const src = readFileSync(new URL('../static/js/handlers/configurationHandler.js', import.meta.url), 'utf8');
const m = src.match(/export function pricingNotice\(pricing, label\) \{[\s\S]*?\n\}/);
check('pricingNotice is defined', !!m);
const pricingNotice = new Function(`${m[0].replace('export ', '')}; return pricingNotice;`)();
const u = pricingNotice({ status: 'unpriced' }, 'Grok judge');
check('an unpriced model warns, naming it and the fallback', u && u.type === 'warning' && /Grok judge/.test(u.text) && /fallback rate/.test(u.text));
const i = pricingNotice({ status: 'filled', source: 'inferred', note: "Azure deployment 'tda-gpt-4o-mini' priced as gpt-4o-mini (inferred …)" }, 'Azure');
check('an inferred price is said to be inferred', i && i.type === 'info' && /inferred/.test(i.text));
check('a filled price names its source', /litellm/.test(pricingNotice({ status: 'filled', source: 'litellm' }, 'X').text));
check('a model that was already priced says nothing', pricingNotice({ status: 'priced' }, 'X') === null);
check('no pricing in the response says nothing', pricingNotice(undefined, 'X') === null);
check('create and update both show the notice', (src.match(/showPricingNotice\(/g) || []).length === 3);
console.log(fail ? `FAILED — ${pass + fail} assertions (${pass} passed, ${fail} failed)` : `ALL PASS — ${pass} assertions (the pricing notice at selection time)`);
process.exit(fail ? 1 : 0);
