// S9 — Grounded Multimodal: the consumer surfaces, EXECUTED under the shared DOM stub + pinned.
//   (1) the badge vocabulary (verified / unverified / unread — the state is on the badge as a
//       word, never colour alone) and the alt text FROM the extraction; (2) the user bubble:
//       `renderAttachmentChips` with the turn's stamp — thumbnail, "used as source", and the
//       live flip (read → used) through `markUsed` (session-guarded); (3) the capsule's Sources
//       row — an image with a region crop, a transcription named as unverified; (4) the
//       lineage dialog's attachment statement; (5) the wiring: the Live Status card, the reload
//       decoration, the editor's attachmentConfig round-trip, the script tag, the CSS tokens.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

const { doc, body } = installDom({ __currentSessionId: 'sess-1' });
globalThis.CSS = { escape: (s) => String(s).replace(/[^\w-]/g, (c) => '\\' + c) }; // the browser's CSS.escape leaves [\w-] alone
globalThis.URL = Object.assign(globalThis.URL || {}, { createObjectURL: () => 'blob:local', revokeObjectURL() {} });
const fetched = [];
globalThis.fetch = async (url) => { fetched.push(url); return { ok: /content\?session_id=sess-1/.test(url), blob: async () => ({}) }; };
const log = new El('div', { id: 'chat-log' }); body.appendChild(log); doc._register('chat-log', log);
await import(pathToFileURL('static/js/handlers/attachmentSources.js').href);
const AS = window.AttachmentSources;
check('the module publishes its surface', AS && ['badgeHtml', 'decorate', 'markUsed', 'hydrate', 'sourcesRowHtml', 'statementHtml', 'altText'].every(k => typeof AS[k] === 'function'));

const verified = { file_id: 'f-img', filename: 'table.png', kind: 'image', method: 'ocr', verified: true, engine: 'rapidocr', width: 520, height: 200, text_chars: 0,
    tables: [{ index: 1, columns: ['Region', 'Revenue', 'Orders'], row_count: 3, region: [18, 18, 424, 173], page: null, sample_rows: [{ Region: 'EMEA', Revenue: '48,210.50', Orders: '312' }] }], statement_ns: [1] };
const transcribed = { file_id: 'f-chart', filename: 'chart.png', kind: 'image', method: 'vision_transcription', verified: false, engine: 'model', text_chars: 40, tables: [{ index: 1, columns: ['a', 'b'], row_count: 2 }] };
const unread = { file_id: 'f-blank', filename: 'blank.png', kind: 'image', method: 'none', verified: false, engine: 'rapidocr', text_chars: 0, tables: [], note: 'the OCR engine found no text in the image' };
const csv = { file_id: 'f-csv', filename: 'd.csv', kind: 'csv', method: 'csv', verified: true, engine: 'csv', text_chars: 0, tables: [{ index: 1, columns: ['Region', 'Revenue'], row_count: 2 }] };

// ── (1) the badge + alt text ──────────────────────────────────────────────────────────────
{
  const b = AS.badgeHtml(verified, false);
  check('badge: a verified read says read · method · rows, state on the badge', /as-verified/.test(b) && /data-state="verified"/.test(b) && /read · OCR · 1 table · 3 rows/.test(b), b);
  check('badge: after the turn it says used as source', /used as source · OCR/.test(AS.badgeHtml(verified, true)));
  const u = AS.badgeHtml(transcribed, true);
  check('badge: a transcription says unverified and its tip says never evidence', /as-unverified/.test(u) && /transcribed · unverified/.test(u) && /never evidence/.test(u), u);
  const n = AS.badgeHtml(unread, true);
  check('badge: an unread file says not readable and names the reason + native', /as-unread/.test(n) && /not readable/.test(n) && /found no text/.test(n) && /natively/.test(n), n);
  check('badge tooltips ride the platform tooltip, never a native title', /u-tip/.test(b) && !/ title=/.test(b + u + n));
  check('alt text comes from the extraction: the table\'s columns, rows, who read it', AS.altText(verified, 'table.png') === 'table.png: table of Region, Revenue, Orders, 1 table · 3 rows (read by the platform)', AS.altText(verified, 'table.png'));
  check('alt text for a transcription names the model', /transcribed by the model/.test(AS.altText(transcribed, 'chart.png')));
  check('alt text for an unread image says so', /not readable/.test(AS.altText(unread, 'blank.png')));
  check('state vocabulary is the one rule', AS.state(verified) === 'verified' && AS.state(transcribed) === 'unverified' && AS.state(unread) === 'unread');
}

