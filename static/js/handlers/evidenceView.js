/**
 * Evidence — one answer's signed parts (PROP-009 §5.1). Part of the removable Ontology
 * Suite: the answer capsule leaves a host (`[data-ac-evidence]`) and calls
 * `window.EvidenceView.mount(host, sid, turn)` only when this script is present.
 *
 * Reads GET /api/v1/evidence/turn/<sid>/<turn> and renders:
 *   collapsed  — the Evidence row: "12 parts · all signed when answered · 1 changed since",
 *                the changed or unsigned parts named on the row;
 *   opened     — the parts as a short tree (Agent · Deployment · What the agent is made of ·
 *                Data read · Acts performed), each with the fixed-order pair
 *                "when answered · now" (a missing side reads "—") and its status word;
 *   detail     — signer, time, key fingerprint and status, hashes — one more click down.
 *
 * Rules (TRUST_STATUS_COMMUNICATION): state words, never wire tokens; absence is said
 * ("parts not recorded — …"); colour is attention (trust-chip tokens), never the only
 * carrier; theme tokens; the platform tooltip (data-tip), never a native title.
 */
(function () {
    'use strict';

    const CACHE = new Map(); // "sid:turn" -> promise of the evidence view

    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    // The state words — one vocabulary for both sides of the pair. Never a wire token.
    const STATE_WORD = {
        trusted: 'signed and intact',
        changed: 'changed after signing',
        unsigned: 'unsigned',
        deployment_unattested: 'not attested here',
        not_found: 'not found',
        performed: 'performed',
    };
    const STATE_CLS = {
        trusted: 'tc-pass', changed: 'tc-warn', unsigned: '', deployment_unattested: 'tc-warn',
        not_found: 'tc-warn', performed: '',
    };
    const STATUS_WORD = {
        unchanged: '',          // the norm is not marked — only the exception is
        changed_since: 'changed since',
        not_found: 'not found now',
        no_longer_used: 'no longer used by the agent',
        recorded: '',
        unknown: '',
    };
    const STATUS_CLS = { changed_since: 'tc-warn', not_found: 'tc-warn' };

    function stateWord(s) {
        if (s == null || s === '') return '—';
        return STATE_WORD[s] || String(s).replace(/_/g, ' ');
    }

    function fetchEvidence(sid, turn, fresh) {
        const key = `${sid}:${turn}`;
        if (fresh || !CACHE.has(key)) {
            const token = localStorage.getItem('tda_auth_token');
            CACHE.set(key, fetch(`/api/v1/evidence/turn/${encodeURIComponent(sid)}/${parseInt(turn, 10)}`,
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} })
                .then((r) => (r.ok ? r.json() : null)).catch(() => null));
        }
        return CACHE.get(key);
    }

    function chip(text, cls, tip) {
        const t = tip ? ` u-tip" data-tip="${esc(tip)}` : '';
        return `<span class="trust-chip ${cls || ''}${t}">${esc(text)}</span>`;
    }

    function shortHash(h) { return h ? `${String(h).slice(0, 12)}…` : '—'; }

    function when(ts) {
        if (!ts) return '';
        const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
        return isNaN(d) ? '' : d.toLocaleString();
    }

    function detailHtml(e) {
        const t = e.then || {};
        const n = e.now || {};
        const sg = t.signer || null;
        const rows = [];
        if (sg) {
            rows.push(['Signed by', `${esc(sg.signer_name || (sg.signer_uuid ? String(sg.signer_uuid).slice(0, 8) : '—'))}${sg.signed_at ? ' · ' + esc(when(sg.signed_at)) : ''}`]);
            if (sg.key_fingerprint) {
                const ks = sg.key_status === 'retired' ? ' · retired key'
                    : (sg.key_status === 'carried' ? ' · another signer’s key'
                    : (sg.key_status === 'active' ? ' · this deployment’s key' : ''));
                rows.push(['Key', `<code>${esc(shortHash(sg.key_fingerprint))}</code>${esc(ks)}`]);
            }
        } else if (t.state && t.state !== 'trusted' && t.state !== 'performed') {
            rows.push(['Signed by', 'nobody — this part was not signed when the answer was given']);
        }
        if (t.live_hash || n.live_hash) {
            rows.push(['Content', `when answered <code>${esc(shortHash(t.live_hash))}</code> · now <code>${esc(shortHash(n.live_hash))}</code>`]);
        }
        if (t.unresolved) rows.push(['Note', 'its content could not be read when the answer was given']);
        if (e.summary) {
            const s = e.summary;
            const bits = [s.provider && s.model ? `${s.provider} · ${s.model}` : (s.model || ''),
                s.tools != null ? `${s.tools} tool${s.tools === 1 ? '' : 's'}` : '',
                s.prompts ? `${s.prompts} prompt${s.prompts === 1 ? '' : 's'}` : '',
                s.connectors ? `${s.connectors} connector${s.connectors === 1 ? '' : 's'}` : '']
                .filter(Boolean).join(' · ');
            if (bits) rows.push(['Surface', esc(bits)]);
        }
        if (t.row_count != null) rows.push(['Returned', `${esc(t.row_count)} row${t.row_count === 1 ? '' : 's'}`]);
        if (t.rows_affected != null) rows.push(['Wrote', `${esc(t.rows_affected)} row${t.rows_affected === 1 ? '' : 's'}${t.posture ? ' · ' + esc(t.posture) : ''}`]);
        if (n.drift && n.drift.length) rows.push(['Drift now', esc(n.drift.slice(0, 3).join('; '))]);
        if (!rows.length) return '';
        return `<div class="ev-detail">${rows.map(([k, v]) =>
            `<div class="ev-kv"><span class="ev-k">${esc(k)}</span><span class="ev-v">${v}</span></div>`).join('')}</div>`;
    }

    // A disclosure row that offers an act. The act may NOT sit inside the <summary>: a
    // summary is one toggle, and a button nested in it is mis-announced or unreachable for
    // keyboard and screen-reader users (Chrome flags it as an error-level issue). So the act
    // is a sibling of the <details>, pinned to the row's top-right over space the summary
    // reserves — still on the collapsed row, because it is the act the row says is owed.
    function disclosure(open, head, act, body) {
        const d = `${open}<summary>${head}</summary>${body}</details>`;
        return act ? `<div class="ev-disc">${d}${act}</div>` : d;
    }

    function itemHtml(e) {
        const t = e.then || {};
        const n = e.now || {};
        const pair = `<span class="ev-pair">${chip(stateWord(t.state), STATE_CLS[t.state] || '', 'When the answer was given')}<span class="ev-sep">·</span>${chip(stateWord(n.state), STATE_CLS[n.state] || '', 'Now')}</span>`;
        const sw = STATUS_WORD[e.status];
        const status = sw ? chip(sw, STATUS_CLS[e.status] || '') : '';
        const label = e.role === 'deployment' ? 'Model, tools and connectors' : (e.label || e.artifact_id);
        const det = detailHtml(e);
        const kind = e.role === 'deployment' ? '' : `<span class="ev-kind">${esc(e.kind_label || e.kind)}</span>`;
        const openAct = (window.TrustBadge && window.TrustBadge.openArtifactTrust
            && e.role === 'ingredient' && (e.status === 'changed_since' || t.state !== 'trusted'))
            ? `<button type="button" class="ev-open" data-kind="${esc(e.kind)}" data-id="${esc(e.artifact_id)}" data-name="${esc(e.label || '')}">Open trust record</button>` : '';
        const head = `<span class="ev-name">${kind}<span class="ev-label">${esc(label)}</span></span>${pair}${status}`;
        return det
            ? disclosure('<details class="ev-item">', head, openAct, det)
            : `<div class="ev-item ev-flat">${head}${openAct}</div>`;
    }

    function treeHtml(v) {
        const groups = (v.groups || []).map((g) => `<div class="ev-group">
            <div class="ev-glabel">${esc(g.label)}</div>
            ${(g.items || []).map(itemHtml).join('')}</div>`).join('');
        const legend = `<div class="ev-legend">Each part reads <b>when the answer was given</b> · <b>now</b>. A part is recorded as it was at the start of the turn.</div>`;
        const extra = v.data_and_acts_note ? `<div class="ev-note">${esc(v.data_and_acts_note)}</div>` : '';
        return legend + groups + extra;
    }

    function chainLine(v) {
        const c = v.chain || {};
        if (c.valid === true) {
            const ks = c.key_status === 'retired' ? ' · signed with a retired key' : '';
            return chip(`record verified — ${c.verified_steps || 0} step(s) re-derived, Ed25519 chain intact${ks}`, 'tc-pass',
                `The answer's sealed chain and the content each step binds were checked on the server.\nKey ${shortHash(c.key_fingerprint)}`);
        }
        if (c.valid === false) {
            return chip(`record does NOT verify — ${(c.errors && c.errors[0]) || 'integrity check failed'}`, 'tc-danger');
        }
        return `<span class="text-[11px]" style="color: var(--text-muted);">${esc(c.note || 'no sealed record to check')}</span>`;
    }

    function rowHtml(v) {
        if (!v) return '<span class="text-[11px]" style="color: var(--text-muted);">Evidence unavailable.</span>';
        const s = v.summary || {};
        if (!v.recorded) {
            return `<span class="trust-chip tc-na">${esc(s.words || 'parts not recorded')}</span>`;
        }
        const cls = s.attention === 'pass' ? 'tc-pass' : (s.attention === 'warn' ? 'tc-warn' : '');
        const named = [];
        if ((s.changed_since || []).length) named.push(`changed since: ${s.changed_since.slice(0, 3).join(', ')}`);
        if ((s.not_found_now || []).length) named.push(`not found now: ${s.not_found_now.slice(0, 3).join(', ')}`);
        if ((s.not_signed_when_answered || []).length) named.push(`not signed when answered: ${s.not_signed_when_answered.slice(0, 3).join(', ')}${s.not_signed_when_answered.length > 3 ? ` +${s.not_signed_when_answered.length - 3}` : ''}`);
        const sess = v.session && v.session.available === false
            ? `<div class="ev-note">${esc(v.session.note)}</div>` : '';
        return `<details class="ev-root">
            <summary>${chip(s.words || '', cls)}${named.length ? `<span class="ev-named">${esc(named.join(' · '))}</span>` : ''}</summary>
            <div class="ev-body">${sess}<div class="ev-chain">${chainLine(v)}</div>${treeHtml(v)}
                ${window.EvidenceVerify ? `<div class="ev-turn-check"><button type="button" class="ev-act ev-turn-verify">Verify in this browser</button><span class="ev-turn-out ev-note" aria-live="polite"></span></div>` : ''}</div>
        </details>`;
    }

    function injectCSS() {
        if (document.getElementById('evidence-view-css')) return;
        const el = document.createElement('style');
        el.id = 'evidence-view-css';
        el.textContent = `
        .ev-root > summary{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;align-items:center;gap:0.4rem;}
        .ev-root > summary::-webkit-details-marker{display:none;}
        .ev-root > summary:focus-visible, .ev-item > summary:focus-visible{outline:2px solid var(--teradata-orange);outline-offset:2px;border-radius:4px;}
        .ev-named{font-size:10px;color:var(--text-muted);}
        .ev-body{margin-top:0.4rem;padding-top:0.4rem;border-top:1px dashed var(--border-secondary);}
        .ev-chain{margin-bottom:0.4rem;}
        .ev-legend{font-size:10px;color:var(--text-muted);margin-bottom:0.3rem;}
        .ev-group{margin-top:0.35rem;}
        .ev-glabel{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);margin-bottom:0.15rem;}
        .ev-item{padding:0.15rem 0;}
        .ev-item > summary{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;align-items:center;gap:0.35rem;}
        .ev-item > summary::-webkit-details-marker{display:none;}
        .ev-flat{display:flex;flex-wrap:wrap;align-items:center;gap:0.35rem;}
        .ev-name{display:inline-flex;align-items:baseline;gap:0.3rem;min-width:9rem;}
        .ev-kind{font-size:10px;color:var(--text-muted);}
        .ev-label{font-size:11px;color:var(--text-primary);}
        .ev-pair{display:inline-flex;align-items:center;gap:0.2rem;}
        .ev-sep{font-size:10px;color:var(--text-muted);}
        .ev-open{font-size:10px;color:var(--text-muted);background:transparent;border:1px solid var(--border-secondary);border-radius:4px;padding:0 0.4rem;cursor:pointer;}
        .ev-act{font-size:10px;color:var(--text-muted);background:transparent;border:1px solid var(--border-secondary);border-radius:4px;padding:0 0.4rem;cursor:pointer;}
        .ev-open:hover,.ev-act:hover{color:var(--text-primary);}
        .ev-act:focus-visible,.ev-open:focus-visible{outline:2px solid var(--teradata-orange);outline-offset:1px;}
        .ev-detail{margin:0.2rem 0 0.3rem 0.6rem;padding-left:0.5rem;border-left:1px solid var(--border-secondary);}
        .ev-kv{display:flex;gap:0.5rem;font-size:10px;line-height:1.5;}
        .ev-k{color:var(--text-muted);width:64px;flex-shrink:0;}
        .ev-v{color:var(--text-primary);word-break:break-all;}
        .ev-v code{font-size:10px;}
        .ev-note{font-size:10px;color:var(--text-muted);margin:0.2rem 0;}
        .ev-ehead,.ev-etools{display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem;margin-bottom:0.6rem;}
        .ev-verify-all,.ev-pill,.ev-kindsel,.ev-search{font-size:11px;color:var(--text-primary);background:transparent;border:1px solid var(--border-secondary);border-radius:6px;padding:0.2rem 0.55rem;}
        .ev-pill{cursor:pointer;color:var(--text-muted);}
        .ev-pill-on{color:var(--text-primary);border-color:var(--teradata-orange);}
        .ev-verify-all{cursor:pointer;}
        .ev-turn-check{display:flex;flex-wrap:wrap;align-items:center;gap:0.4rem;margin-top:0.4rem;}
        .ev-verify-all:focus-visible,.ev-pill:focus-visible,.ev-kindsel:focus-visible,.ev-search:focus-visible{outline:2px solid var(--teradata-orange);outline-offset:1px;}
        .ev-kindsel option{background:var(--card-bg);color:var(--text-primary);}
        .ev-etools .ev-kindsel,.ev-etools .ev-search{width:auto !important;display:inline-block !important;max-width:15rem;flex:0 0 auto;}
        .ev-etools .cs-wrap{width:12rem !important;flex:0 0 auto;}
        .ev-elist{display:flex;flex-direction:column;gap:2px;}
        .ev-erow{border:1px solid var(--border-secondary);border-radius:6px;padding:0.35rem 0.6rem;background:var(--card-bg);}
        .ev-erow > summary{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem;}
        .ev-erow > summary::-webkit-details-marker{display:none;}
        .ev-erow > summary:focus-visible{outline:2px solid var(--teradata-orange);outline-offset:2px;border-radius:4px;}
        .ev-erow .ev-name{min-width:14rem;}
        .ev-disc{position:relative;}
        .ev-disc > details > summary{padding-right:8rem;}
        .ev-disc > .ev-open{position:absolute;top:0.15rem;right:0;}
        .ev-disc > .ev-erow + .ev-open{top:calc(0.35rem + 1px);right:calc(0.6rem + 1px);}
        .ev-who,.ev-use{font-size:10px;color:var(--text-muted);}
        .ev-eusage{margin-top:0.35rem;}
        .ev-uturn{display:flex;flex-wrap:wrap;align-items:center;gap:0.4rem;padding:0.15rem 0;border-top:1px dashed var(--border-secondary);}
        .ev-turnhost{flex-basis:100%;}
        `;
        document.head.appendChild(el);
    }

    // ── portable bundles (PROP-009 §4.4) ──────────────────────────────────────────────
    // One export (POST /v1/evidence/export) serves every caller; the browser check runs the
    // same rules as scripts/verify_evidence.py (static/js/evidenceVerify.js). The bundle is
    // kept as TEXT end to end: re-serializing it would re-format numbers and break the
    // re-derived hashes, so anything added on this side is appended to the text.
    async function exportBundleText(body) {
        const token = localStorage.getItem('tda_auth_token');
        try {
            const r = await fetch('/api/v1/evidence/export', { method: 'POST',
                headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
                body: JSON.stringify(body) });
            return r.ok ? await r.text() : null;
        } catch (_) { return null; }
    }

    function appendTopLevel(text, key, value) {
        return String(text).replace(/\}\s*$/, `,${JSON.stringify(key)}:${JSON.stringify(value)}}`);
    }

    // meta (optional): {what, sessionId, turnId} — an answer's bundle carries its turn into Files
    function downloadText(text, filename, meta) {
        const blob = new Blob([text], { type: 'application/json' });
        window.FilesKeep.deliver(blob, filename, { what: 'evidence bundle', ...(meta || {}) });
    }

    function stamp() { return new Date().toISOString().slice(0, 10).replace(/-/g, ''); }

    // The verdict of a browser check, in words. ``res`` is EvidenceVerify.verifyBundle's result.
    function verifyWords(res) {
        if (!res) return chip('could not be checked in this browser', 'tc-na');
        const rows = (res.attestations || []).concat(res.turns || [], res.audit_pack ? [res.audit_pack] : [],
            res.ledger ? [res.ledger] : []);
        const withContent = (res.attestations || []).filter((a) => a.ok && /content hashes/.test((a.notes || []).join(' '))).length;
        if (res.failed) {
            const first = rows.find((r) => r.ok === false);
            return chip(`${res.failed} of ${res.checked} did NOT verify${first ? ` — ${(first.notes || []).slice(-1)[0]}` : ''}`, 'tc-danger');
        }
        return chip(`${res.checked} of ${res.checked} verify — checked in this browser (Ed25519)`
            + (withContent ? ` · signed content re-hashed for ${withContent}` : ''), 'tc-pass');
    }

    async function verifyInBrowser(text) {
        if (!text || !window.EvidenceVerify) return null;
        try { return await window.EvidenceVerify.verifyBundle(text); } catch (_) { return null; }
    }

    async function mount(host, sid, turn, opts) {
        if (!host) return;
        injectCSS();
        host.innerHTML = '<span class="text-[11px]" style="color: var(--text-muted);">Loading evidence…</span>';
        const v = await fetchEvidence(sid, turn, opts && opts.fresh);
        host.innerHTML = rowHtml(v);
        host.querySelectorAll('.ev-open').forEach((b) => b.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            try { window.TrustBadge.openArtifactTrust(b.dataset.kind, b.dataset.id, b.dataset.name); } catch (_) { /* best effort */ }
        }));
        const vb = host.querySelector('.ev-turn-verify');
        if (vb) vb.addEventListener('click', async (ev) => {
            ev.preventDefault();
            const out = host.querySelector('.ev-turn-out');
            if (out) out.textContent = 'Checking…';
            const text = await exportBundleText({ turns: [{ session_id: sid, turn: parseInt(turn, 10) }] });
            if (out) out.innerHTML = verifyWords(await verifyInBrowser(text));
        });
        return v;
    }

    // ── the estate: every current signature (PROP-009 §5.3) ──────────────────────────────
    // One fetch, then filtering and search run on the loaded set. Worst first. The summary
    // describes the whole scope; a filter narrows the list, never the summary.
    const ESTATE_FILTERS = [
        ['all', 'All'], ['changed', 'Changed since signed'], ['retired_key', 'Retired key'],
        ['not_signed', 'Recorded, not signed'],
    ];
    const ESTATE = { data: null, filter: 'all', kind: '', q: '' };

    // The evidence ledger's reading (PROP-009 §4.5), verified by this server now.
    function fetchLedger() {
        const token = localStorage.getItem('tda_auth_token');
        return fetch('/api/v1/evidence/ledger', { headers: token ? { 'Authorization': `Bearer ${token}` } : {} })
            .then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }

    // The ledger's limit in words, following WHO holds the signing key (PROP-009 §4.6) and
    // whether checkpoints are published outside this host — never a stronger claim than holds.
    function ledgerTip(l) {
        const head = 'Every attestation event and every answer\u2019s record is entered in a hash-chained ledger: '
            + 'editing, inserting or removing one afterwards breaks the chain. Checked by this server now. ';
        const c = (l && l.custody) || {};
        const custody = c.key_on_host === false
            ? 'The signing key is held outside this host: the server can ask for signatures but cannot read the key, so it cannot quietly re-sign history. '
            : 'The signing key is held on this host, so its administrator could rebuild the whole chain; every export carries a signed checkpoint that later proves the ledger still contains it. ';
        const a = (l && l.anchoring) || {};
        const anchor = a.enabled
            ? `Checkpoints are published outside this host (${a.sink}), so a rewrite would conflict with what was published.`
            : 'Checkpoints are not published outside this host (anchoring is off).';
        return head + custody + anchor;
    }

    function ledgerChip(l) {
        if (!l || !l.words) return '';
        const w = l.words;
        const cls = w.attention === 'pass' ? 'tc-pass' : (w.attention === 'danger' ? 'tc-danger'
            : (w.attention === 'warn' ? 'tc-warn' : 'tc-na'));
        return `<span class="ev-ledger u-tip" data-tip="${esc(ledgerTip(l))}">${chip(w.words, cls)}</span>`;
    }

    function fetchEstate(fresh) {
        if (fresh || !ESTATE.promise) {
            const token = localStorage.getItem('tda_auth_token');
            ESTATE.promise = fetch('/api/v1/evidence', { headers: token ? { 'Authorization': `Bearer ${token}` } : {} })
                .then((r) => (r.ok ? r.json() : null)).catch(() => null);
        }
        return ESTATE.promise;
    }

    function estateFilter(rows, filter, kind, q) {
        const ql = (q || '').toLowerCase();
        return (rows || []).filter((r) => (filter === 'all' || r.worst === filter || (r.flags || []).includes(filter))
            && (!kind || r.kind === kind)
            && (!ql || String(r.label || '').toLowerCase().includes(ql) || String(r.artifact_id).toLowerCase().includes(ql)));
    }

    function usageWords(r) {
        if (typeof r.answers !== 'number') return '';
        if (!r.answers) return 'not used by a recorded answer yet';
        const s = `used by ${r.answers} answer${r.answers === 1 ? '' : 's'}`;
        if (typeof r.since_current !== 'number') return s;
        return r.since_current === r.answers ? `${s} · all with this version`
            : `${s} · ${r.since_current} with this version`;
    }

    function estateRowHtml(r) {
        const st = r.state_now;
        const keyW = r.key_status === 'retired' ? ' · retired key' : (r.key_status === 'carried' ? ' · another signer’s key' : '');
        const when_ = r.signed_at ? when(r.signed_at) : '';
        const who = r.signed ? `signed by ${r.signer_name || (r.signer_uuid ? String(r.signer_uuid).slice(0, 8) : '—')}${when_ ? ' · ' + when_ : ''}${keyW}`
            : 'recorded without a signature';
        const owed = (st === 'changed' || !r.signed)
            ? `<button type="button" class="ev-open" data-kind="${esc(r.kind)}" data-id="${esc(r.artifact_id)}" data-name="${esc(r.label || '')}">Open trust record</button>` : '';
        return disclosure(`<details class="ev-erow" data-kind="${esc(r.kind)}" data-id="${esc(r.artifact_id)}">`,
            `<span class="ev-name"><span class="ev-kind">${esc(r.kind_label || r.kind)}</span><span class="ev-label">${esc(r.label || r.artifact_id)}</span></span>
                ${chip(st === 'changed' ? 'changed since signed' : stateWord(st), STATE_CLS[st] || '', 'Now')}
                <span class="ev-who">${esc(who)}</span>
                <span class="ev-use">${esc(usageWords(r))}</span>`,
            owed, '<div class="ev-eusage"></div>');
    }

    function footWords(shown, total) {
        return `${shown} of ${total} shown · worst first. Each row reads the signature against the artefact as it is now.`;
    }

    function estateHtml() {
        const d = ESTATE.data;
        if (!d) return '<div class="text-sm" style="color: var(--text-muted);">Evidence unavailable.</div>';
        const s = d.summary || {};
        const kinds = [...new Set((d.rows || []).map((r) => r.kind))].sort();
        const rows = estateFilter(d.rows, ESTATE.filter, ESTATE.kind, ESTATE.q);
        const cls = s.attention === 'pass' ? 'tc-pass' : (s.attention === 'warn' ? 'tc-warn' : 'tc-na');
        return `<div class="ev-estate">
            <div class="ev-ehead">
                ${chip(s.words || 'no signatures', cls)}
                ${ledgerChip(ESTATE.ledger)}
                <button type="button" class="ev-verify-all">Verify all shown</button>
                <button type="button" class="ev-verify-all ev-export-shown">Export shown</button>
                <button type="button" class="ev-verify-all ev-export-audit">Export for audit</button>
                <span class="ev-verify-out" aria-live="polite"></span>
            </div>
            <div class="ev-etools">
                <div class="ev-pills" role="group" aria-label="Filter signatures">${ESTATE_FILTERS.map(([k, l]) =>
                    `<button type="button" class="ev-pill${ESTATE.filter === k ? ' ev-pill-on' : ''}" data-filter="${k}" aria-pressed="${ESTATE.filter === k}">${esc(l)}</button>`).join('')}</div>
                <select class="ev-kindsel" aria-label="Kind"><option value="">All kinds</option>${kinds.map((k) =>
                    `<option value="${esc(k)}"${ESTATE.kind === k ? ' selected' : ''}>${esc(k.replace(/_/g, ' '))}</option>`).join('')}</select>
                <input class="ev-search" type="search" placeholder="Search by name" aria-label="Search signatures" value="${esc(ESTATE.q)}">
            </div>
            <div class="ev-elist">${rows.length ? rows.map(estateRowHtml).join('')
                : '<div class="ev-note">No signature matches this filter.</div>'}</div>
            <div class="ev-note ev-efoot">${esc(footWords(rows.length, (d.rows || []).length))}</div>
        </div>`;
    }

    async function loadUsage(details) {
        const host = details.querySelector('.ev-eusage');
        if (!host || host.dataset.loaded) return;
        host.dataset.loaded = '1';
        host.innerHTML = '<div class="ev-note">Loading the answers that used it…</div>';
        try {
            const token = localStorage.getItem('tda_auth_token');
            const r = await fetch(`/api/v1/evidence/artifact/${encodeURIComponent(details.dataset.kind)}/${encodeURIComponent(details.dataset.id)}/usage?limit=20`,
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} });
            const u = r.ok ? await r.json() : null;
            if (!u || !u.turns || !u.turns.length) {
                host.innerHTML = '<div class="ev-note">No recorded answer used it yet. Answers recorded before evidence recording cannot be counted.</div>';
                return;
            }
            host.innerHTML = u.turns.map((t) => `<div class="ev-uturn">
                <span class="ev-label">${esc(t.profile_tag ? '@' + t.profile_tag : 'answer')}</span>
                <span class="ev-who">${esc(t.answered_at ? when(t.answered_at) : '')} · turn ${esc(t.turn)} · ${t.same_as_current ? 'this version' : 'an earlier version'}</span>
                <button type="button" class="ev-open ev-show-turn" data-sid="${esc(t.session_id)}" data-turn="${esc(t.turn)}">Show its evidence</button>
                <div class="ev-turnhost"></div></div>`).join('');
        } catch (_) {
            host.innerHTML = '<div class="ev-note">The answers that used it could not be read.</div>';
        }
    }

    async function verifyShown(root) {
        const out = root.querySelector('.ev-verify-out');
        const rows = estateFilter(ESTATE.data && ESTATE.data.rows, ESTATE.filter, ESTATE.kind, ESTATE.q).filter((r) => r.signed);
        if (!rows.length) { if (out) out.textContent = 'Nothing signed to verify.'; return; }
        if (out) out.textContent = `Checking ${rows.length} signature(s) in this browser…`;
        const text = await exportBundleText({ artifacts: rows.map((r) => ({ kind: r.kind, artifact_id: r.artifact_id })) });
        if (out) out.innerHTML = verifyWords(await verifyInBrowser(text));
    }

    async function exportShown(root, audit) {
        const out = root.querySelector('.ev-verify-out');
        const rows = estateFilter(ESTATE.data && ESTATE.data.rows, ESTATE.filter, ESTATE.kind, ESTATE.q).filter((r) => r.signed);
        if (!audit && !rows.length) { if (out) out.textContent = 'Nothing signed to export.'; return; }
        if (out) out.textContent = audit ? 'Building the audit export…' : `Exporting ${rows.length} signature(s)…`;
        const text = await exportBundleText(audit ? { preset: 'audit' }
            : { artifacts: rows.map((r) => ({ kind: r.kind, artifact_id: r.artifact_id })) });
        if (!text) { if (out) out.innerHTML = chip('the export failed', 'tc-danger'); return; }
        downloadText(text, `uderia_evidence_${audit ? 'audit' : 'signatures'}_${stamp()}.json`);
        if (out) out.textContent = 'Exported. Verify it offline with scripts/verify_evidence.py or the evidence verifier page.';
    }

    function wireEstate(root) {
        root.querySelectorAll('.ev-pill').forEach((b) => b.addEventListener('click', () => {
            ESTATE.filter = b.dataset.filter; renderEstate(root);
        }));
        const ks = root.querySelector('.ev-kindsel');
        if (ks) ks.addEventListener('change', () => { ESTATE.kind = ks.value; renderEstate(root); });
        const se = root.querySelector('.ev-search');
        if (se) se.addEventListener('input', () => {
            ESTATE.q = se.value;
            const list = root.querySelector('.ev-elist');
            const rows = estateFilter(ESTATE.data.rows, ESTATE.filter, ESTATE.kind, ESTATE.q);
            if (list) { list.innerHTML = rows.length ? rows.map(estateRowHtml).join('') : '<div class="ev-note">No signature matches this filter.</div>'; wireRows(root); }
            const foot = root.querySelector('.ev-efoot');
            if (foot) foot.textContent = footWords(rows.length, (ESTATE.data.rows || []).length);
        });
        const va = root.querySelector('.ev-verify-all');
        if (va) va.addEventListener('click', () => verifyShown(root));
        const xs = root.querySelector('.ev-export-shown');
        if (xs) xs.addEventListener('click', () => exportShown(root, false));
        const xa = root.querySelector('.ev-export-audit');
        if (xa) xa.addEventListener('click', () => exportShown(root, true));
        wireRows(root);
    }

    function wireRows(root) {
        root.querySelectorAll('.ev-erow').forEach((d) => {
            if (d.dataset.wired) return;
            d.dataset.wired = '1';
            d.addEventListener('toggle', () => { if (d.open) loadUsage(d).then(() => wireRows(root)); });
        });
        root.querySelectorAll('.ev-open').forEach((b) => {
            if (b.dataset.wired) return;
            b.dataset.wired = '1';
            b.addEventListener('click', async (ev) => {
                ev.preventDefault(); ev.stopPropagation();
                if (b.classList.contains('ev-show-turn')) {
                    const host = b.parentElement.querySelector('.ev-turnhost');
                    if (host) await mount(host, b.dataset.sid, b.dataset.turn);
                    return;
                }
                try {
                    const TB = window.TrustBadge;
                    if (!TB) return;
                    if (b.dataset.kind === 'agent' || b.dataset.kind === 'agent_deployment') TB.openDetail(b.dataset.id, (b.dataset.name || '').replace(/^@/, ''));
                    else TB.openArtifactTrust(b.dataset.kind, b.dataset.id, b.dataset.name);
                } catch (_) { /* best effort */ }
            });
        });
    }

    function renderEstate(root) {
        root.innerHTML = estateHtml();
        wireEstate(root);
    }

    /** Mount the Evidence tab. `opts.filter` pre-filters (the SIGNED / PROVEN stations route
     * here with one); `opts.fresh` re-reads ("now" moves). */
    async function mountEstate(root, opts) {
        if (!root) return;
        injectCSS();
        const o = opts || {};
        if (o.filter) ESTATE.filter = o.filter;
        if (o.kind !== undefined) ESTATE.kind = o.kind;
        // "Now" moves: a list older than a minute is re-read when the tab is shown again.
        if (!ESTATE.data || o.fresh || (Date.now() - (ESTATE.at || 0)) > 60000) {
            root.innerHTML = '<div class="text-sm" style="color: var(--text-muted);">Reading every signature against its artefact as it is now…</div>';
            const [d, l] = await Promise.all([fetchEstate(true), fetchLedger()]);
            ESTATE.data = d;
            ESTATE.ledger = l;
            ESTATE.at = Date.now();
        }
        renderEstate(root);
    }

    window.EvidenceView = { mount, mountEstate, fetchEvidence, rowHtml, itemHtml, stateWord,
        estateHtml, estateFilter, estateRowHtml, usageWords, _estate: ESTATE,
        exportBundleText, appendTopLevel, downloadText, verifyWords, verifyInBrowser, ledgerChip, ledgerTip,
        STATE_WORD, STATUS_WORD };
})();
