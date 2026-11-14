/**
 * Needs Input — the question card (PROP-011).
 *
 * A turn that stopped to ask ends `input_required`; its `input_request` (the record the
 * platform wrote) is handed to this module, which draws the card under the assistant's
 * bubble: the agent that asks and on what grounds, 1–4 items (options as buttons, a typed
 * field for `provide`, Yes/No for `confirm`), the *why* in muted text, and three actions —
 * Send · Decline · Not now. Answering goes through the COMPOSER (the one submit path):
 * the card puts the answer's display text in the input, stores the binding on
 * `window.NeedsInput.pendingAnswer`, and submits the form; `handleChatSubmit` reads the
 * binding and posts it with the message. A reply typed in the composer while a question
 * is pending follows the composer rule (`bindComposerText`): an option's value or label, or
 * free text where the question allows it, IS the answer; anything else is a new prompt and
 * the question is superseded (the server marks it; the card says so).
 *
 * Theme variables only; monochrome glyphs; one delegated click listener. Emits exactly
 * what the manifest declares: `uderia:input-required` when a pending card is attached,
 * `uderia:input-answered` when it is answered, declined, dismissed or superseded.
 */

const CSS_ID = 'needs-input-css';
const _pending = new Map();   // session_id → { request, bubble, card }
let _listening = false;
const _hasBus = () => typeof UderiaBus !== 'undefined' && !!UderiaBus;

function _injectCSS() {
    if (document.getElementById(CSS_ID)) return;
    const s = document.createElement('style');
    s.id = CSS_ID;
    s.textContent = `
.ni-card{margin-top:10px;border:1px solid var(--border-primary);border-radius:10px;background:var(--card-bg);padding:12px 14px;font-size:0.9rem;color:var(--text-primary)}
.ni-card[data-state="answered"],.ni-card[data-state="closed"]{opacity:.85}
.ni-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;color:var(--text-muted);font-size:0.78rem}
.ni-head .ni-glyph{width:14px;height:14px;flex:none;color:var(--text-muted)}
.ni-head .ni-who{color:var(--text-primary);font-weight:600}
.ni-head .ni-mark{border:1px solid var(--border-secondary);border-radius:999px;padding:0 7px;line-height:18px;font-size:0.72rem}
.ni-head .ni-mark.ni-taint{border-color:var(--status-warning,#f59e0b);color:var(--status-warning,#f59e0b)}
.ni-item{margin:8px 0 10px}
.ni-item .ni-h{font-size:0.72rem;letter-spacing:.04em;text-transform:uppercase;color:var(--text-muted);margin-bottom:3px}
.ni-item .ni-q{margin-bottom:6px}
.ni-opts{display:flex;flex-wrap:wrap;gap:6px}
.ni-opt{border:1px solid var(--border-primary);background:transparent;color:var(--text-primary);border-radius:8px;padding:5px 10px;cursor:pointer;font:inherit;font-size:0.85rem;text-align:left;max-width:100%}
.ni-opt:hover{background:var(--hover-bg)}
.ni-opt[aria-pressed="true"]{border-color:var(--teradata-orange);box-shadow:inset 0 0 0 1px var(--teradata-orange)}
.ni-opt .ni-detail{display:block;color:var(--text-muted);font-size:0.75rem}
.ni-field{width:100%;max-width:420px;border:1px solid var(--border-primary);background:var(--bg-primary);color:var(--text-primary);border-radius:8px;padding:6px 9px;font:inherit;font-size:0.85rem}
.ni-why{color:var(--text-muted);font-size:0.8rem;margin:4px 0 10px}
.ni-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px}
.ni-btn{border:1px solid var(--border-primary);background:transparent;color:var(--text-primary);border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit;font-size:0.85rem}
.ni-btn:hover{background:var(--hover-bg)}
.ni-btn[disabled]{opacity:.5;cursor:default}
.ni-btn.ni-send{border-color:var(--teradata-orange);color:var(--teradata-orange)}
.ni-btn.ni-quiet{border-color:transparent;color:var(--text-muted)}
.ni-outcome{color:var(--text-muted);font-size:0.82rem;margin-top:4px}
.ni-remember{display:flex;gap:12px;flex-wrap:wrap;color:var(--text-muted);font-size:0.78rem;margin:2px 0 6px}
.ni-remember label{display:flex;align-items:center;gap:4px;cursor:pointer}
.ni-outcome b{color:var(--text-primary);font-weight:600}
`;
    document.head.appendChild(s);
}

