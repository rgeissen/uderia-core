#!/usr/bin/env node
/**
 * Composer multi-line gate.
 *
 * A pasted MULTI-LINE message must reach the agent whole. The @TAG strip in
 * `handleChatSubmit` matched `/^@([\w-]+)\s+(.+)/` and `.` stops at the first
 * newline — so `cleanedMessage = tagMatch[2]` was the FIRST LINE ONLY, and every
 * later line was silently dropped. It fires whenever a tag is present: typed by
 * the user, or prepended from the active profile badge (`activeTagPrefix`), which
 * is the common case — so pasting a long prompt under a profile badge sent one line.
 *
 * The assertion is the PROPERTY (the shipped regex preserves every line), driven
 * through the regex literal extracted from the source — never a grep for one
 * spelling of the fix.
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };

const src = readFileSync('static/js/eventHandlers.js', 'utf8');

// The one site that strips an @TAG and keeps the remainder as the message body.
const m = src.match(/const tagMatch = message\.match\((\/\^@.*?\/)\);/);
check('the @TAG strip site is present in handleChatSubmit', !!m);

if (m) {
    const re = new RegExp(m[1].slice(1, -1));
    const body = 'You are collaborating with a data architect.\n\n- line two\n- line three';

    const tagged = re.exec('@ANDPA-DESIGN ' + body);
    check('a tagged message matches', !!tagged);
    check('the tag is captured', tagged && tagged[1] === 'ANDPA-DESIGN');
    check('EVERY line of a multi-line body survives the strip', tagged && tagged[2] === body);

    // A newline directly after the tag (badge prefix + a paste starting on line 1).
    const nl = re.exec('@ANDPA-DESIGN\n' + body);
    check('a newline separator still yields the whole body', nl && nl[2] === body);

    // Single-line and no-tag behaviour unchanged.
    const one = re.exec('@OPTIM how many databases?');
    check('single-line body unchanged', one && one[2] === 'how many databases?');
    check('an untagged multi-line message does not match (no strip)', !re.exec(body));
}

console.log(fail === 0
    ? `\nALL PASS — ${pass} assertions (a pasted multi-line message is never truncated).`
    : `\n${pass} passed, ${fail} FAILED`);
process.exit(fail ? 1 : 0);
