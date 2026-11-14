#!/usr/bin/env node
/**
 * Files UX compliance (PROP-010 §4.7.1) — the conventions every Uderia component follows,
 * asserted on the Files sources so they cannot drift. No server, no browser.
 *
 *   chrome      the aside is an .sp-panel under #chat-canvas-split, registered for its accent
 *   workspace   one WORKSPACE_REGISTRY entry, its real total, live on uderia:files-changed
 *   controls    industrial buttons (.card-btn) and pills (.ind-tab--pill); no hand-rolled button
 *               classes; never a native dialog; tooltips are data-tip, never title=
 *   identity    IFOC words, never a raw profile type; monochrome SVG, no emoji
 *   theme       the injected CSS uses theme variables (a hex only as a var() fallback)
 *   gating      Keep is revealed only through ComponentGate for the agent that answered
 *   truth       "Kept · Open" comes from the server (/files/kept), never a click-time flag
 *   live        the panel, the Keep acts and the Workspace follow uderia:files-changed
 *   exit        downloads go through the one governed route (/download), nothing else
 *   a11y        keyboard map wired, aria-live announcements, reduced motion honoured
 *   exports     every export leaves a page through ONE helper (FilesKeep.deliver): download as
 *               before, record it, offer Save to Files; a server-built export saves server bytes
 *   snapshot    a dashboard snapshot sends only pictures of drawn tiles, keyed by tile id
 *   flow        the File nodes come from the one palette list, gated on Files, no shortcut key
 *   knowledge   Add to knowledge goes through the repository's own flow, said in words
 *   agent tool  TDA_Files is opt-in per agent; its companion skill follows the tool
 *
 * Run: node test/test_files_ux.mjs
 */
import fs from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };
const read = (p) => fs.readFileSync(p, 'utf8');

const R = read('components/builtin/files/renderer.js');
const K = read('static/js/handlers/filesKeep.js');
const A = read('static/js/handlers/fileStoreAdmin.js');
const W = read('static/js/handlers/workspacePanelHandler.js');
const C = read('static/js/handlers/componentHandler.js');
const U = read('static/js/handlers/chatDocumentUpload.js');
const N = read('static/js/notifications.js');
const T = read('templates/index.html');
const M = JSON.parse(read('components/builtin/files/manifest.json'));
const FILES = { renderer: R, filesKeep: K, fileStoreAdmin: A };

// ── chrome ───────────────────────────────────────────────────────────────────
const aside = T.indexOf('<aside id="files-split-panel"');
check('the Files aside exists', aside > 0);
check('…as an .sp-panel (shared chrome)', /<aside id="files-split-panel" class="sp-panel"/.test(T));
check('…inside #chat-canvas-split', aside > T.indexOf('id="chat-canvas-split"'));
check('…with the shared header classes (sp-header / sp-title / sp-action-btn)',
    /id="files-split-title" class="sp-title"/.test(T) && /id="files-split-close" class="sp-action-btn"/.test(T));