const GLYPH = '<svg class="ni-glyph" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 10h8M8 14h5M21 12a8 8 0 01-11.6 7.1L4 20l1.1-4.2A8 8 0 1121 12z"/></svg>';

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function _raisedByMark(req) {
    switch (req.raised_by) {
        case 'code': return '<span class="ni-mark" title="The platform found the ambiguity — the agent did not decide to ask">platform</span>';
        case 'model': return (req.tainted
            ? '<span class="ni-mark ni-taint" title="Raised after reading content the tool-result firewall flagged — not the platform\'s own question">from flagged content</span>'
            : '<span class="ni-mark" title="The agent decided to ask">agent</span>');
        case 'data_source': return '<span class="ni-mark" title="A data source asked">data source</span>';
        case 'child': return '<span class="ni-mark" title="Relayed from an agent this agent asked">relayed</span>';
        default: return '';
    }
}

function _itemHtml(req, it, i) {
    const h = esc(it.header || `Q${i + 1}`);
    let body = '';
    if (req.kind === 'auth' && it.connector) {
        // auth_required (phase 3): the answer is completing the platform's own OAuth popup — never
        // typing anything here. The button opens it; the success message answers the question.
        const c = it.connector;
        body = `<div class="ni-opts"><button type="button" class="ni-opt ni-connect" data-ni-item="${i}" data-value="connected" data-platform="${esc(c.platform)}" aria-pressed="false">Connect ${esc(c.display_name || c.platform)}</button></div>`;
    } else if (req.kind === 'choose' || req.kind === 'confirm') {
        body = '<div class="ni-opts">' + (it.options || []).map((o) =>
            `<button type="button" class="ni-opt" data-ni-item="${i}" data-value="${esc(o.value)}" aria-pressed="false" title="${esc(o.value)}">${esc(o.label)}${o.detail ? `<span class="ni-detail">${esc(o.detail)}</span>` : ''}</button>`
        ).join('') + '</div>';
        if (it.other_allowed) {
            body += `<div style="margin-top:6px"><input class="ni-field" data-ni-other="${i}" placeholder="Or something else…" maxlength="300"></div>`;
        }
    } else {
        const schema = it.schema || {type: 'string'};
        const props = schema.type === 'object' ? (schema.properties || {}) : {value: schema};
        body = Object.entries(props).map(([name, fs]) => {
            const t = fs.type === 'number' || fs.type === 'integer' ? 'number' : (fs.format === 'date' ? 'date' : (fs.format === 'date-time' ? 'datetime-local' : (fs.format === 'email' ? 'email' : (fs.format === 'uri' ? 'url' : 'text'))));
            if (fs.enum) {
                return `<select class="ni-field" data-ni-item="${i}" data-ni-field="${esc(name)}"><option value="">${esc(name)}…</option>${fs.enum.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('')}</select>`;
            }
            if (fs.type === 'boolean') {
                return `<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" data-ni-item="${i}" data-ni-field="${esc(name)}"> ${esc(name)}</label>`;
            }
            return `<input class="ni-field" type="${t}" data-ni-item="${i}" data-ni-field="${esc(name)}" placeholder="${esc(fs.description || name)}" maxlength="500">`;
        }).join('<div style="height:6px"></div>');
    }
    return `<div class="ni-item" data-ni-item-wrap="${i}"><div class="ni-h">${h}</div><div class="ni-q">${esc(it.question)}</div>${body}</div>`;
}

