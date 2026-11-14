#!/usr/bin/env node
/**
 * A profile tag may contain a hyphen — every parser must accept one.
 *
 * `\w` is [A-Za-z0-9_] and EXCLUDES "-". Eleven parsers used it, so every hyphenated
 * tag silently failed. The visible symptom: the composer re-validates the override on
 * each keystroke (main.js), concluded the tag had been deleted, and removed the chip as
 * soon as the user typed. Worse and invisible: the send path strips the tag from the
 * message, so on a miss the literal "@ANDP-DESIGN " stayed in the text — which pollutes
 * the RAG embedding and duplicates the badge (CLAUDE.md states this explicitly).
 *
 * It went unnoticed because every earlier tag (OPTIM, IDEAT, FOCUS, UDERIA) is pure
 * word characters. The ANDP pack introduced 15 hyphenated tags.
 */
import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
let passed = 0; const failures = [];
const check = (n, c, d = '') => c ? (passed++, console.log(`  ok  ${n}`))
                                  : (failures.push(n), console.log(`  FAIL  ${n}${d ? ' — ' + d : ''}`));

// ── §A. No parser may be hyphen-blind ───────────────────────────────────────
console.log('\n[A] no hyphen-blind @TAG parser survives');
const scan = [];
for (const dir of ['static/js', 'src/trusted_data_agent']) {
    (function walk(d) {
        for (const e of fs.readdirSync(path.join(ROOT, d), { withFileTypes: true })) {
            const rel = path.join(d, e.name);
            if (e.isDirectory()) { if (e.name !== '__pycache__') walk(rel); }
            else if (/\.(js|py)$/.test(e.name)) scan.push(rel);
        }
    })(dir);
}
const blind = [];
for (const rel of scan) {
    const txt = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    txt.split('\n').forEach((line, i) => {
        // an @TAG capture that uses \w without allowing "-"
        if (/@\\?\(?\\w\+?/.test(line) && !/\[\\w-\]/.test(line)) blind.push(`${rel}:${i + 1}`);
    });
}
check('every @TAG parser accepts a hyphen', blind.length === 0, blind.slice(0, 6).join(', '));
check('the scan actually looked at the known parsers',
      scan.includes('static/js/main.js') && scan.includes('static/js/eventHandlers.js'));

// ── §B. The pattern behaves on real tag shapes ──────────────────────────────
console.log('\n[B] behaviour on real tags');
const RE_PREFIX = /^@([\w-]+)\s/;
const RE_SPLIT  = /^@([\w-]+)\s+(.+)/;
for (const tag of ['OPTIM', 'ANDP', 'ANDP-DOMAIN', 'ANDPA-DESIGN', 'ANDP-OBSERVABILITY', 'A_B-C1']) {
    const m = `@${tag} design something`.match(RE_PREFIX);
    check(`@${tag} is recognised whole`, !!m && m[1] === tag, m ? m[1] : 'no match');
}
const split = '@ANDPA-DESIGN Produce the design brief.'.match(RE_SPLIT);
check('the tag is stripped, leaving only the question',
      !!split && split[1] === 'ANDPA-DESIGN' && split[2] === 'Produce the design brief.',
      split ? JSON.stringify(split.slice(1)) : 'no match');
check('a bare @ is not a tag', !'@ hello'.match(RE_PREFIX));
check('a tag without trailing space is not yet complete', !'@ANDP-DOMAIN'.match(RE_PREFIX));

// ── §C. The exact regression: the chip survives typing ──────────────────────
console.log('\n[C] the reported regression');
const mainJs = fs.readFileSync(path.join(ROOT, 'static/js/main.js'), 'utf8');
const revalidate = /const fullMessage = activeTagPrefix \+ inputValue;[\s\S]{0,200}?match\(([^)]+)\)/.exec(mainJs);
check('the keystroke re-validation exists and is hyphen-tolerant',
      !!revalidate && revalidate[1].includes('[\\w-]'), revalidate ? revalidate[1] : 'not found');
// simulate it: prefix + typed text must still match, or the chip is removed
const sim = (tag, typed) => (`@${tag} ` + typed).match(/^@([\w-]+)\s/);
check('typing under an @ANDPA-DESIGN override keeps the chip', !!sim('ANDPA-DESIGN', 'Design a data product'));
check('typing under an @OPTIM override keeps the chip (no regression)', !!sim('OPTIM', 'How many databases?'));

console.log('\n' + '='.repeat(62));
console.log(`RESULT: ${failures.length ? 'FAILURES: ' + JSON.stringify(failures) : 'ALL PASS'}  (${passed} checks)`);
console.log('='.repeat(62));
process.exit(failures.length ? 1 : 0);
