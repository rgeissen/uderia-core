#!/usr/bin/env node
/**
 * The header avatar is a flat disc — the gate.
 *
 * The user mark in the top bar was a 36px blue-tinted circle with a 2px ring: the same blue
 * (the Focus class colour) for every user, the heaviest element in the row, the account's
 * OAuth picture never shown. It is now ONE solid neutral disc, 30px, nothing drawn around it.
 * Interaction lives outside the disc as a ring offset: quiet on hover, the brand accent only
 * while the menu is open and on keyboard focus. A linked provider picture sits over the
 * initials and the initials come back when the image fails. Every colour is a token.
 *
 * Asserted here: the markup (image slot, ARIA state, chevron), the CSS (no border, no tint,
 * ring-offset states, tokens, light override), the picture helper + its fallback, the /me
 * field, and the pins. No server.
 *
 * Run: node test/test_header_avatar.mjs
 */
import fs from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };
const read = (p) => fs.readFileSync(p, 'utf8');
const block = (css, sel) => { const i = css.indexOf(sel); if (i < 0) return ''; return css.slice(i, css.indexOf('}', i) + 1); };

const T = read('templates/index.html');
const CSS = read('static/css/main.css');
const TH = read('static/css/themes.css');
const PY = read('src/trusted_data_agent/api/auth_routes.py');

// ---- 1. the trigger markup ----
const trig = (T.match(/<button id="user-menu-button"[^>]*>[\s\S]*?<\/button>/) || [''])[0];
check('the user menu trigger exists', !!trig);
check('…type=button', /type="button"/.test(trig));
check('…declares the popup it controls (aria-haspopup + aria-controls)', /aria-haspopup="menu"/.test(trig) && /aria-controls="user-dropdown-menu"/.test(trig));
check('…starts closed (aria-expanded="false")', /aria-expanded="false"/.test(trig));
check('…holds the initials', /id="user-avatar-initial"/.test(trig));
check('…and an image slot, hidden at rest, no referrer leak', /<img id="user-avatar-image"[^>]*\bhidden\b/.test(trig) && /referrerpolicy="no-referrer"/.test(trig));
check('…the image has an empty alt (the initials/name carry the text)', /<img id="user-avatar-image"[^>]*alt=""/.test(trig));
check('…a monochrome stroke chevron, aria-hidden', /<svg class="ind-user-chevron[^"]*"[^>]*stroke="currentColor"[^>]*aria-hidden="true"/.test(trig));
check('…no emoji in the trigger', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(trig));