function _headHtml(req) {
    const who = req.profile_tag ? `@${esc(req.profile_tag)}` : 'The agent';
    const chain = req.parent_id ? ` <span class="ni-mark" title="${esc(req.parent_id)}">in a chain</span>` : '';
    const src = req.source ? ` <span class="ni-mark" title="the step that asked">${esc(String(req.source)).slice(0, 48)}</span>` : '';
    return `<div class="ni-head">${GLYPH}<span class="ni-who">${who}</span> needs your input ${_raisedByMark(req)}${src}${chain}</div>`;
}

function _closedLine(req) {
    const by = req.answered_by && req.answered_by !== req.user_uuid ? ` (${esc(req.answered_by)})` : '';
    switch (req.status) {
        case 'accepted': {
            const vals = req.answer && typeof req.answer === 'object' ? Object.values(req.answer).map((v) => Array.isArray(v) ? v.join(', ') : v).join(' · ') : (req.answer == null ? '' : String(req.answer));
            const labels = {};
            (req.items || []).forEach((it) => (it.options || []).forEach((o) => { labels[o.value] = o.label; }));
            const shown = vals.split(' · ').map((v) => labels[v] || v).join(' · ');
            return `<div class="ni-outcome">You answered: <b>${esc(shown)}</b>${by}</div>`;
        }
        case 'declined': return `<div class="ni-outcome">You declined${by}.</div>`;
        case 'cancelled': return `<div class="ni-outcome">Dismissed for now${by}.</div>`;
        case 'expired': return `<div class="ni-outcome">This question expired unanswered.</div>`;
        case 'superseded': return `<div class="ni-outcome">You moved on; the question was not answered.</div>`;
        default: return '';
    }
}

function _cardHtml(req) {
    const open = req.status === 'pending';
    const items = (req.items || []).map((it, i) => _itemHtml(req, it, i)).join('');
    const why = req.why ? `<div class="ni-why">${esc(req.why)}</div>` : '';
    // decision 10 — a single choose/confirm may be remembered: for this conversation, or always for
    // this agent (the owner's standing default); never a typed value, an approval or an auth
    const remember = open && (req.items || []).length === 1 && (req.kind === 'choose' || req.kind === 'confirm')
        ? `<div class="ni-remember"><label><input type="radio" name="ni-rem-${esc(req.id)}" value="" checked> Just this once</label>
           <label><input type="radio" name="ni-rem-${esc(req.id)}" value="session"> Use this for the rest of the session</label>
           <label><input type="radio" name="ni-rem-${esc(req.id)}" value="agent"> Always for this agent</label></div>` : '';
    const actions = open ? `${remember}<div class="ni-actions">
        <button type="button" class="ni-btn ni-send" data-ni-act="accept" disabled>Send</button>
        <button type="button" class="ni-btn" data-ni-act="decline">Decline</button>
        <button type="button" class="ni-btn ni-quiet" data-ni-act="cancel">Not now</button>
    </div>` : _closedLine(req);
    return `${_headHtml(req)}${open ? items : ''}${open ? why : ''}${actions}`;
}

function _collect(card, req) {
    const values = {};
    for (let i = 0; i < (req.items || []).length; i++) {
        const it = req.items[i];
        const h = it.header || `Q${i + 1}`;
        if (req.kind === 'choose' || req.kind === 'confirm') {
            const picked = [...card.querySelectorAll(`.ni-opt[data-ni-item="${i}"][aria-pressed="true"]`)].map((b) => b.dataset.value);
            const other = card.querySelector(`[data-ni-other="${i}"]`);
            if (picked.length) values[h] = it.multi_select ? picked : picked[0];
            else if (other && other.value.trim()) values[h] = other.value.trim();
            else return null;
        } else {
            const fields = [...card.querySelectorAll(`[data-ni-item="${i}"][data-ni-field]`)];
            const obj = {};
            for (const f of fields) {
                const v = f.type === 'checkbox' ? f.checked : f.value;
                if (v === '' || v == null) return null;
                obj[f.dataset.niField] = f.type === 'number' ? Number(v) : v;
            }
            const keys = Object.keys(obj);
            values[h] = keys.length === 1 && keys[0] === 'value' ? obj.value : obj;
        }
    }
    return values;
}