check('the aside is registered for its manifest accent', /files:\s*'files-split-panel'/.test(C));
check('the manifest carries identity (display name, monochrome icon, accent)',
    M.display_name === 'Files' && /stroke="currentColor"/.test(M.icon_svg) && /^#[0-9a-f]{6}$/i.test(M.icon_accent));
check('the LLM tool is TDA_Files, served by the component handler', M.tool_definition && M.tool_definition.name === 'TDA_Files'
    && M.backend && M.backend.handler_class === 'FilesComponentHandler');
const REG = JSON.parse(read('skills/skill_registry.json'));
const _regList = REG.skills || REG.builtin_skills || Object.values(REG).find(Array.isArray) || [];
check('its companion skill is registered against the component', _regList.some(x => x.skill_id === 'files-expert' && x.component_id === 'files'));
const HANDLER = read('components/builtin/files/handler.py');
check('the tool is offered only with the owner\'s opt-in (offers_tool reads componentConfig.files.agentTool)',
    /def offers_tool/.test(HANDLER) && /cc\.get\("agentTool"\) is True/.test(HANDLER));
check('a call without the opt-in is refused in the handler too', /if not agent_tool_on\(profile\):/.test(HANDLER));
const INS = JSON.parse(read('components/builtin/files/instructions.json'));
check('the instructions forbid emoji and never ask the model to retype rows',
    Object.values(INS).filter(Boolean).every(t => /No emoji/.test(t) && /never retype rows/.test(t)));

// ── workspace ────────────────────────────────────────────────────────────────
const wsEntry = W.slice(W.indexOf('    files: {'), W.indexOf('    obda_read: {'));
check('one Workspace registry entry for files', wsEntry.length > 100);
check('…says its real total, not the page size', /total:\s*\(items\)/.test(wsEntry) && /reg\.total\(items\)/.test(W));
check('…opens through the component renderer (one panel instance)', W.includes("import('/api/v1/components/files/renderer')"));
check('the Workspace follows every component\'s changes through the one bus subscriber', W.includes("UderiaBus.onComponent('*'"));

// ── controls ─────────────────────────────────────────────────────────────────
for (const [name, src] of Object.entries(FILES)) {
    check(`${name}: no native dialog`, !/\b(window\.)?(alert|confirm|prompt)\s*\(/.test(src.replace(/showConfirmBanner|confirmIt|_confirm/g, '')));
    check(`${name}: no native title= tooltip in markup`, !/<[a-z][^>]*\stitle="/i.test(src));
    check(`${name}: no emoji`, !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B50}\u{2705}\u{274C}\u{2716}]/u.test(src));
    check(`${name}: no hand-rolled button classes`, !/\b(fl-btn|fl-primary|fl-caret|fl-chip|fl-seg)\b/.test(src));
}
check('the Download button is the industrial primary card button',
    /class="card-btn card-btn--primary" data-act="download"/.test(R));
check('Delete is the industrial danger card button', /class="card-btn card-btn--danger" data-act="delete"/.test(R));
check('kind filters and switches are industrial pills', /ind-tab ind-tab--pill\$\{S\.kind === k/.test(R) && /data-group="\$\{g\}"/.test(R));
check('the picker uses industrial buttons', /data-go disabled class="card-btn card-btn--primary"/.test(K));
check('the admin tabs use industrial buttons and pills', /card-btn card-btn--primary/.test(A) && /ind-tab ind-tab--pill/.test(A) && !/const PRIMARY|const BTN/.test(A));
check('attachment chips keep with an industrial button', /chip-keep-btn card-btn card-btn--neutral card-btn--sm/.test(U));

// ── identity ─────────────────────────────────────────────────────────────────
check('IFOC words for every profile type', ['Optimize', 'Ideate', 'Focus', 'Coordinate'].every(w => R.includes(`label: '${w}'`)));
check('a raw profile type is never printed', !/\$\{esc\(f\.profile_type\)\}/.test(R) && !/>\s*(tool_enabled|llm_only|rag_focused|genie)\s*</.test(R));
check('the agent word stays readable (text colour + IFOC dot)', /function _agentHtml/.test(R));

// ── theme ────────────────────────────────────────────────────────────────────
const css = (R.match(/s\.textContent = `([\s\S]*?)`;/) || [, ''])[1];
const bareHex = css.replace(/var\([^)]*\)/g, '').match(/#[0-9a-fA-F]{3,8}\b/g) || [];
check(`the injected CSS uses theme variables (bare hex: ${bareHex.join(' ') || 'none'})`, bareHex.every(h => /^#fff$/i.test(h)));
check('the injected CSS honours reduced motion', /prefers-reduced-motion/.test(css));

// ── gating and truth ─────────────────────────────────────────────────────────
check('Keep is revealed through ComponentGate for the agent that answered',
    /ComponentGate\.isEnabled\('files', profileId\)/.test(K) && /b\.hidden = !ok/.test(K));
check('Keep starts hidden (no claim of capability before the gate answers)', /b\.hidden = true/.test(K));
check('"Kept · Open" is read from the server', K.includes('/api/v1/files/kept?') && /setKept\(btn, rec\)/.test(K));
check('the turn and agent come from the ONE answer-origin derivation', K.includes('window.__answerOrigin'));
check('Pin and Keep share that derivation (ui.js exports it)', read('static/js/ui.js').includes('window.__answerOrigin = _deriveTableOrigin'));
check('the file picker offers exactly what the server accepts (tables included)',
    /DOM\.fileAttachInput\.accept = fmts\.join\(','\)/.test(U) && /id="file-attach-input" multiple\s+accept="[^"]*\.csv,\.tsv,\.xlsx"/.test(T));
check('the paperclip is unchanged where Files is off', /if \(!filesOn\) \{ DOM\.fileAttachInput\.click\(\); return; \}/.test(U));

// ── live ─────────────────────────────────────────────────────────────────────
check('the server notice becomes uderia:component-changed (the one change event)', /case 'component_changed'/.test(N) && N.includes("UderiaBus.emit('uderia:component-changed'"));
check('the panel follows it', R.includes("UderiaBus.onComponent('files'"));
check('the Keep acts follow it', K.includes("UderiaBus.onComponent('files'"));

// ── the governed exit ────────────────────────────────────────────────────────
check('single downloads go through /download', /\/api\/v1\/files\/\$\{encodeURIComponent\(id\)\}\/download/.test(R));
check('bulk downloads go through the zip route', R.includes("'/api/v1/files/download'"));
check('the panel never reads the attachment content endpoint', !R.includes('/chat/upload/'));
check('previews come from the bounded preview route, never the raw file', R.includes('/preview') && !/src="\$\{esc\(.*\/download/.test(R));
check('Markdown previews escape raw HTML before rendering', /replace\(\/</.test(R) && R.includes('marked.parse(safe)'));

// ── a11y ─────────────────────────────────────────────────────────────────────
for (const key of ["e.key === '/'", "'ArrowDown'", "e.key === 'F2'", "'Delete'", "e.key === 'Escape'", "e.key === ' '"]) {
    check(`keyboard: ${key}`, R.includes(key));
}
check('changes are announced (aria-live)', /aria-live="polite"/.test(R) && /aria-live/.test(K));
check('icon-only buttons are named', /aria-label="Download \$\{esc\(f\.filename\)\}"/.test(R) && /aria-label="List view"/.test(R));
check('the list is a listbox with selectable options', /role="listbox"/.test(R) && /role="option"/.test(R));

// ── exports: ONE exit for every browser-built export (PROP-010 phase 3) ────────
const EXPORT_SITES = {
    'static/js/eventHandlers.js': 1, 'static/js/ui.js': 1, 'static/js/handlers/evidenceView.js': 1,
    'static/js/handlers/answerCapsule.js': 1, 'static/js/handlers/agentPerformanceHandler.js': 1,
    'static/js/executionDashboard.js': 2, 'components/builtin/knowledge_graph/renderer.js': 2,
    'components/builtin/canvas/renderer.js': 1, 'components/builtin/action/renderer.js': 1,
    'components/builtin/explain_plan/renderer.js': 1, 'static/js/api.js': 2, 'static/js/handlers/instancePortabilityUI.js': 1,
};
for (const [file, n] of Object.entries(EXPORT_SITES)) {
    const src = read(file);
    check(`${file}: its export${n > 1 ? 's go' : ' goes'} through FilesKeep.deliver (${n})`, (src.match(/window\.FilesKeep\.deliver\(/g) || []).length === n);
}
check('no export site keeps its own download anchor', !/a\.download = `receipt-|a\.download = 'knowledge-graph\.png'|link\.download = `session_|a\.download = `explain-plan-|a\.download = `uderia_audit_pack_/
    .test(Object.keys(EXPORT_SITES).map(read).join('\n')));
const API = read('static/js/api.js');
check('a server-built export names its own route to save from (the server\'s bytes, ?to=files)',
    /serverBuilt: true,\s*saveUrl: `\/api\/v1\/knowledge-graph\/export\?\$\{params\}`/.test(API)
    && /saveUrl: `\/api\/v1\/components\/\$\{componentId\}\/instances\/\$\{instanceId\}\/export`/.test(read('static/js/handlers/instancePortabilityUI.js')));
check('deliver downloads exactly as before, records a browser-built export, then offers',
    /function deliver\(blob, filename, meta = \{\}\) \{\s*download\(blob, filename\);/.test(K) && K.includes("'/api/v1/files/exports/recorded'")
    && /if \(!meta\.serverBuilt\)/.test(K) && /filesAvailable\(\)\.then\(ok => \{ if \(ok\) offer\(/.test(K));
check('the Save-to-Files toast is industrial, announced and motion-safe',
    /data-save class="card-btn card-btn--primary card-btn--sm">Save to Files/.test(K) && /el\.setAttribute\('aria-live', 'polite'\)/.test(K)
    && /#files-export-offer\{transition:none\}/.test(K));
check('a duplicate save is named, with a second copy only on request', /Already in Files/.test(K) && /Save another copy/.test(K));

check('the inline card of an agent save is a registered renderer (else its row renders empty)',
    /files: 'renderFiles'/.test(read('static/js/componentRenderers.js')) && /export const renderersReady = Promise\.allSettled/.test(read('static/js/componentRenderers.js')));

// ── dashboard snapshot ───────────────────────────────────────────────────────
const D = read('components/builtin/dashboard/renderer.js');
check('the snapshot button is hidden until Files is available', /id="dashboard-split-snapshot"[^>]*hidden/.test(T) && /snapBtn\.hidden = !ok/.test(D));
check('the snapshot sends pictures keyed by tile id, never the tiles\' html', /images\[t\.id\] = pic/.test(D)
    && /body: JSON\.stringify\(\{ dashboard_id: _currentDashboardId, images \}\)/.test(D));
check('the saved snapshot is announced through the one Files toast', /window\.FilesKeep\.announceSaved\(d\.file\)/.test(D));

// ── flow File nodes ──────────────────────────────────────────────────────────
const F = read('components/builtin/flow/renderer.js');
check('the File nodes are in the ONE palette list, gated on Files, with no shortcut key',
    /type: 'file_input', label: 'File input', short: 'File in', key: null, files: true/.test(F)
    && /type: 'file_output', label: 'File output', short: 'File out', key: null, files: true/.test(F)
    && /\(!n\.files \|\| filesOn\)/.test(F));
check('the canvas probes Files before offering them', /setFilesOn\(!!\(d && d\.available\)\)/.test(F));
check('a File input chooses ONE file through the shared Files picker, with the executor\'s own rule',
    /window\.FilesKeep\.pick\(\{[\s\S]{0,400}single: true,[\s\S]{0,400}accept: _fileInputAccepts/.test(F));
check('a File input without a file is an error before the run', /File input "\$\{n\.data\?\.label \|\| n\.id\}" has no file chosen/.test(F));
check('after a run the node opens its file in Files', /UderiaBus\.emit\('uderia:flow-open-file'/.test(F) && /UderiaBus\.on\('uderia:flow-open-file'/.test(K));
check('the File nodes take the Files accent', /file_input:\s+'#78716c'/.test(F) && /file_output:\s+'#78716c'/.test(F));

// ── knowledge ────────────────────────────────────────────────────────────────
check('Add to knowledge reads the caller\'s repositories and posts to the repository flow',
    R.includes("'/files/knowledge-targets'") && /\/files\/\$\{encodeURIComponent\(f\.id\)\}\/to-knowledge/.test(R));
check('with no repository, the chooser says where to make one', /Create one in Intelligence → Knowledge/.test(R));

// ── stores and keys: one catalog, test before save ──────────────────────────
check('the Add-store form draws each backend\'s own fields from the ONE catalog (no backend hard-coded)',
    /const spec = backends\.find\(b => b\.id === st\.backend\)/.test(A) && !/st\.backend === 's3'/.test(A)
    && /missing = \(spec\.fields \|\| \[\]\)\.find\(f => f\.required/.test(A));
check('testing an edited store fills its masked secrets from the saved store', /existing_id: existing \? existing\.id : null/.test(A));
check('a Keys tab sits beside Stores, Policy and Capacity', /\{ id: 'keys', label: 'Keys', render: renderKeys \}/.test(A));
check('a key manager is used only after a passing test, and confirmed',
    /data-use class="card-btn card-btn--primary" \$\{st\.tested \? '' : 'disabled'\}/.test(A) && /confirmIt\(`New files' keys will be wrapped by/.test(A));
check('re-wrapping counts first (dry run) and asks', /dry_run: true \}\) \}\);[\s\S]{0,200}confirmIt\(`Re-wrap/.test(A));
check('the key list names which key the server says is active (no client copy of the rule)', /const activeKey = v\.active_key;/.test(A));

// ── the agent opt-in in the profile editor ──────────────────────────────────
const CH = read('static/js/handlers/configurationHandler.js');
check('the editor offers the opt-in under the Files row and stores componentConfig.files.agentTool',
    /class="comp-tool-optin" data-component-id="files"/.test(CH) && /componentConfig\[compId\]\.agentTool = true/.test(CH));
check('the companion skill row follows the opt-in (the tool it teaches)',
    /\(!optin \|\| !!optin\.checked\)/.test(CH) && /classList\?\.contains\('comp-tool-optin'\)/.test(CH));

// ── session switch (the Agent Memory precedent: user-level, closes, no per-session marker) ──
const SM = read('static/js/handlers/sessionManagement.js');
check('a session switch closes the Files panel', SM.includes("import('/api/v1/components/files/renderer')"));
check('the session remembers an open Files panel and the selected file (written on open and on selection)',
    /localStorage\.setItem\(`files_state_\$\{sid\}`, JSON\.stringify\(\{ open: true, focus: S\.sel \|\| null \}\)\)/.test(R)
    && /if \(S\.sel === id && isFilesPanelOpen\(\)\) _persistOpenState\(\)/.test(R));
check('only the user\'s own close forgets it; a switch-away close keeps it',
    /onclick = _manualClose;/.test(R) && /function _manualClose\(\) \{[\s\S]{0,200}removeItem\(`files_state_/.test(R)
    && !/removeItem\(`files_state_/.test(SM));
check('returning to the session (switch or reload) reopens Files on that file',
    (SM.match(/_restoreFilesState\(sessionId\);/g) || []).length === 2 && /openFilesPanel\(info\.focus \? \{ focus: info\.focus \} : \{\}\)/.test(SM));
check('a turn reading a file updates its "Used in" live (no reload)',
    /if \(d\.action === 'used'\) \{[\s\S]{0,400}_loadDetail\(S\.sel\)/.test(R));

// ── a chat-log chip states what its attachment is NOW ──
check('chips read their state from the conversation\'s own list (one fetch per session), not the kept list',
    /\/api\/v1\/files\/attachments\?session_id=/.test(K) && !/\/api\/v1\/files\/kept\?session_id=\$\{encodeURIComponent\(sid\)\}`, \{ headers: hdrs\(\) \}\)\s*\.then\(r => \(r\.ok \? r\.json\(\) : \{ kept/.test(K));
check('a deleted attachment says Deleted, says the answer stays, and offers Restore while it can',
    /tag\.textContent = 'Deleted';/.test(K) && /The answer stays/.test(K) && /b\.textContent = 'Restore';/.test(K)
    && /if \(st\.state !== 'deleted' \|\| !st\.file_id\) return;/.test(K));
check('an unreadable list says nothing (never a false "Deleted")',
    /states\.readable \? \{ state: 'gone' \} : null/.test(K) && /readable: d\.manifest !== false/.test(K));
check('chips follow deletes, restores and purges made anywhere, but not a turn reading the file',
    /const chipsChanged = !e \|\| !e\.detail \|\| e\.detail\.action !== 'used';/.test(K) && /if \(chipsChanged\) ATT\.clear\(\);/.test(K));
// The chip loop (prod, 2026-10-25): wiring rebuilt the chip's button every time, the chat-log observer
// answered that insert by wiring the bubble again — every animation frame, for as long as a
// conversation with an attachment was open; on an iPad no control in that conversation answered.
const _wire = K.slice(K.indexOf('async function wireLogChips'), K.indexOf('// ── "From Files…"'));
check('a chip already showing its state is left untouched (the check comes BEFORE any DOM write)',
    /if \(chip\.dataset\.fkSig === sig\) return;\s*chip\.dataset\.fkSig = sig;/.test(_wire)
    && _wire.indexOf('chip.dataset.fkSig === sig') < _wire.indexOf(".forEach(el => el.remove())")
    && _wire.indexOf('chip.dataset.fkSig === sig') < _wire.indexOf("chip.classList.remove('fk-chip-deleted')"));
check('…and the signature names everything the chip shows (state, file, purge date, keep/kept)',
    /`\$\{st\.state\}\|\$\{st\.file_id \|\| ''\}\|\$\{st\.purge_after \|\| ''\}`/.test(_wire)
    && /`live\|\$\{st\.file_id \|\| ''\}\|\$\{st\.lifetime === 'kept' \? 'kept' : 'keep'\}`/.test(_wire));
check('the chat-log observer does not wire a bubble for the chip decorations this module adds',
    /if \(n\.matches && n\.matches\('\.fk-chip-keep, \.fk-chip-state'\)\) return;/.test(K));

// ── the agent is shown as the agent (prod, 2026-10-25: "@UDERIA" plain text; files grouped under "Focus") ──
check('Files shows an agent with the platform\'s agent pill (renderProfileTag), its class word beside it',
    /function _agentPill\(a, f, strong\)[\s\S]{0,500}window\.renderProfileTag\(esc\(a\.tag\), f\.profile_type/.test(R));
check('…in the list row, the grid card and the detail\'s Agent row (no "@tag" as plain text)',
    /\$\{_agentPill\(a, f\)\} · \$\{esc\(f\.deleted_at/.test(R) && /\$\{_agentPill\(_agentOf\(f\), f\)\}<\/span>/.test(R)
    && /const agentV = _agentPill\(a, f, true\);/.test(R) && !/` · @\$\{esc\(a\.tag\)\}`/.test(R));
check('Group · Agent groups by the agent (its pill heads the group), not by IFOC class',
    /else if \(S\.group === 'agent'\) \{ const a = _agentOf\(f\); key = a\.tag \? '@' \+ a\.tag : a\.label;/.test(R)
    && !/agent: \['Optimize', 'Coordinate', 'Focus', 'Ideate', 'No agent'\]/.test(R)
    && /g\.agent && g\.agent\.a\.tag \? _agentPill\(g\.agent\.a, g\.agent\.f\)/.test(R));

// ── the Files tab in Components → Files (the instance-management tab every stateful component has) ──
const FT = read('static/js/handlers/filesTabHandler.js');
check('the Files component detail carries a Files tab, after Profiles, initialised by its handler',
    /comp\.component_id === 'files'\) \{[\s\S]{0,600}label: 'Files'[\s\S]{0,200}files-tab-root/.test(C)
    && /window\.filesTabHandler\?\.initFilesTab\('files-tab-root', comp\)/.test(C)
    && /js\/handlers\/filesTabHandler\.js'\) \}\}\?v=/.test(T));
check('the tab manages files in place: rename, keep, delete, restore, open in the panel',
    /method: 'PATCH'/.test(FT) && /\/keep`, \{ method: 'POST' \}/.test(FT) && /method: 'DELETE'/.test(FT)
    && /\/restore`, \{ method: 'POST' \}/.test(FT) && /openFilesPanel\(id \? \{ focus: id \} : \{\}\)/.test(FT));
check('the tab downloads only through the one governed exit', /\/files\/\$\{encodeURIComponent\(f\.id\)\}\/download`/.test(FT) && !/FilesKeep\.deliver/.test(FT));
check('the tab uses the same kind filters as the panel (industrial pills) and buttons (.card-btn)',
    /\['deleted', 'Recently deleted'\]/.test(FT) && /ind-tab ind-tab--pill/.test(FT) && /card-btn card-btn--danger/.test(FT));
check('the tab follows changes made anywhere (the files component on the bus; "used" refreshes only the detail)',
    /UderiaBus\.onComponent\('files'/.test(FT) && /d\.action === 'used'[\s\S]{0,200}d\.ids/.test(FT));
check('the tab never uses a native dialog, emoji or a raw profile type',
    !/\b(alert|confirm|prompt)\(/.test(FT.replace(/_confirm\(|showConfirmBanner\(/g, '')) && !/[\u{1F300}-\u{1FAFF}]/u.test(FT)
    && !/>\s*(tool_enabled|llm_only|rag_focused|genie)\s*</.test(FT));
check('the tab\'s injected CSS uses theme variables', /var\(--text-primary\)/.test(FT) && /var\(--border-primary\)/.test(FT));

// ── the panel follows the agent in view (14 Nov 2026) ──
check('the panel follows the ONE effective-profile signal (the KG panel\'s pattern), not the composer sites',
    /UderiaBus\.on\('uderia:effective-profile'/.test(R) && /export function followEffectiveProfile/.test(R)
    && !/uderia:(default-profile-changed|profile-selection-changed)/.test(R));
check('a closed panel ignores the signal; a user pick pins the filter until the panel closes',
    /files-split--open'\)\) return 'closed'/.test(R) && /if \(_agentPinned\) return 'pinned'/.test(R)
    && /S\.agent = e\.target\.value; _agentPinned = true/.test(R) && /closeFilesPanel[\s\S]{0,400}_agentPinned = false/.test(R));
check('opening reads the agent in view; a file opened by id is shown whatever agent produced it',
    /if \(opts\.focus\) S\.agent = ''/.test(R) && /else if \(!_agentPinned\) S\.agent = _effectiveProfileId\(\)/.test(R));
check('the panel\'s own sync of the select is not taken for a pick', /if \(e\.target\.value === S\.agent\) return;/.test(R));
check('a filtered empty list names the agent and offers every agent; the first-run view ignores the filter',
    /No files from \$\{who \?/.test(R) && /data-all-agents/.test(R) && /\[data-all-agents\]'\)\?\.addEventListener\('click', _showAllAgents\)/.test(R)
    && /!files\.length && !S\.q && S\.kind === 'all' && !S\.fileCount\)/.test(R));

console.log(fail ? `\n${pass} passed, ${fail} FAILED` : `ALL PASS — ${pass} assertions (Files follows the component conventions)`);
process.exit(fail ? 1 : 0);