// ---- 2. the disc: flat, 30px, nothing drawn around it ----
const av = block(CSS, '.ind-avatar {');
check('.ind-avatar is styled in main.css', !!av);
check('…30px via one size token, in px (the app root is 13px, so rem would land at 24px)', /--avatar-size:\s*30px/.test(av) && /width:\s*var\(--avatar-size\)/.test(av));
check('…a circle', /border-radius:\s*50%/.test(av));
check('…NO border (the ring moved outside the disc)', !/\bborder:/.test(av) && !/border-color/.test(av));
check('…a solid fill from a token, not a translucent tint', /background:\s*var\(--avatar-fill\)/.test(av) && !/rgba\([^)]*,\s*0\.\d+\)\s*;?\s*$/m.test(av.split('background')[1] || ''));
check('…ink from a token', /color:\s*var\(--avatar-ink\)/.test(av));
check('…the ring gap takes the header background token', /--avatar-ring-gap:\s*var\(--header-bg/.test(av));
check('…clips the picture to the disc', /overflow:\s*hidden/.test(av));
check('…the old blue --avatar-color is gone from the header avatar', !/\.ind-avatar[^{]*\{[^}]*--avatar-color/.test(CSS) && !/#user-avatar-initial[^{]*\{[^}]*--avatar-color/.test(CSS));

// ---- 3. the states live OUTSIDE the disc ----
const hover = block(CSS, '.ind-user-trigger:hover .ind-avatar {');
check('hover = a ring offset (box-shadow pair), no fill change', /box-shadow:\s*0 0 0 2px var\(--avatar-ring-gap\),\s*0 0 0 3\.5px var\(--avatar-ring\)/.test(hover) && !/background/.test(hover));
const open = block(CSS, '.ind-user-trigger[aria-expanded="true"] .ind-avatar {');
check('open = the same ring in the brand accent (the one place orange touches the mark)', /0 0 0 3\.5px var\(--teradata-orange/.test(open));
const focus = block(CSS, '.ind-user-trigger:focus-visible {');
check('keyboard focus has a visible outline in the accent', /outline:\s*2px solid var\(--teradata-orange/.test(focus) && /outline-offset/.test(focus));
check('the open state turns the chevron', /\.ind-user-trigger\[aria-expanded="true"\] \.ind-user-chevron \{\s*transform:\s*rotate\(180deg\)/.test(CSS));
check('reduced motion is respected', /prefers-reduced-motion: reduce\)\s*\{\s*\.ind-avatar, \.ind-user-chevron \{ transition: none; \}/.test(CSS));

// ---- 4. picture over initials, initials back on failure ----
check('the image fills the disc with object-fit: cover', /#user-avatar-image \{[^}]*object-fit:\s*cover/.test(CSS));
check('a picture hides the initials only through .has-picture', /\.ind-avatar\.has-picture #user-avatar-initial \{ visibility: hidden; \}/.test(CSS));
const helper = (T.match(/function setHeaderAvatarPicture\(url\) \{[\s\S]*?\n        \}/) || [''])[0];
check('one helper sets the picture', !!helper);
check('…accepts only http(s) URLs', /\^https\?:\\\/\\\//.test(helper));
check('…shows the image only once it has loaded (onload → has-picture)', /img\.onload = \(\) => \{ img\.hidden = false; avatar\.classList\.add\('has-picture'\)/.test(helper));
check('…falls back to the initials when the image fails (onerror → remove has-picture)', /img\.onerror = \(\) => \{[^}]*avatar\.classList\.remove\('has-picture'\)/.test(helper));
check('…sits as its own statement before updateAuthUI (never wedged between `async` and `function` — that aborted the whole block once)', /async function updateAuthUI\(\)/.test(T) && !/\basync\s*(\/\/|\n)/.test(T.slice(T.indexOf('function setHeaderAvatarPicture') - 400, T.indexOf('function setHeaderAvatarPicture'))));
check('…is called from the initials path and after the /me refresh', (T.match(/setHeaderAvatarPicture\((user|data\.user)\.picture_url\)/g) || []).length === 2);

// ---- 5. the toggle reflects its state ----
check('the click toggle writes aria-expanded', /const open = userDropdownMenu\.classList\.toggle\('open'\);\s*userMenuButton\.setAttribute\('aria-expanded', open \? 'true' : 'false'\)/.test(T));
check('an outside click resets aria-expanded', /userDropdownMenu\.classList\.remove\('open'\);\s*userMenuButton\.setAttribute\('aria-expanded', 'false'\)/.test(T));

// ---- 6. light theme redefines lightness only ----
const light = block(TH, '[data-theme="light"] .ind-avatar {');
check('light theme overrides the fill/ink/ring tokens…', /--header-avatar-fill/.test(light) && /--header-avatar-ink/.test(light) && /--header-avatar-ring/.test(light));
check('…and nothing else (no shape, no border, no size)', !/border|radius|width|height/.test(light));
check('the old light-theme --avatar-color override is gone', !/\[data-theme="light"\] \.ind-avatar \{[^}]*--avatar-color/.test(TH) && !/\[data-theme="light"\] #user-avatar-initial/.test(TH));

// ---- 7. the server hands the picture over ----
check('/me carries picture_url', /'picture_url':\s*picture_url,/.test(PY));
check('…read from the linked OAuth accounts in its own session', /def _user_picture_url\(user_id/.test(PY) && /OAuthAccount\.provider_picture_url/.test(PY) && /with get_db_session\(\) as db_session/.test(PY.slice(PY.indexOf('def _user_picture_url'))));
check('…http(s) only, never raises', /startswith\(\('https:\/\/', 'http:\/\/'\)\)/.test(PY) && /except Exception as exc:/.test(PY.slice(PY.indexOf('def _user_picture_url'), PY.indexOf('def _user_picture_url') + 1200)));

// ---- 8. pins ----
const pin = (re) => parseFloat((T.match(re) || [, '0'])[1]);
check('themes.css pin ≥ 1.8 (bumped for this change)', pin(/css\/themes\.css'\) \}\}\?v=([\d.]+)"/) >= 1.8);
check('main.css pin ≥ 5.13 (bumped for this change)', pin(/css\/main\.css'\) \}\}\?v=([\d.]+)"/) >= 5.13);

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