function _displayText(req, decision, values) {
    if (decision === 'decline') return 'No — I\'d rather not.';
    if (decision === 'cancel') return 'Not now.';
    const labels = {};
    (req.items || []).forEach((it) => (it.options || []).forEach((o) => { labels[o.value] = o.label; }));
    return Object.values(values || {}).map((v) => {
        if (Array.isArray(v)) return v.map((x) => labels[x] || x).join(', ');
        if (v && typeof v === 'object') return Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(', ');
        return labels[v] || String(v);
    }).join(' · ');
}

function _refresh(card, req) {
    const send = card.querySelector('[data-ni-act="accept"]');
    if (send) send.disabled = _collect(card, req) === null;
}

function _rememberChoice(card, req) {
    const r = card && card.querySelector(`input[name="ni-rem-${req.id}"]:checked`);
    return r && r.value ? r.value : null;
}

function _submitThroughComposer(req, decision, values, remember) {
    const input = document.getElementById('user-input');
    const form = document.getElementById('chat-form');
    if (!input || !form) return false;
    window.NeedsInput.pendingAnswer = {
        input_request_id: req.id, resume: req.resume || null, decision, values: values || null, session_id: req.session_id,
        remember: decision === 'accept' ? (remember || null) : null,
    };
    input.value = _displayText(req, decision, values);
    form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
    return true;
}

function _close(sessionId, status, answer) {
    const p = _pending.get(sessionId);
    if (!p) return;
    const req = Object.assign({}, p.request, {status, answer: answer === undefined ? p.request.answer : answer});
    p.card.dataset.state = status === 'accepted' ? 'answered' : 'closed';
    p.card.innerHTML = _cardHtml(req);
    _pending.delete(sessionId);
    if (_hasBus()) UderiaBus.emit('uderia:input-answered', {session_id: req.session_id, request_id: req.id, decision: status});
}

async function _connect(card, p, btn) {
    // open the platform's OAuth popup; its success message (the connector routes post
    // `<platform>_oauth_success` to the opener) is the answer
    const platform = btn.dataset.platform;
    const req = p.request;
    try {
        const r = await fetch(`/api/v1/connectors/${encodeURIComponent(platform)}/auth`, {headers: {'Authorization': `Bearer ${localStorage.getItem('tda_auth_token') || ''}`}});
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.auth_url) { if (window.showAppBanner) window.showAppBanner(d.error || 'This connector is not configured', 'error'); return; }
        const popup = window.open(d.auth_url, `${platform}_oauth`, 'width=520,height=640,menubar=no,toolbar=no');
        if (!popup) { if (window.showAppBanner) window.showAppBanner('The sign-in window was blocked — allow pop-ups and try again', 'error'); return; }
        const onMsg = (ev) => {
            if (!ev.data || ev.data.type !== `${platform}_oauth_success`) return;
            window.removeEventListener('message', onMsg);
            btn.setAttribute('aria-pressed', 'true');
            const values = {[(req.items[0] || {}).header || 'Connect']: 'connected'};
            const status = 'accepted';
            if (p.via === 'rest') _submitViaRest(req, 'accept', values, null).then((ok) => { if (ok) _close(p.key, status, values); });
            else if (_submitThroughComposer(req, 'accept', values, null)) _close(req.session_id, status, values);
        };
        window.addEventListener('message', onMsg);
    } catch (_) { /* the card stays open; the person can retry */ }
}