// ── (2) the user bubble: renderAttachmentChips + the live flip ────────────────────────────
const CDU = read('static/js/handlers/chatDocumentUpload.js');
const chipsSrc = CDU.slice(CDU.indexOf('export function renderAttachmentChips'), CDU.indexOf('// Expose remove function globally'));
const formatFileSize = (n) => `${n} B`; const getImageIcon = () => '<svg class="chip-icon"></svg>'; const getDocumentIcon = () => '<svg class="chip-icon doc"></svg>';
const renderAttachmentChips = new Function('formatFileSize', 'getImageIcon', 'getDocumentIcon', 'window', chipsSrc.replace('export function', 'return function'))(formatFileSize, getImageIcon, getDocumentIcon, window);
const atts = [{ file_id: 'f-img', filename: 'table.png', file_size: 100, is_image: true }, { file_id: 'f-csv', filename: 'd.csv', file_size: 50, is_image: false }];
{
  const plain = renderAttachmentChips(atts);
  check('no stamp ⇒ today\'s chips: kind badge, file id carried, no thumbnail', /as-kind/.test(plain) && /data-file-id="f-img"/.test(plain) && !/data-as-thumb/.test(plain) && !/as-badge/.test(plain), plain);
  const reload = renderAttachmentChips(atts, [verified, csv]);
  check('with the stamp ⇒ the image is its thumbnail with alt text, both chips wear used-as-source', /img data-as-thumb data-file-id="f-img"/.test(reload) && /alt="table.png: table of Region/.test(reload) && (reload.match(/used as source/g) || []).length === 2 && /data-as-used="verified"/.test(reload), reload);
  const live = renderAttachmentChips(atts, [verified, csv], false);
  check('at submit (used=false) the chips say read, not used, and stay flippable', /read · OCR/.test(live) && !/used as source/.test(live) && !/data-as-used/.test(live), live);
  // the live flip
  const bubble = new El('div', { class: 'message-bubble' }); bubble.innerHTML = live; log.appendChild(bubble);
  const n = AS.markUsed('sess-1', { sources: [verified, csv], verified: 2, unverified: 0, none: 0 });
  check('markUsed decorates the last user bubble\'s chips: both flip to used as source', n === 2 && (bubble.innerHTML.match(/used as source/g) || []).length === 2 || bubble.querySelectorAll('.as-badge').length === 2, bubble.innerHTML);
  // the guard must be load-bearing on an UNDECORATED bubble — on a decorated one idempotency
  // alone returns 0 and a missing guard would escape
  const fresh = new El('div', { class: 'message-bubble' }); fresh.innerHTML = renderAttachmentChips(atts, [verified, csv], false); log.appendChild(fresh);
  check('markUsed is session-guarded (another session\'s card never marks this bubble)', AS.markUsed('other', { sources: [verified, csv] }) === 0 && !/used as source/.test(fresh.innerHTML) && fresh.querySelectorAll('[data-as-used]').length === 0);
  fresh.remove();
  check('markUsed is idempotent (a second card does not double-badge)', AS.markUsed('sess-1', { sources: [verified, csv] }) === 0 && bubble.querySelectorAll('.as-badge').length === 2);
  check('hydrate asked the session-scoped content endpoint for the image, once', fetched.filter(u => /upload\/f-img\/content\?session_id=sess-1/.test(u)).length === 1, fetched);
}

// ── (3) the capsule's Sources row ─────────────────────────────────────────────────────────
{
  const td = { attachment_sources: { sources: [verified, transcribed, unread, csv], verified: 2, unverified: 1, none: 1 } };
  const row = AS.sourcesRowHtml(td, 'sess-1');
  check('one entry per source, the verified image as a crop canvas over the cited region', (row.match(/class="as-source /g) || []).length === 4 && /canvas data-as-crop data-file-id="f-img" data-region="18,18,424,173"/.test(row), row);
  check('the crop is a labelled image for a screen reader', /role="img" aria-label="table.png: the cited region"/.test(row));
  check('a transcription is named unverified, an unread file not readable, a csv without a crop', /chart\.png[\s\S]*transcribed · unverified/.test(row) && /blank\.png[\s\S]*not readable/.test(row) && !/data-file-id="f-csv"[^>]*data-as-crop/.test(row));
  check('the tip names the result set a verified table was registered as', /result set 1/.test(row));
  check('no stamp ⇒ no row', AS.sourcesRowHtml({}, 'sess-1') === '' && AS.sourcesRowHtml({ attachment_sources: { sources: [] } }, 'sess-1') === '');
}

// ── (4) the lineage dialog ────────────────────────────────────────────────────────────────
{
  const st = { n: 1, tool: 'attachment', statement: 'attachment:table.png#1 — ocr/rapidocr, verified', columns: ['Region', 'Revenue'], row_count: 3, source: { file_id: 'f-img', filename: 'table.png', method: 'ocr', engine: 'rapidocr', region: [18, 18, 424, 173], page: null, width: 520, height: 200, table: 1 }, sample_rows: [{ Region: 'EMEA', Revenue: '48,210.50' }] };
  const h = AS.statementHtml(st, 'sess-1');
  check('an attachment statement opens on the file, the method and a crop of the region', /Quoted from the attachment table\.png — OCR \(rapidocr\), verified/.test(h) && /data-as-crop/.test(h) && /outlined/.test(h), h);
  check('a tool statement yields nothing here', AS.statementHtml({ n: 2, tool: 'base_readQuery', statement: 'SELECT 1' }, 'sess-1') === '');
}

// ── (5) the wiring ────────────────────────────────────────────────────────────────────────
{
  const UI = read('static/js/ui.js');
  check('ui.js: the attachment_sources card is in the assurance chain (both sites) and has its renderer', (UI.match(/type === 'attachment_sources'/g) || []).length >= 2 && /function _renderAttachmentSourcesDetails/.test(UI) && (UI.match(/case 'attachment_sources': \{/g) || []).length === 2 && /type === "attachment_sources"/.test(UI));
  check('ui.js: the card moves the dial AND marks the user bubble on all THREE render paths (the standard one is the live path — the step precedes engine dispatch)', (UI.match(/AssuranceDial\.note\(_adSession\(\), 'attachment_sources', details\)/g) || []).length === 3 && (UI.match(/AttachmentSources\.markUsed\(_adSession\(\), details\)/g) || []).length === 3);
  const rendSrc = UI.slice(UI.indexOf('function _renderAttachmentSourcesDetails'), UI.indexOf('function _renderToolSurfaceDetails'));
  const render = new Function('escapeHtml', '_mechLink', '_trustChip', rendSrc + '\nreturn _renderAttachmentSourcesDetails;')((s) => String(s), (k) => k, (lvl, w) => `<chip ${lvl}>${w}</chip>`);
  const card = render({ sources: [verified, transcribed], verified: 1, unverified: 1, none: 0 });
  check('the card: unverified present ⇒ warn word names it; verified and transcribed rows both listed', /<chip warn>1 transcribed, unverified<\/chip>/.test(card) && /Verified<\/div>/.test(card) && /Transcribed by the model/.test(card) && /may rest on it/.test(card), card);
  check('the card: verified only ⇒ pass; unread only ⇒ na', /<chip pass>1 read by the platform, verified/.test(render({ sources: [verified] })) && /<chip na>nothing could be read/.test(render({ sources: [unread] })));
  const EH = read('static/js/eventHandlers.js');
  check('eventHandlers: reload-harden synthesizes the card from the persisted stamp (first, evidence text dropped) on every engine', /turnData\.attachment_sources && Array\.isArray\(turnData\.attachment_sources\.sources\)/.test(EH) && /listKey\]\.unshift\(\{\n\s*type: 'attachment_sources'/.test(EH) && /const \{ text, \.\.\.rest \} = x; return rest;/.test(EH));
  check('eventHandlers: the reload title, the client-only keys stripped from the wire, the live bubble drawn with read-not-used', /'Attachments Read as Sources'/.test(EH) && /\(\{ _source, _objectUrl, \.\.\.a \}\) => a/.test(EH) && /renderAttachmentChips\(body\.attachments, _lastSubmittedSources, false\)/.test(EH));
  const SM = read('static/js/handlers/sessionManagement.js');
  check('reload: the user bubble\'s chips are drawn from the turn\'s persisted stamp + hydrated', /wfTurn\.attachment_sources\.sources/.test(SM) && /renderAttachmentChips\(msg\.attachments, attSources\)/.test(SM) && /AttachmentSources\.hydrate\(bubble, sessionId\)/.test(SM));
  const AC = read('static/js/handlers/answerCapsule.js');
  check('capsule: an Attachments row beside Sources, crops drawn after mount', /factRow\('Attachments', attachmentsLine\)/.test(AC) && /AttachmentSources\.sourcesRowHtml\(td, sid\)/.test(AC) && /AttachmentSources\.hydrate\(body, ids\.sid\)/.test(AC));
  const FL = read('static/js/handlers/figureLineage.js');
  check('lineage dialog: an attachment statement routes through the source renderer and its own rows', /st\.source && window\.AttachmentSources/.test(FL) && /statementHtml\(st, sid\)/.test(FL) && /st\.sample_rows/.test(FL));
  check('composer: the remove control is a named button; the thumbnail is the local file; the read badge from the upload response', /aria-label', `Remove \$\{att\.filename\}`/.test(CDU) && /AS\.thumbHtml\(att\.file_id, AS\.altText\(att\._source, att\.filename\), true\)/.test(CDU) && /blobToDataUrl\(f\)/.test(CDU) && /AS\.badgeHtml\(att\._source, false\)/.test(CDU) && !/nameSpan\.title = /.test(CDU));
  const DIAL = read('static/js/handlers/assuranceDial.js');
  check('the dial routes the event to GROUNDED', /attachment_sources: 'grounded'/.test(DIAL));
  const CH = read('static/js/handlers/configurationHandler.js');
  check('editor: attachmentConfig populated and saved through carryForward, unchecked writes false', /a\.transcribeImages === true/.test(CH) && /carryForward\(\{ transcribeImages: !!el\.checked \}, 'attachmentConfig'/.test(CH));
  const IDX = read('templates/index.html');
  check('index.html: the script is pinned after turnLineage; the editor card + checkbox exist', /turnLineage\.js'\) \}\}\?v=[\d.]+"><\/script>\n\s*<script src="\{\{ url_for\('static', filename='js\/handlers\/attachmentSources\.js'\) \}\}\?v=[\d.]+"><\/script>/.test(IDX) && /id="profile-attachment-transcribe"/.test(IDX) && /id="profile-card-attachment-sources"/.test(IDX));
  const CSSs = read('static/css/main.css');
  const block = CSSs.slice(CSSs.indexOf('/* S9 — attachment sources'), CSSs.indexOf('/* Attachment chip base'));
  const hexes = block.replace(/var\([^)]*\)/g, '').match(/#[0-9a-fA-F]{3,6}\b/g) || [];
  check('CSS: the S9 block uses theme tokens only (no bare hex outside a var() fallback) and the state is also a border style', hexes.length === 0 && /as-unverified[^}]*border-style: dashed/.test(block) && /as-unread[^}]*border-style: dotted/.test(block), hexes);
}

console.log(`${fail ? 'FAILURES' : 'ALL PASS'} — ${pass} assertions (${fail} failed)`);
process.exit(fail ? 1 : 0);
