/**
 * Session primers render as markdown — and NOTHING else changes.
 *
 * A primer is an authored .md file of standing rules that used to land in the
 * transcript as one wall of raw source. The risk in fixing that is collateral: the
 * same function renders every message in the chat log, and the same stylesheet is
 * read by every message body. So this gate spends most of its assertions proving
 * what did NOT change.
 *
 * Run: node test/test_primer_markdown.mjs
 */
import { readFileSync } from 'fs';

let PASS = 0, FAIL = 0; const FAILURES = [];
const check = (name, cond, detail = '') => {
  if (cond) PASS++; else { FAIL++; FAILURES.push(`${name}${detail ? ' — ' + detail : ''}`); }
};

const ui = readFileSync(new URL('../static/js/ui.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../static/css/polish.css', import.meta.url), 'utf8');

/* ── the render decision, extracted from the shipped source ──────────────── */
const m = ui.match(/if \(\((role === 'assistant'[^)]*)\) && typeof marked !== 'undefined' && !isAlreadyHtml\) \{/);
check('the markdown branch is present and still guarded by marked + isAlreadyHtml', !!m,
      'condition not found — the render seam moved');
const condSrc = m ? m[1] : 'false';
const decide = new Function('role', 'isSessionPrimer', 'marked', 'isAlreadyHtml',
  `return Boolean((${condSrc}) && typeof marked !== 'undefined' && !isAlreadyHtml);`);

const MD = {}, NO_MD = undefined;

/* what SHOULD now be markdown */
check('a user-role primer renders as markdown', decide('user', true, MD, false) === true);
check('an assistant-role primer still renders as markdown', decide('assistant', true, MD, false) === true);
check('an ordinary assistant message is unchanged', decide('assistant', false, MD, false) === true);

/* the no-regression core: everything else keeps its old path */
check('an ordinary user message still takes the RAW path',
      decide('user', false, MD, false) === false);
check('a user message is not markdown just because marked exists',
      decide('user', false, MD, true) === false);
check('content that is already HTML is never re-parsed, primer or not',
      decide('assistant', false, MD, true) === false &&
      decide('user', true, MD, true) === false &&
      decide('assistant', true, MD, true) === false);
check('with marked.js absent nothing is parsed, primer or not',
      decide('user', true, NO_MD, false) === false &&
      decide('assistant', false, NO_MD, false) === false &&
      decide('assistant', true, NO_MD, false) === false);

/* ── the class that scopes the styling ───────────────────────────────────── */
check('the primer class is added only under the primer flag',
      /if \(isSessionPrimer\) messageContent\.classList\.add\('message-content--primer'\);/.test(ui));
check('the base message-content class is still applied to every message',
      /messageContent\.className = 'message-content';/.test(ui));

/* ── the stylesheet cannot reach a non-primer message ────────────────────── */
const seg = css.slice(css.indexOf('Session primer'));
check('the primer stylesheet block exists', seg.length > 200);
const selectors = [...seg.matchAll(/\n(\.[^{\n]+)\{/g)].map(x => x[1].trim());
check('every new rule is scoped to the primer class', selectors.length > 0 &&
      selectors.every(sel => sel.split(',').every(part => part.includes('.message-content--primer'))),
      selectors.filter(sel => !sel.split(',').every(p => p.includes('.message-content--primer'))).join(' | '));
check('no new rule targets bare .message-content',
      !selectors.some(sel => /(^|,)\s*\.message-content\s*[>{ ]/.test(sel)));
check('braces balance', (seg.match(/\{/g) || []).length === (seg.match(/\}/g) || []).length);
check('list markers are restored (Tailwind preflight strips them)',
      /list-style:\s*revert/.test(seg));
check('colours come from theme tokens, not literals',
      !/#[0-9a-fA-F]{6}(?![^;]*var\()/.test(seg.replace(/var\([^)]*\)/g, '')));

console.log(`\n${PASS} passed, ${FAIL} failed`);
FAILURES.forEach(f => console.log('  FAIL:', f));
process.exit(FAIL ? 1 : 0);