function _onClick(e) {
    const opt = e.target.closest('.ni-opt');
    const act = e.target.closest('[data-ni-act]');
    const card = e.target.closest('.ni-card');
    if (!card || card.dataset.state !== 'pending') return;
    const p = [..._pending.values()].find((x) => x.card === card);
    if (!p) return;
    const req = p.request;
    if (opt && opt.classList.contains('ni-connect')) { _connect(card, p, opt); return; }
    if (opt) {
        const i = Number(opt.dataset.niItem);
        const it = req.items[i] || {};
        if (!it.multi_select) {
            card.querySelectorAll(`.ni-opt[data-ni-item="${i}"]`).forEach((b) => b.setAttribute('aria-pressed', b === opt ? String(b.getAttribute('aria-pressed') !== 'true') : 'false'));
        } else {
            opt.setAttribute('aria-pressed', String(opt.getAttribute('aria-pressed') !== 'true'));
        }
        _refresh(card, req);
        return;
    }
    if (act) {
        const decision = act.dataset.niAct;
        const values = decision === 'accept' ? _collect(card, req) : null;
        if (decision === 'accept' && values === null) return;
        const status = decision === 'accept' ? 'accepted' : (decision === 'decline' ? 'declined' : 'cancelled');
        const remember = decision === 'accept' ? _rememberChoice(card, req) : null;
        if (p.via === 'rest') {
            // the panel and the Inbox answer over REST: the turn runs in the background and the
            // conversation (if open) receives it as a completed task
            act.disabled = true;
            _submitViaRest(req, decision, values, remember).then((ok) => {
                if (ok) _close(p.key, status, values); else act.disabled = false;
            });
            return;
        }
        if (_submitThroughComposer(req, decision, values, remember)) _close(req.session_id, status, values);
    }
}

