// Gate: profile-editor skill-row version picker (best-practice step 4b UI).
//
// The contract: `skillsConfig.skills[].content_hash` pins a profile to one
// content-addressed skill version. The picker follows the anchor-grant
// pattern — state stamped SYNCHRONOUSLY on the row dataset from the STORED
// entry (truthful-save), mutated only by the control, collected by the save
// through ONE _withVersionPin helper at BOTH push sites. Absent pin = follow
// latest = no key = byte-identical round-trip.
//
// CI-pure: reads the shipped source, executes the pure helper, asserts the
// wiring as properties. No browser.
import { readFileSync } from 'fs';

const src = readFileSync('static/js/handlers/configurationHandler.js', 'utf8');
let passed = 0; const failed = [];
const check = (name, cond, detail = '') => {
  if (cond) passed++;
  else { failed.push(name); console.log(`  FAIL ${name}  ${detail}`); }
};

// ── extract + EXECUTE the pure save-side helper ──────────────────────────────
const m = src.match(/function _withVersionPin\(entry, row\) \{[\s\S]*?\n\}/);
check('U1 _withVersionPin exists', !!m);
const _withVersionPin = new Function(`${m ? m[0] : ''}; return _withVersionPin;`)();

const rowWith = (pin) => ({ dataset: pin === undefined ? {} : { contentHash: pin } });
let e = _withVersionPin({ id: 's' }, rowWith('a1b2c3d4e5f60718'));
check('U2 valid pin lands on the entry', e.content_hash === 'a1b2c3d4e5f60718');
e = _withVersionPin({ id: 's' }, rowWith(undefined));
check('U3 no pin -> NO content_hash key (byte-identical round-trip)',
      !('content_hash' in e));
e = _withVersionPin({ id: 's' }, rowWith('NOT-A-HASH'));
check('U4 malformed pin is dropped, never guessed', !('content_hash' in e));
e = _withVersionPin({ id: 's' }, rowWith('abc'));
check('U5 too-short pin is dropped (< 12 hex)', !('content_hash' in e));
e = _withVersionPin({ id: 's' }, null);
check('U6 helper tolerates a missing row', !('content_hash' in e));

// ── save wiring: BOTH push sites route through the helper ────────────────────
const collect = src.slice(src.indexOf('function _collectSkillsConfig'),
                          src.indexOf('function _withAnchorGrant'));
const pushSites = (collect.match(/skills\.push\(/g) || []).length;
const wrapped = (collect.match(/skills\.push\(_withVersionPin\(_withAnchorGrant\(/g) || []).length;
check('U7 every skills.push routes through _withVersionPin',
      pushSites >= 2 && wrapped === pushSites,
      `push=${pushSites} wrapped=${wrapped}`);

// ── render wiring ────────────────────────────────────────────────────────────
check('U8 stored pin stamped SYNCHRONOUSLY at render (truthful save)',
      /entry\?\.content_hash === 'string'[\s\S]{0,120}row\.dataset\.contentHash = entry\.content_hash/.test(src));
check('U9 picker mounts only where actionable (archived versions OR stored pin)',
      /\(skill\.archived_versions \|\| 0\) > 0 \|\| row\.dataset\.contentHash/.test(src));

// ── the control itself ───────────────────────────────────────────────────────
const ctl = src.slice(src.indexOf('function _mountVersionPinControl'),
                      src.indexOf('function _withVersionPin'));
check('U10 version list fetched LAZILY inside the control, never per-row',
      ctl.includes('/versions') &&
      (src.match(/\/versions`/g) || []).length === 1);
check('U11 unpin deletes the dataset key (follow-latest saves NO key)',
      ctl.includes('delete row.dataset.contentHash'));
check('U12 panel shares the skills list close/cleanup lifecycle',
      ctl.includes("setAttribute('data-skill-param-panel'"));
check('U13 follow-latest still offered when the version fetch fails',
      /Could not load the version list[\s\S]{0,60}Follow latest/.test(ctl) ||
      /Follow latest[\s\S]*Could not load the version list/.test(ctl));
check('U14 pinned state is visually distinct from follow-latest',
      ctl.includes('Version · pinned') && ctl.includes('Version · latest'));
check('U15 theme tokens, not hardcoded panel colors',
      ctl.includes('var(--card-bg)') && ctl.includes('var(--border-primary)'));

console.log(`\n${passed} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
