/**
 * The admin quality-band sliders: a resource track is LOGARITHMIC (a one-call Ideate turn
 * and a multi-expert Coordinate turn are decades apart), value ↔ position round-trips,
 * a log value snaps to two significant figures, every preset lies inside its track, and
 * the token track reaches the turns this platform actually runs. Executes the shipped
 * helpers (extracted verbatim from the IIFE) — no browser.
 */
import fs from 'node:fs';
const src = fs.readFileSync(new URL('../static/js/handlers/thresholdSliders.js', import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d === '' ? '' : JSON.stringify(d).slice(0, 200)); } };
const slice = (from, to) => src.slice(src.indexOf(from), src.indexOf(to, src.indexOf(from)));
const helpers = slice('    const clamp = (v, lo, hi)', '    function pct(spec, v)');
const ranges = slice('    const RANGES = [', '    ];') + '];';
const presets = slice('    const PRESETS = {', '    };') + '};';
const { RANGES, PRESETS, snap, toRatio, fromRatio } = new Function(ranges + presets + helpers + 'return { RANGES, PRESETS, snap, toRatio, fromRatio };')();
const spec = (k) => RANGES.find(r => r.key === k);

check('the three resource tracks are logarithmic', ['max_tokens', 'max_latency_ms', 'max_cost_usd'].every(k => spec(k).scale === 'log'));
check('a log track has a positive floor (log(0) is not a position)', RANGES.filter(r => r.scale === 'log').every(r => r.min > 0));
check('the token track reaches a multi-expert turn (≥ 1M) and starts below a one-call turn (≤ 2k)', spec('max_tokens').max >= 1e6 && spec('max_tokens').min <= 2000);
check('the latency track reaches a coordinator turn (≥ 5 min)', spec('max_latency_ms').max >= 300000);
const tk = spec('max_tokens');
check('the ends of the track map to 0 and 1', toRatio(tk, tk.min) === 0 && Math.abs(toRatio(tk, tk.max) - 1) < 1e-12);
check('the geometric middle sits at the middle of the track (equal drag = equal ratio)', Math.abs(toRatio(tk, Math.sqrt(tk.min * tk.max)) - 0.5) < 1e-9);
check('30k→60k moves the handle as far as 300k→600k', Math.abs((toRatio(tk, 60000) - toRatio(tk, 30000)) - (toRatio(tk, 600000) - toRatio(tk, 300000))) < 1e-12);
check('value ↔ position round-trips', [1000, 31000, 150000, 1999999].every(v => Math.abs(fromRatio(tk, toRatio(tk, v)) - v) < 1e-6 * v));
check('a log value snaps to two significant figures (a reader can tell 150,000 from 160,000, not 150,500)', snap(tk, 152345) === 150000 && snap(tk, 31234) === 31000 && snap(tk, 999) === 1000 && snap(tk, 9e9) === tk.max);
const lin = spec('max_steps');
check('a linear track is unchanged (snap to its step, position linear)', snap(lin, 4.4) === 4 && toRatio(lin, lin.min) === 0 && Math.abs(toRatio(lin, (lin.min + lin.max) / 2) - 0.5) < 1e-12);
for (const [name, p] of Object.entries(PRESETS)) {
  check(`preset ${name}: every cut lies inside its track`, Object.entries(p).every(([k, [h, d]]) => { const s = spec(k); return s && h >= s.min && h <= s.max && d >= s.min && d <= s.max; }));
}
check('presets: tokens are realistic for this platform (strict healthy ≥ 20k, lenient danger ≥ 300k) and ordered', PRESETS.strict.max_tokens[0] >= 20000 && PRESETS.lenient.max_tokens[1] >= 300000
  && PRESETS.strict.max_tokens[1] <= PRESETS.balanced.max_tokens[1] && PRESETS.balanced.max_tokens[1] <= PRESETS.lenient.max_tokens[1]);
check('the pointer and the activate-defaults read the track through fromRatio (no linear span arithmetic left)', /return snap\(spec, fromRatio\(spec, ratio\)\)/.test(src) && !/spec\.min \+ span \*/.test(src));
console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