async function _submitViaRest(req, decision, values, remember) {
    try {
        const h = {'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token') || ''}`};
        let resume = req.resume;
        if (!resume) {
            const g = await fetch(`/api/v1/input-requests/${encodeURIComponent(req.id)}`, {headers: h});
            if (g.ok) resume = ((await g.json()) || {}).resume;
        }
        const r = await fetch(`/api/v1/input-requests/${encodeURIComponent(req.id)}/answer`, {
            method: 'POST', headers: h, body: JSON.stringify({decision, answer: values, resume, remember: remember || undefined}),
        });
        if (!r.ok) {
            const e = await r.json().catch(() => ({}));
            if (window.showAppBanner) window.showAppBanner(e.error || 'The answer could not be used', 'error');
            return false;
        }
        return true;
    } catch (_) { return false; }
}

function _onInput(e) {
    const card = e.target.closest && e.target.closest('.ni-card');
    if (!card || card.dataset.state !== 'pending') return;
    const p = [..._pending.values()].find((x) => x.card === card);
    if (p) _refresh(card, p.request);
}

function _listen() {
    if (_listening) return;
    _listening = true;
    document.addEventListener('click', _onClick);
    document.addEventListener('input', _onInput);
    document.addEventListener('change', _onInput);
}

/**
 * Attach the card under an assistant bubble. `request` is the record as the faces read it
 * (`input_request` on the final answer, the REST task, or the turn record on reload).
 */
export function attach(bubble, request, opts = {}) {
    if (!bubble || !request || !request.id) return null;
    _injectCSS();
    _listen();
    const host = opts.host || bubble.parentElement || bubble;
    const prev = host.querySelectorAll ? [...host.querySelectorAll('.ni-card')].find((c) => c.dataset && c.dataset.niId === request.id) : null;
    if (prev) return prev;
    const card = document.createElement('div');
    card.className = 'ni-card';
    card.dataset.niId = request.id;
    card.dataset.state = request.status === 'pending' ? 'pending' : (request.status === 'accepted' ? 'answered' : 'closed');
    card.innerHTML = _cardHtml(request);
    host.appendChild(card);
    if (request.status === 'pending') {
        // a chat card is keyed by its session (the composer rule reads it); a panel or Inbox
        // card answers over REST and is keyed by the request, so it never captures the composer
        const key = opts.via === 'rest' ? `rest:${request.id}` : request.session_id;
        _pending.set(key, {request, bubble, card, via: opts.via || 'composer', key});
        if (opts.via !== 'rest' && _hasBus()) UderiaBus.emit('uderia:input-required', {session_id: request.session_id, request_id: request.id});
    }
    return card;
}

// ── the split panel: this agent's questions — pending first, then what was asked and answered ──

const PANEL_ID = 'needs-input-split-panel';
let _panelProfileId = null, _panelAll = false, _panelFollowOff = null;

function _injectPanelCSS() {
    if (document.getElementById(CSS_ID + '-panel')) return;
    const s = document.createElement('style');
    s.id = CSS_ID + '-panel';
    s.textContent = `
#${PANEL_ID}.ni-split--open{width:46%;min-width:380px}
.ni-split-body{flex:1;overflow:auto;padding:10px 12px;color:var(--text-primary)}
.ni-split-sub{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px;font-size:0.8rem;color:var(--text-muted)}
.ni-split-sub .ni-count{border:1px solid var(--border-secondary);border-radius:999px;padding:0 8px;line-height:20px}
.ni-split-sub .ni-count.ni-live{border-color:var(--trust-pass,#22c55e);color:var(--text-primary)}
.ni-split-sub label{display:flex;align-items:center;gap:5px;cursor:pointer}
.ni-split-sub .ni-stats{margin-left:auto;color:var(--text-muted)}
.ni-split-h{font-size:0.72rem;letter-spacing:.04em;text-transform:uppercase;color:var(--text-muted);margin:12px 0 6px}
.ni-split-body .ni-card{margin-top:8px}
.ni-split-body .ni-card .ni-when{color:var(--text-muted);font-size:0.75rem;margin-left:auto}
.ni-split-empty{color:var(--text-muted);font-size:0.85rem;padding:12px 0}
`;
    document.head.appendChild(s);
}

async function _panelLoad() {
    const body = document.getElementById('needs-input-split-content');
    if (!body) return;
    const h = {'Authorization': `Bearer ${localStorage.getItem('tda_auth_token') || ''}`};
    const q = `/api/v1/input-requests?status=all&limit=80${(_panelAll || !_panelProfileId) ? '' : `&profile_id=${encodeURIComponent(_panelProfileId)}`}`;
    let data = {items: [], pending: 0};
    try { const r = await fetch(q, {headers: h}); if (r.ok) data = await r.json(); } catch (_) { /* empty */ }
    const items = data.items || [];
    const pendingHere = items.filter((it) => it.status === 'pending');
    const history = items.filter((it) => it.status !== 'pending');
    for (const k of [..._pending.keys()]) if (String(k).startsWith('rest:')) _pending.delete(k);
    // the scoreboard's readings (PROP-011 §4.8): asked · answered · declined · expired · median time to answer
    let stats = null;
    try {
        const sq = `/api/v1/input-requests/stats${(_panelAll || !_panelProfileId) ? '' : `?profile_id=${encodeURIComponent(_panelProfileId)}`}`;
        const sr = await fetch(sq, {headers: h}); if (sr.ok) stats = await sr.json();
    } catch (_) { stats = null; }
    const med = (s) => (s == null ? '—' : s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);
    const statsLine = stats ? `<span class="ni-stats" title="the last ${stats.days} days">asked ${stats.asked} · answered ${stats.accepted} · declined ${stats.declined} · expired ${stats.expired} · median ${med(stats.median_answer_seconds)}</span>` : '';
    body.innerHTML = `<div class="ni-split-sub">
        <span class="ni-count ${data.pending ? 'ni-live' : ''}" title="pending across every agent of yours">${data.pending || 0} waiting for you</span>
        <label><input type="checkbox" id="ni-split-all" ${_panelAll ? 'checked' : ''}> All agents</label>
        ${statsLine}
      </div>
      <div class="ni-split-h">Waiting for you</div><div id="ni-split-pending"></div>
      <div class="ni-split-h">Asked and answered</div><div id="ni-split-history"></div>`;
    const pend = body.querySelector('#ni-split-pending'), hist = body.querySelector('#ni-split-history');
    if (!pendingHere.length) pend.innerHTML = '<div class="ni-split-empty">Nothing is waiting on you here.</div>';
    for (const it of pendingHere) attach(pend, it, {host: pend, via: 'rest'});
    if (!history.length) hist.innerHTML = '<div class="ni-split-empty">Nothing asked yet.</div>';
    for (const it of history) {
        const c = attach(hist, it, {host: hist, via: 'rest'});
        if (c) { const w = document.createElement('span'); w.className = 'ni-when'; w.textContent = (it.answered_at || it.created_at || '').replace('T', ' ').slice(0, 16); const head = c.querySelector('.ni-head'); if (head) head.appendChild(w); }
    }
    const all = body.querySelector('#ni-split-all');
    if (all) all.onchange = () => { _panelAll = all.checked; _panelLoad(); };
}

function _panelClose() {
    const panel = document.getElementById(PANEL_ID);
    if (!panel) return;
    panel.classList.remove('ni-split--open');
    panel.style.display = 'none';
    if (_panelFollowOff) { try { _panelFollowOff(); } catch (_) {} _panelFollowOff = null; }
    try { window.SplitPanelResizer && window.SplitPanelResizer.update(); } catch (_) {}
}

/** Open the questions panel for an agent (the Workspace's "open"; the chat's card header). */
export async function openNeedsInputPanel(spec = {}) {
    _injectCSS(); _injectPanelCSS(); _listen();
    const panel = document.getElementById(PANEL_ID);
    if (!panel) return;
    _panelProfileId = spec.profile_id || _panelProfileId;
    if (!_panelProfileId) {
        try { const m = await import('/static/js/handlers/nextStepHandler.js'); _panelProfileId = m.getEffectiveProfileId ? m.getEffectiveProfileId() : null; } catch (_) {}
    }
    panel.style.display = 'flex';
    panel.offsetHeight;
    panel.classList.add('ni-split--open');
    const close = document.getElementById('needs-input-split-close');
    if (close) close.onclick = _panelClose;
    if (!_panelFollowOff && _hasBus()) {
        _panelFollowOff = UderiaBus.onComponent('needs_input', () => {
            const p = document.getElementById(PANEL_ID);
            if (p && p.classList.contains('ni-split--open')) _panelLoad();
        });
    }
    await _panelLoad();
    try { window.SplitPanelResizer && window.SplitPanelResizer.update(); } catch (_) {}
}

export function closeNeedsInputPanel() { _panelClose(); }

/** The registry entry point: render into a container (an inline component slot). */
export function renderNeedsInput(containerId, payload) {
    const el = typeof containerId === 'string' ? document.getElementById(containerId) : containerId;
    if (!el) return null;
    const req = payload && payload.input_request ? payload.input_request : payload;
    return attach(el, req);
}

/** The composer rule: text typed while a question is pending → a binding, or null (a new prompt). */
export function bindComposerText(sessionId, text) {
    const p = _pending.get(sessionId);
    if (!p) return null;
    const req = p.request;
    const t = String(text || '').trim();
    if (!t) return null;
    if (req.items.length !== 1) return null;                     // several items: answer in the card
    const it = req.items[0];
    const h = it.header || 'Q1';
    if (req.kind === 'choose' || req.kind === 'confirm') {
        const hit = (it.options || []).find((o) => o.value.toLowerCase() === t.toLowerCase() || o.label.toLowerCase() === t.toLowerCase());
        if (hit) return {input_request_id: req.id, resume: req.resume || null, decision: 'accept', values: {[h]: hit.value}, session_id: sessionId};
        if (it.other_allowed) return {input_request_id: req.id, resume: req.resume || null, decision: 'accept', values: {[h]: t}, session_id: sessionId};
        return null;
    }
    return {input_request_id: req.id, resume: req.resume || null, decision: 'accept', values: {[h]: t}, session_id: sessionId};
}

/** The person moved on in the composer: the card says so (the server supersedes the record). */
export function supersede(sessionId) {
    if (_pending.has(sessionId)) _close(sessionId, 'superseded');
}

export function pendingFor(sessionId) {
    const p = _pending.get(sessionId);
    return p ? p.request : null;
}

/** A pending card answered elsewhere (the Inbox, REST): close it with the recorded outcome. */
export function settle(sessionId, status, answer) {
    _close(sessionId, status, answer);
}

if (typeof window !== 'undefined') {
    window.NeedsInput = Object.assign(window.NeedsInput || {}, {
        attach, renderNeedsInput, bindComposerText, supersede, pendingFor, settle, openNeedsInputPanel, closeNeedsInputPanel, pendingAnswer: null,
    });
}
