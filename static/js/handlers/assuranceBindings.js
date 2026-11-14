/**
 * PROP-003 · The BINDING STUDIO — which module assesses each property, in which lane.
 *
 * Everything else in Config → Trust configures how loudly the assurance machinery speaks.
 * This configures WHAT the machinery is: one row per property, one cell per lane, each
 * cell naming the module that runs there. Rebinding a row moves the gate's input and the
 * scorer's number together, because both lanes resolve the same binding.
 *
 * THREE RULES THIS FILE EXISTS TO KEEP (each has a gate assertion):
 *
 *   1. Nothing is auto-applied. A binding change is an explicit, confirmed, audited act,
 *      so the set of assessments running on a deployment is exactly the set an operator
 *      assigned. An install that silently changed what a gate sees would be a protection
 *      change in either direction that nobody decided.
 *   2. Absence is rendered, never hidden. An unbound lane says `nothing bound`; a lane a
 *      property does not have says WHY, in the property's own words. A blank cell would
 *      let "not assessed" read as "assessed clean" — the single worst failure mode on
 *      this surface.
 *   3. A refusal is quoted, not paraphrased. `conformance` already writes its reasons for
 *      a human ("it would run and report clean without ever seeing its input"); a picker
 *      that lists only the valid options teaches nothing.
 *
 * The IN_FLIGHT and IN_FLIGHT_TRIGGERED lanes render as ONE column, exactly as the
 * Assurance Guide's `lanesRow` collapses them: they are alternatives, never both, so a
 * third column would read as a gap that is not one. Which of the two is in force is said
 * in the cell's own sub-label (`decides` vs `escalates`).
 */
(function () {
    'use strict';

    const HOST_ID = 'assurance-bindings-host';
    /** The two RENDERED lanes. The wire carries three; the third is an alternative form
     *  of the first (see the header), so it is resolved into this column, never beside it. */
    const COLUMNS = [
        { key: 'IN_FLIGHT', label: 'During the turn',
          tip: 'Assessed while the turn runs, so the finding can reach a gate.' },
        { key: 'POST_HOC', label: 'After the turn',
          tip: 'Assessed once the answer has shipped. Grades; never acts.' },
    ];

    let _model = null;      // the last served payload
    let _busy = false;
    let _refreshTimer = null;

    // ── data ─────────────────────────────────────────────────────────
    function _token() { return localStorage.getItem('tda_auth_token'); }

    async function _api(path, opts) {
        const r = await fetch('/api/v1' + path, Object.assign({
            headers: {
                'Authorization': 'Bearer ' + _token(),
                'Content-Type': 'application/json',
            },
        }, opts || {}));
        let body = null;
        try { body = await r.json(); } catch (e) { body = null; }
        return { ok: r.ok, status: r.status, body: body };
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ── the model, indexed for rendering ─────────────────────────────
    /**
     * One cell per (property, rendered column).
     *
     * The in-flight column resolves the two wire lanes in the order the substrate does:
     * an UNCONDITIONAL binding wins over a triggered one, because that is the stronger
     * claim — it decides rather than escalates — and showing the weaker one beside it
     * would misreport what the property can do.
     */
    function _cell(rows, prop, column) {
        const forProp = rows.filter((r) => r.property === prop);
        if (column === 'POST_HOC') {
            return forProp.find((r) => r.lane === 'POST_HOC') || null;
        }
        const uncond = forProp.find((r) => r.lane === 'IN_FLIGHT');
        const trig = forProp.find((r) => r.lane === 'IN_FLIGHT_TRIGGERED');
        if (uncond && uncond.bound) return uncond;
        if (trig && trig.bound) return trig;
        // Neither bound: prefer whichever lane the property actually HAS, so the cell can
        // render the by-design note rather than a generic emptiness.
        if (uncond && uncond.meaningful) return uncond;
        if (trig && trig.meaningful) return trig;
        return uncond || trig || null;
    }

    function _moduleOf(key) {
        return (_model && (_model.modules || []).find((m) => m.key === key)) || null;
    }

    function _overrideOf(prop, lane) {
        return (_model && (_model.overrides || []).find(
            (o) => o.property === prop && o.lane === lane)) || null;
    }

    function _driftOn(prop, lane) {
        return (_model && (_model.drift || []).some(
            (d) => d.property === prop && d.lane === lane)) || false;
    }

    // ── marks ────────────────────────────────────────────────────────
    /** The M badge — the same letter-in-a-chip the Assurance Guide uses for a module, so
     *  the two surfaces name the same thing the same way. A letter is the channel, so it
     *  survives greyscale (Rule 9.4a). */
    function _mBadge() {
        return '<span class="ab-mark" role="img" aria-label="Module">M</span>';
    }

    /** A third-party module's trust seal. Builtins carry none: "trusted by shipping" is
     *  not a state an operator can act on, and a badge on every row would be noise. */
    function _sealFor(mod) {
        if (!mod || mod.builtin) return '';
        const st = (mod.trust && mod.trust.state) || 'unknown';
        const label = {
            trusted: 'signed and intact',
            changed: 'signed, but changed since',
            unsigned: 'not signed',
            deployment_unattested: 'not attested here',
            unknown: 'trust state could not be resolved',
        }[st] || st;
        const tone = st === 'trusted' ? 'pass' : (st === 'changed' ? 'danger' : 'warn');
        return `<span class="ab-seal ab-seal--${tone} u-tip" data-tip="${esc(label)}"
            data-seal="${esc(st)}">${esc(st === 'trusted' ? 'signed' : st.replace(/_/g, ' '))}</span>`;
    }

    // ── cells ────────────────────────────────────────────────────────
    function _cellHtml(prop, column, row) {
        // A staged cell shows what it WILL be, marked as not in force. Showing the current
        // value would hide the operator's own decision; showing the new value unmarked
        // would claim it is already deciding what the agent may say. It says both.
        const st = _staged.get(_stageKey(prop, column));
        if (st) {
            const to = st.act === 'bind'
                ? ((_moduleOf(st.key) || {}).label || st.key)
                : (st.act === 'restore' ? 'the shipped default' : 'NOT ASSESSED');
            return `<div class="ab-cell ab-cell--staged">
                <span class="ab-staged-mark" aria-hidden="true"></span>
                <span class="ab-staged-to">${esc(to)}</span>
                <span class="ab-note ab-note--staged">not applied yet</span>
                <button type="button" class="ab-plain ab-undo"
                    data-undo="${esc(_stageKey(prop, column))}">undo</button>
                ${_failed.has(_stageKey(prop, column))
                    ? `<div class="ab-err ab-err--row"><strong>Refused</strong><br>${
                        esc(_failed.get(_stageKey(prop, column)))}</div>` : ''}
            </div>`;
        }
        if (!row) return '<div class="ab-cell ab-cell--na">&mdash;</div>';

        // A lane the property does not have. The property's own note is the reading —
        // never a generic "unavailable", which would make a deliberate design decision
        // look like a missing feature.
        if (!row.meaningful && !row.bound) {
            const note = row.note || 'this property has no such lane';
            return `<div class="ab-cell ab-cell--design"><em>${esc(note)}</em></div>`;
        }

        if (!row.bound) {
            return `<div class="ab-cell ab-cell--unbound">
                <button type="button" class="ab-pick" data-prop="${esc(prop)}"
                    data-lane="${esc(row.lane)}">nothing bound</button>
                <span class="ab-sub">not assessed &mdash; never assessed-clean</span>
            </div>`;
        }

        const mod = _moduleOf(row.assessor);
        const label = (mod && mod.label) || row.assessor;
        const ov = _overrideOf(prop, row.lane);
        const drift = _driftOn(prop, row.lane);

        // What the binding can DO, said in words. `decides` and `escalates` are the same
        // distinction the Guide draws: a triggered assessor cannot withhold anything on
        // its own, it can only ask a judge.
        let power = '';
        if (column === 'IN_FLIGHT') {
            power = row.lane === 'IN_FLIGHT' ? 'can act on the answer'
                                             : 'can only escalate to a judge';
        } else {
            power = 'reports only';
        }

        // Bound in flight, but no gate asks. The binding is real and the studio must not
        // imply it can move an answer — the honest reading is that nothing consults it.
        //
        // Three distinct readings, and collapsing any two loses a fact an operator needs:
        //   consulted directly — the gate resolves it; this IS the gate's assessment
        //   consulted via the seam — the gate has its own, and this ADDS to it
        //   not consulted — bound, but nothing asks, so it cannot move an answer
        const consumers = (_model && _model.inflight_consumers) || {};
        const seam = new Set((_model && _model.seam_consulted) || []);
        const routed = (_model && _model.routed) || {};
        const gate = consumers[prop] || routed[prop] || null;
        const unconsulted = column === 'IN_FLIGHT' && !gate;
        const viaSeam = column === 'IN_FLIGHT' && gate
                        && (seam.has(prop) || !!routed[prop]);

        return `<div class="ab-cell ${drift ? 'ab-cell--drift' : ''}">
            <button type="button" class="ab-pick ab-pick--bound" data-prop="${esc(prop)}"
                data-lane="${esc(row.lane)}">${esc(label)}${_mBadge()}</button>
            ${_sealFor(mod)}
            ${ov ? '<span class="ab-tag u-tip" data-tip="' +
                   esc('Changed from the shipped default' +
                       (ov.bound_at ? ' on ' + String(ov.bound_at).slice(0, 16) : '') +
                       (ov.note ? ' — ' + ov.note : '')) + '">override</span>' : ''}
            <span class="ab-sub">${esc(power)}</span>
            ${unconsulted ? '<span class="ab-note ab-note--warn u-tip" data-tip="' +
                esc('This lane is bound, but no gate resolves this property during a turn ' +
                    'yet — so the binding does not currently affect an answer.') +
                '">no gate consults this in flight yet</span>' : ''}
            ${viaSeam ? '<span class="ab-note ab-note--ok u-tip" data-tip="' +
                esc('The ' + String(gate).replace(/_/g, ' ') + ' performs its own '
                    + 'assessment and takes this one as well, deciding over both. A '
                    + 'module here can make the gate act where it would not have; it '
                    + 'can never make it pass where it would have acted.') +
                '">' + esc(String(gate).replace(/_/g, ' ')) + ' acts on this</span>' : ''}
            ${drift ? '<span class="ab-note ab-note--danger">this module is no longer valid &mdash; re-sign it or unbind</span>' : ''}
        </div>`;
    }

    // ── symmetry ─────────────────────────────────────────────────────
    /** The symmetry word, in the Guide's own sentences. Derived server-side from the
     *  bindings, so it cannot go stale against the table beside it. */
    const SYMMETRY_TEXT = {
        shared: 'One assessment, both lanes — they can never disagree.',
        split: 'Two different assessments — the gate and the score can disagree.',
        by_design_score_only: 'Graded after the turn only, by design.',
        by_design_act_only: 'Acts during the turn only, by design.',
        gap: 'Assessed in one lane only — the other is a gap, not a design decision.',
        unbound: 'Nothing assesses this property at all.',
        unknown: 'Unknown.',
    };

    function _symmetryHtml(prop) {
        const s = (_model && _model.symmetry && _model.symmetry[prop]) || {};
        const st = s.state || 'unknown';
        return `<div class="ab-sym ab-sym--${esc(st)} u-tip"
            data-tip="${esc(SYMMETRY_TEXT[st] || st)}">${esc(st.replace(/_/g, ' '))}</div>`;
    }

    // ── staging ──────────────────────────────────────────────────────
    /** Changes an operator has CHOSEN but not yet applied.
     *
     *  The card used to apply on confirm, each change its own audited act. That was right
     *  while it sat below the panel's Save row — it was visibly not part of it. Once it
     *  moved into the stack, an operator reasonably expects Save to cover everything above
     *  it, and two save models in one column is the ambiguity this panel exists to remove.
     *
     *  THE RISK THIS DESIGN AVOIDS. Staging normally moves refusals to Save: you queue
     *  five changes, press Save, and learn the second was invalid. That does not happen
     *  here, because eligibility is decided at PICK time — `conformance.candidates()`
     *  returns eligible/blocked with reasons before anything is written, and the picker
     *  never offers a blocked module. The server re-checks on apply and still fails
     *  closed, so nothing is weakened; the reasons simply stay where they were.
     *
     *  Each staged change is still applied as ITS OWN request on save, never a batch:
     *  one act, one audit event, and a refusal names the row it belongs to.
     */
    const DORMANT_POSTURE = 'observe';
    /** Refusals from the last apply, keyed like the staged change that caused them.
     *  A refused change stays STAGED, so its reason belongs on its own row — the Save
     *  status can only carry one, and "1 refused" without saying which is a dead end. */
    const _failed = new Map();
    const _staged = new Map();       // 'prop|lane' -> {prop, lane, act, key}
    const _stagedPost = new Map();   // controlKey  -> posture | null (null = restore)

    function _stageKey(prop, lane) { return prop + '|' + lane; }

    function isDirty() { return _staged.size > 0 || _stagedPost.size > 0; }

    function _stagedCount() { return _staged.size + _stagedPost.size; }

    function _stage(prop, lane, act, key) {
        _failed.delete(_stageKey(prop, lane));   // a new choice is not the old refusal
        _staged.set(_stageKey(prop, lane), { prop, lane, act, key });
        _closePicker();
        _render();
        _announceDirty();
    }

    function _unstage(k, isPosture) {
        _failed.delete(k);
        (isPosture ? _stagedPost : _staged).delete(k);
        _render();
        _announceDirty();
    }

    /** Warn before the page is left with work chosen and not applied. An accordion hides
     *  the row, so without this a staged change is invisible AND lost. */
    function _onBeforeUnload(e) {
        if (!isDirty()) return undefined;
        e.preventDefault();
        e.returnValue = '';
        return '';
    }

    function _announceDirty() {
        if (isDirty()) window.addEventListener('beforeunload', _onBeforeUnload);
        else window.removeEventListener('beforeunload', _onBeforeUnload);
        // The panel's Save is the one that applies these, so it must SHOW there is work.
        const btn = document.getElementById('save-agent-eval-btn');
        if (btn) btn.dataset.assuranceDirty = isDirty() ? String(_stagedCount()) : '';
    }

    function _dirtyHtml() {
        if (!isDirty()) return '';
        const n = _stagedCount();
        return `<div class="ab-dirty" role="status">
            <span class="ab-dirty-n">${n}</span>
            <span>change${n === 1 ? '' : 's'} chosen and not yet applied &mdash; press
              <b>Save Trust Settings</b> below to apply ${n === 1 ? 'it' : 'them'}.
              Each is applied and audited on its own.</span>
            <button type="button" class="ab-plain" data-ab-discard>Discard</button>
        </div>`;
    }

    /** Apply every staged change, in order, each as its own request.
     *
     *  Sequential and NOT a batch endpoint: the audit record is one act per change, and a
     *  refusal must be attributable to the row that caused it. A failure keeps its change
     *  STAGED with its reason attached — losing an operator's choice because the server
     *  disagreed with one of five is the outcome this whole surface exists to prevent.
     *
     *  Returns {applied, failed:[{label, reason}]} for the Save button to report.
     */
    async function applyStaged() {
        const failed = [];
        let applied = 0;
        _failed.clear();

        for (const [k, c] of [..._staged]) {
            let res;
            if (c.act === 'bind') {
                res = await _api('/assurance/bindings', {
                    method: 'POST',
                    body: JSON.stringify({ property: c.prop, lane: c.lane, assessor: c.key }),
                });
            } else {
                res = await _api(`/assurance/bindings/${encodeURIComponent(c.prop)}/${
                    encodeURIComponent(c.lane)}?restore_default=${c.act === 'restore'}`,
                    { method: 'DELETE' });
            }
            if (res.ok) { _staged.delete(k); applied += 1; }
            else {
                const reason = ((res.body && res.body.reasons) || [])[0]
                    || ((res.body && res.body.message) || `HTTP ${res.status}`);
                _failed.set(k, reason);
                failed.push({ label: `${c.prop} · ${c.lane}`, reason });
            }
        }

        for (const [key, posture] of [..._stagedPost]) {
            const res = posture === null
                ? await _api(`/assurance/controls/${encodeURIComponent(key)}/posture`,
                             { method: 'DELETE' })
                : await _api(`/assurance/controls/${encodeURIComponent(key)}/posture`,
                             { method: 'POST', body: JSON.stringify({ posture }) });
            if (res.ok) { _stagedPost.delete(key); applied += 1; }
            else {
                const reason = (res.body && res.body.message) || `HTTP ${res.status}`;
                _failed.set(key, reason);
                failed.push({ label: key, reason });
            }
        }

        _announceDirty();
        await refresh();
        return { applied, failed };
    }

    // ── the ring ─────────────────────────────────────────────────────
    /** The eight guarantees, in RUNTIME order — the order the Trust Circle draws, because
     *  this is the same eight and a second ordering would read as a second model.
     *
     *  DELIBERATELY A DIFFERENT INSTRUMENT from the Agents lens's ring. There, colour
     *  means attention priority on live data. A config page has no live agent, so a
     *  coloured ring here would be the same shape with different rules, and an operator
     *  would read "GROUNDED is red" as "GROUNDED is misconfigured" — the exact drift the
     *  trust grammar exists to prevent. So: no state fill, structural strokes only, and
     *  each segment labelled with what it HOLDS, never how it is DOING. */
    // [key, LABEL, the question the station answers] — from the ONE registry
    // (trustGrammar.STATIONS, W5). This surface used to carry its own eight subtitles,
    // seven of them differing from the ring's; the question is the one line a reader
    // needs here and it is the same line the ring leads with.
    const _TGS = (window.TrustGrammar && window.TrustGrammar.STATIONS) || null;
    const STATIONS = _TGS ? _TGS.map((r) => [r.key, String(r.label).toUpperCase(), r.q]) : [
        ['signed', 'SIGNED', 'Is it signed, and attested here?'],
        ['grounded', 'GROUNDED', 'Does it stay on its corpus?'],
        ['guarded', 'GUARDED', 'Does it stay within the rules?'],
        ['bounded', 'BOUNDED', 'Does it act only with the authority it was given?'],
        ['scored', 'JUDGED', 'How did its answers grade?'],
        ['trusted', 'TRUSTED', 'Is it trusted over time?'],
        ['reviewed', 'REVIEWED', 'Is a person deciding?'],
        ['proven', 'PROVEN', 'Can its answers be proven?'],
    ];
    let _sel = 'grounded';

    function _propsAt(station) {
        const rows = (_model && _model.rows) || [];
        const seen = [];
        rows.forEach((r) => {
            if (r.station === station && seen.indexOf(r.property) < 0) seen.push(r.property);
        });
        return seen;
    }

    /** What ACTS on this guarantee — the PLATFORM's own machinery and any pack's, in one
     *  list. The first version read the third-party registry alone, so with no pack
     *  installed every station said "nothing acts here" — including GUARDED, which two
     *  gates uphold. One concept, two registries; the surface must not inherit the split.
     *  Platform first: it is what is there before anyone installs anything. */
    function _ctrlsAt(station) {
        const plat = ((_model && _model.mechanisms) || [])
            .filter((m) => m.station === station);
        const pack = ((_model && _model.controls) || [])
            .filter((c) => c.station === station);
        return plat.concat(pack);
    }

    function _ringHtml() {
        const R = 108, CX = 132, CY = 132, GAP = 5;
        const seg = STATIONS.map(([key, label], i) => {
            const step = 360 / STATIONS.length;
            const a0 = -90 + i * step + GAP / 2, a1 = -90 + (i + 1) * step - GAP / 2;
            const rad = (d) => (d * Math.PI) / 180;
            const x0 = CX + R * Math.cos(rad(a0)), y0 = CY + R * Math.sin(rad(a0));
            const x1 = CX + R * Math.cos(rad(a1)), y1 = CY + R * Math.sin(rad(a1));
            const mid = rad((a0 + a1) / 2);
            const lx = CX + (R + 26) * Math.cos(mid), ly = CY + (R + 26) * Math.sin(mid);
            const n = _propsAt(key).length + _ctrlsAt(key).length;
            return `<g class="ab-seg" data-station="${esc(key)}"
                       data-sel="${_sel === key ? 1 : 0}" tabindex="0" role="button"
                       aria-label="${esc(label)}" aria-pressed="${_sel === key}">
                <path class="ab-seg-hit" d="M ${x0} ${y0} A ${R} ${R} 0 0 1 ${x1} ${y1}"/>
                <path class="ab-seg-arc" d="M ${x0} ${y0} A ${R} ${R} 0 0 1 ${x1} ${y1}"/>
                <text class="ab-seg-label" x="${lx}" y="${ly}"
                      text-anchor="middle">${esc(label)}</text>
                <text class="ab-seg-count" x="${lx}" y="${ly + 10}"
                      text-anchor="middle">${n}</text>
            </g>`;
        }).join('');
        return `<svg id="ab-ring" viewBox="-34 -14 332 292" role="group"
                     aria-label="The eight guarantees">${seg}</svg>`;
    }

    function _stationHtml() {
        const st = STATIONS.find((x) => x[0] === _sel) || STATIONS[1];
        const props = _propsAt(_sel), ctrls = _ctrlsAt(_sel);
        const rows = (_model && _model.rows) || [];
        // A tab HIDES state, and on a governance surface that is a real risk: a dormant
        // control and an unassessed property are exactly what an operator must notice. So
        // each tab carries its count AND an attention mark — a tab may hide the detail,
        // never the fact that there is something to see.
        const ctrlOwed = ctrls.filter((c) => c.third_party && !c.acts_now).length;
        const capOwed = props.filter((p) => rows.some(
            (r) => r.property === p && r.meaningful && !r.bound)).length;
        const tab = (id, label, n, owed) => `<button type="button" class="ab-dtab"
            role="tab" data-dtab="${id}" aria-selected="${_tab === id}"
            ${_tab === id ? '' : 'tabindex="-1"'}>${esc(label)}<span
            class="ab-dtab-n">${n}</span>${owed
                ? `<span class="ab-dtab-owed" role="img" aria-label="${owed} need${
                    owed === 1 ? 's' : ''} a decision"></span>` : ''}</button>`;

        const body = _tab === 'acts'
            ? (ctrls.length ? _controlRowsHtml(ctrls)
                : `<p class="ab-empty">Nothing acts here &mdash; this guarantee is upheld
                   by what is recorded, not by a gate.</p>`)
            : (props.length ? _propRowsHtml(props)
                : `<p class="ab-empty">No property is filed under this guarantee. It
                   reports what its mechanisms did during a turn; no assessment is
                   assigned here.</p>`);

        return `<div class="ab-dhead"><b>${esc(st[1])}</b>
                <span class="ab-sub">${esc(st[2])}</span></div>
            <div class="ab-dtabs" role="tablist" aria-label="${esc(st[1])} detail">
                ${tab('acts', 'What acts on it', ctrls.length, ctrlOwed)}
                ${tab('speaks', 'What speaks to it', props.length, capOwed)}
            </div>
            <div class="ab-dpanel" role="tabpanel">${body}</div>`;
    }

    let _tab = 'acts';
    let _view = 'ring';

    // ── the matrix ───────────────────────────────────────────────────
    function _matrixHtml() {
        if (!_model) return '<p class="ab-empty">Loading the binding table&hellip;</p>';
        if (_model.error) {
            return `<p class="ab-empty">The binding table could not be read: ${esc(_model.error)}</p>`;
        }
        const props = [];
        (_model.rows || []).forEach((r) => {
            if (props.indexOf(r.property) < 0) props.push(r.property); });
        if (!props.length) return '<p class="ab-empty">No assessable properties are registered.</p>';
        // The honest empty state this surface never had. Remapping needs ALTERNATIVES, and
        // alternatives come only from packs: on a fresh install each property has exactly
        // one shipped module, the module already bound is correctly not offered as a
        // change, and the only remaining acts are unbind (a weakening) and restore. So the
        // table looked configurable and offered nothing — indistinguishable from broken.
        // Every other absence on these surfaces is rendered; this one was not.
        const alts = ((_model.modules) || []).filter((m) => !m.builtin).length;
        const note = alts ? '' : `<p class="ab-lede ab-lede--empty">One module assesses each
            property here, and no alternative is installed &mdash; so there is nothing to
            remap yet. A third-party pack adds the choices; until then this table is a
            reading, not a control.</p>`;
        return note + _propRowsHtml(props);
    }

    /** ONE property table, whether it is showing a guarantee's own properties or all of
     *  them. The two views are different QUESTIONS, never different renderers — a second
     *  copy would drift, and the row is where every action lives. */
    function _propRowsHtml(props) {
        const rows = _model.rows || [];

        const head = COLUMNS.map((c) =>
            `<th class="u-tip" data-tip="${esc(c.tip)}">${esc(c.label)}</th>`).join('');

        const body = props.map((p) => {
            const first = rows.find((r) => r.property === p) || {};
            const cells = COLUMNS.map((c) =>
                `<td>${_cellHtml(p, c.key, _cell(rows, p, c.key))}</td>`).join('');
            const third = p.indexOf('.') >= 0;
            return `<tr data-prop="${esc(p)}">
                <th scope="row" class="ab-prop">
                    <span class="ab-prop-name">${esc(first.label || p)}</span>
                    ${third ? '<span class="ab-tag ab-tag--pack">' +
                              esc(p.split('.')[0]) + '</span>' : ''}
                </th>
                ${cells}
                <td class="ab-sym-cell">${_symmetryHtml(p)}</td>
            </tr>`;
        }).join('');

        return `<table class="ab-matrix">
            <thead><tr><th class="ab-prop">Property</th>${head}
                <th class="u-tip" data-tip="Whether the two lanes read the SAME assessment. Shared is the strongest state a property can be in.">Symmetry</th></tr></thead>
            <tbody>${body}</tbody>
        </table>`;
    }

    // ── the picker ───────────────────────────────────────────────────
    function _closePicker() {
        const el = document.getElementById('ab-picker');
        if (el) el.remove();
        document.removeEventListener('keydown', _onPickerKey, true);
        document.removeEventListener('mousedown', _onPickerOutside, true);
    }

    function _onPickerKey(e) { if (e.key === 'Escape') { e.stopPropagation(); _closePicker(); } }

    function _onPickerOutside(e) {
        const el = document.getElementById('ab-picker');
        if (el && !el.contains(e.target)) _closePicker();
    }

    async function _openPicker(anchor, prop, lane) {
        _closePicker();
        const box = document.createElement('div');
        box.id = 'ab-picker';
        box.className = 'ab-picker';
        box.innerHTML = '<div class="ab-picker-load">Loading modules&hellip;</div>';
        document.body.appendChild(box);
        const r = anchor.getBoundingClientRect();
        box.style.top = (window.scrollY + r.bottom + 6) + 'px';
        box.style.left = Math.max(8, Math.min(
            window.scrollX + r.left, window.scrollX + window.innerWidth - 380)) + 'px';
        document.addEventListener('keydown', _onPickerKey, true);
        setTimeout(() => document.addEventListener('mousedown', _onPickerOutside, true), 0);

        const res = await _api(`/assurance/bindings/candidates?property=${
            encodeURIComponent(prop)}&lane=${encodeURIComponent(lane)}`);
        const cands = (res.body && res.body.candidates) || [];
        const rowNow = (_model.rows || []).find(
            (x) => x.property === prop && x.lane === lane) || {};
        const hasOverride = !!_overrideOf(prop, lane);

        const list = cands.map((c) => {
            const isCurrent = c.key === rowNow.assessor;
            // The module already bound here is shown, but is NOT an offer: selecting it
            // could not move the state, and an act that cannot move the state reads as
            // the fix (Rule 8.5). Live-found — the confirm step otherwise rendered
            // "Corpus support → Corpus support" and would have written that to the audit.
            const eligible = !!c.eligible && !isCurrent &&
                             !(c.trust && !c.builtin && c.trust.state !== 'trusted');
            const why = (c.blocked_by || []).slice();
            if (!c.builtin && c.trust && c.trust.state !== 'trusted' && !why.length) {
                why.push('this module is not signed and intact — sign the pack before binding it');
            }
            if (isCurrent && !why.length) {
                why.push('this is what already assesses it here');
            }
            return `<button type="button" class="ab-opt ${eligible ? '' : 'ab-opt--blocked'}
                    ${isCurrent ? 'ab-opt--current' : ''}" ${eligible ? '' : 'disabled'}
                    data-key="${esc(c.key)}">
                <span class="ab-opt-name">${esc(c.label || c.key)}${
                    isCurrent ? '<span class="ab-tag">current</span>' : ''}${_sealFor(c)}</span>
                ${c.description ? `<span class="ab-opt-desc">${esc(c.description)}</span>`
                    : ''}
                <span class="ab-opt-caps">${esc([
                    c.origin && c.origin !== 'platform'
                        ? `from the ${c.origin} pack${c.version ? ' v' + c.version : ''}`
                        : 'platform',
                    ...(c.capabilities || []),
                ].join(' · '))}</span>
                ${why.length ? '<span class="ab-opt-why">' +
                    why.map((w) => esc(w)).join('<br>') + '</span>' : ''}
            </button>`;
        }).join('');

        box.innerHTML = `
            <div class="ab-picker-head">Assess <strong>${esc(rowNow.label || prop)}</strong>
                ${esc(lane === 'POST_HOC' ? 'after the turn' : 'during the turn')} with&hellip;</div>
            <div class="ab-picker-list">${list ||
                '<div class="ab-picker-load">No module assesses this property.</div>'}</div>
            <div class="ab-picker-foot">
                ${rowNow.bound ? `<button type="button" class="ab-danger" data-act="unbind">
                    Unbind &mdash; leave this NOT ASSESSED</button>` : ''}
                ${hasOverride ? `<button type="button" class="ab-plain" data-act="restore">
                    Restore the shipped default</button>` : ''}
            </div>`;

        box.querySelectorAll('.ab-opt:not([disabled])').forEach((b) => {
            b.addEventListener('click', () => _confirm(prop, lane, b.dataset.key, 'bind'));
        });
        const ub = box.querySelector('[data-act="unbind"]');
        if (ub) ub.addEventListener('click', () => _confirm(prop, lane, null, 'unbind'));
        const rb = box.querySelector('[data-act="restore"]');
        if (rb) rb.addEventListener('click', () => _confirm(prop, lane, null, 'restore'));
    }

    // ── confirm, then apply ──────────────────────────────────────────
    /**
     * NOTHING is applied on selection. The consequence of a rebind is not obvious from
     * the option label — it changes what a gate sees AND what the scorecard reports — so
     * the act is stated before it is taken (Rule 8.5: the offered act must be the act
     * that is owed, and the operator must be able to know it).
     */
    function _confirm(prop, lane, key, act) {
        const box = document.getElementById('ab-picker');
        if (!box) return;
        const rowNow = (_model.rows || []).find(
            (x) => x.property === prop && x.lane === lane) || {};
        const fromLabel = rowNow.assessor
            ? ((_moduleOf(rowNow.assessor) || {}).label || rowNow.assessor) : 'nothing';
        const toLabel = act === 'bind'
            ? ((_moduleOf(key) || {}).label || key)
            : (act === 'restore' ? 'the shipped default' : 'nothing');

        const consequence = act === 'unbind'
            ? '<p class="ab-warn">This property will read NOT ASSESSED in this lane &mdash; never assessed-clean.</p>'
            : '<p class="ab-cons">Both this lane’s reading and everything derived from it change together. The symmetry state may change; it is re-read after applying.</p>';

        box.innerHTML = `
            <div class="ab-picker-head">Confirm</div>
            <div class="ab-confirm">
                <p class="ab-from-to"><strong>${esc(fromLabel)}</strong> &rarr;
                   <strong>${esc(toLabel)}</strong></p>
                ${consequence}
                <div class="ab-confirm-acts">
                    <button type="button" class="ab-plain" data-act="cancel">Cancel</button>
                    <button type="button" class="ab-primary" data-act="go">Apply</button>
                </div>
                <div class="ab-err" hidden></div>
            </div>`;
        box.querySelector('[data-act="cancel"]').addEventListener(
            'click', () => _openPicker(document.querySelector(
                `[data-prop="${CSS.escape(prop)}"] .ab-pick[data-lane="${CSS.escape(lane)}"]`)
                || document.body, prop, lane));
        box.querySelector('[data-act="go"]').addEventListener(
            'click', () => _apply(prop, lane, key, act));
    }

    /** The confirm step now STAGES rather than writes. Eligibility was already decided
     *  by the picker (see the staging block), so this cannot be where a refusal appears —
     *  it is the operator recording a decision, and Save is where it takes effect. */
    function _apply(prop, lane, key, act) {
        _stage(prop, lane, act, key);
    }

    // ── controls — the machinery that ACTS ───────────────────────────
    /** The posture ladder, in the operator's words.
     *
     *  `off` and `observe` both change nothing today, and they are still different: `off`
     *  is "do not run this at all", `observe` is "run it and tell me what it would have
     *  done". Collapsing them would take away the only way to trial a control safely,
     *  which is the whole reason a control may be third-party at all. */
    // Leads with the ONE posture vocabulary (trustGrammar.POSTURE_LABEL — W4); the
    // tail describes what the posture does to a control.
    const POSTURE_WORDS = {
        off: 'Off — not consulted',
        observe: 'Watch — records what it would do, changes nothing',
        annotate: 'Watch + disclose — adds a note, never withholds or alters',
        enforce: 'Enforce — acts',
        review: 'Enforce + keep for review — acts, and parks the original for a human',
    };

    function _controlsHtml() {
        return _controlRowsHtml((_model && _model.controls) || []);
    }

    function _controlRowsHtml(list) {
        if (!list.length) {
            return `<p class="ab-empty">No control is registered. Nothing here acts on a
                turn &mdash; the guarantees are upheld by what is recorded.</p>`;
        }
        const rows = list.map((c) => {
            // A PLATFORM mechanism has no deployment-wide posture — a gate's mode is
            // per-agent. It says where that is set instead of offering a switch that
            // would silently mean something else.
            if (!c.third_party) {
                return `<tr data-ctl="${esc(c.key)}">
                    <th scope="row" class="ab-prop">
                        <span class="ab-prop-name">${esc(c.label)}</span>
                        <span class="ab-tag">${esc(c.cls)}</span>
                    </th>
                    <td><span class="ab-sub">${esc(c.act_words)}</span></td>
                    <td><span class="ab-sub">${c.chokepoint
                        ? 'at the ' + esc(c.chokepoint.replace(/_/g, ' ')) : '&mdash;'}</span></td>
                    <td><span class="ab-note">${esc(c.posture_note)}</span></td>
                </tr>`;
            }
            // DORMANT is the state worth marking, because it is the one an operator will
            // misread: a control that is installed, signed and visible, and doing nothing.
            const dormant = !c.acts_now;
            const stagedP = _stagedPost.has(c.key) ? _stagedPost.get(c.key) : undefined;
            const shown = stagedP === undefined ? c.posture
                : (stagedP === null ? DORMANT_POSTURE : stagedP);
            const opts = (_model.control_postures || ['observe', 'enforce']).map((k) =>
                `<option value="${esc(k)}"${k === ((_pending && _pending.key === c.key)
                    ? _pending.posture : shown) ? ' selected' : ''}
                    >${esc(POSTURE_WORDS[k] || k)}</option>`).join('');
            return `<tr data-ctl="${esc(c.key)}">
                <th scope="row" class="ab-prop">
                    <span class="ab-prop-name">${esc(c.label)}</span>
                    ${c.third_party
                        ? `<span class="ab-tag ab-tag--pack">${esc(c.origin)}</span>` : ''}
                    ${c.description ? `<span class="ab-sub">${esc(c.description)}</span>` : ''}
                </th>
                <td><span class="ab-sub">${esc(c.act_words || c.act)}</span></td>
                <td><span class="ab-sub">when <b>${esc(
                    (c.fires_on || '').split('.').pop())}</b> triggers</span></td>
                <td>
                    <select class="ab-posture" data-ctl="${esc(c.key)}"
                        aria-label="Posture for ${esc(c.label)}">${opts}</select>
                    ${dormant
                        ? '<span class="ab-note">dormant &mdash; changes nothing</span>'
                        : ''}
                    ${c.configured
                        ? `<button type="button" class="ab-restore-ctl"
                             data-ctl="${esc(c.key)}">restore</button>`
                        : '<span class="ab-note">no decision recorded</span>'}
                    ${stagedP !== undefined ? `<span class="ab-note ab-note--staged">${
                        stagedP === null ? 'restore staged' : 'change staged'
                      } &mdash; not applied yet</span>
                      <button type="button" class="ab-plain ab-undo"
                        data-undo-post="${esc(c.key)}">undo</button>${
                        _failed.has(c.key)
                          ? `<div class="ab-err ab-err--row"><strong>Refused</strong><br>${
                              esc(_failed.get(c.key))}</div>` : ''}` : ''}
                    ${_pending && _pending.key === c.key ? _pendingHtml(c) : ''}
                    <div class="ab-err" hidden></div>
                </td>
            </tr>`;
        }).join('');
        return `<table class="ab-matrix ab-controls">
            <thead><tr>
                <th class="ab-prop">Control</th>
                <th class="u-tip" data-tip="What the platform does on this control's behalf when its posture permits an act. The act is the platform's; the control only chooses which one.">Act</th>
                <th class="u-tip" data-tip="The property whose finding triggers it. A control only ever sees findings from a module bound to that property — which is why an unsigned pack's control can never act.">Fires on</th>
                <th class="u-tip" data-tip="A third-party control arrives dormant. Raising it is your decision, and it is recorded in this deployment's attestation.">Posture</th>
            </tr></thead>
            <tbody>${rows}</tbody></table>`;
    }

    /** Staged posture change, keyed by control. A change is ARMED, never applied on the
     *  select's own event: raising a control starts altering live answers, and that is not
     *  a thing to do on a stray scroll over a dropdown. */
    let _pending = null;

    function _armPosture(key, posture) {
        const ctl = ((_model && _model.controls) || []).find((c) => c.key === key);
        if (!ctl) return;
        const willAct = !!_actAt(ctl, posture);
        // Ceremony only where the change grants POWER. Standing a control down, or moving
        // between two postures that both do nothing, applies straight away — a confirm on
        // every change is one people learn to click through.
        if (!willAct) return _applyPosture(key, posture);
        _pending = { key: key, posture: posture };
        _render();
    }

    /** What this posture would do — read from the served ladder, never re-derived here.
     *  The platform owns the ladder; the studio must not keep a second opinion of it. */
    function _actAt(ctl, posture) {
        if (posture === 'off' || posture === 'observe') return null;
        if (posture === 'annotate') return 'annotate';
        return ctl.act;
    }

    function _pendingHtml(ctl) {
        const act = _actAt(ctl, _pending.posture);
        return `<div class="ab-confirm ab-confirm--inline">
            <p class="ab-cons"><strong>${esc(ctl.label)}</strong> will start acting on live
               answers: it ${esc(act === 'annotate'
                   ? 'adds a note to the answer' : (ctl.act_words || ctl.act))}.
               This is recorded in the deployment attestation, so the agent reads
               <em>changed</em> until it is re-attested.</p>
            <div class="ab-confirm-acts">
                <button type="button" class="ab-plain" data-ctl-cancel>Cancel</button>
                <button type="button" class="ab-primary"
                    data-ctl-go="${esc(ctl.key)}">Apply</button>
            </div>
            <div class="ab-err" hidden></div>
        </div>`;
    }

    function _applyPosture(key, posture) {
        _stagedPost.set(key, posture);
        _pending = null;
        _render();
        _announceDirty();
    }

    function _restorePosture(key) {
        // `null` is the RESTORE act, distinct from staging `observe`: one forgets the
        // decision, the other records it. The store keeps them apart and so must this.
        _stagedPost.set(key, null);
        _pending = null;
        _render();
        _announceDirty();
    }

    // ── mount ────────────────────────────────────────────────────────
    function _host() { return document.getElementById(HOST_ID); }
    function _packHost() { return document.getElementById('assurance-modules-host'); }

    function _render() {
        const h = _host();
        if (!h) return;
        // TWO VIEWS, ONE MODEL. The ring answers "what upholds this promise?"; the
        // table answers "where is this capability used?". Neither is a subset of the
        // other and both reach every action, so an operator arriving with a guarantee in
        // mind and one arriving with a module in mind each land one click from the row.
        const views = [['ring', 'By guarantee'], ['table', 'By capability']]
            .map(([k, l]) => `<button type="button" class="ab-vtab" role="tab"
                data-view="${k}" aria-selected="${_view === k}"
                ${_view === k ? '' : 'tabindex="-1"'}>${l}</button>`).join('');

        h.innerHTML = _dirtyHtml() + `<div class="ab-views" role="tablist"
              aria-label="How to read the assurance plane">${views}</div>`
            + (_view === 'ring'
                ? `<div class="ab-ringwrap">
                     <div class="ab-ringpane">${_ringHtml()}
                       <p class="ab-ringhint">Select a guarantee.</p></div>
                     <div class="ab-detail">${_stationHtml()}</div>
                   </div>`
                : _matrixHtml() + `
                   <h4 class="ab-subhead">What acts on a turn</h4>
                   <p class="ab-lede">A control decides <b>when</b> to act and <b>which</b>
                      act; the platform performs it. A third-party control arrives dormant
                      and does nothing until you raise it here.</p>` + _controlsHtml());

        h.querySelectorAll('[data-undo]').forEach((b) => {
            b.addEventListener('click', () => _unstage(b.dataset.undo, false));
        });
        h.querySelectorAll('[data-undo-post]').forEach((b) => {
            b.addEventListener('click', () => _unstage(b.dataset.undoPost, true));
        });
        const disc = h.querySelector('[data-ab-discard]');
        if (disc) disc.addEventListener('click', () => {
            _staged.clear(); _stagedPost.clear(); _failed.clear();
            _render(); _announceDirty();
        });
        h.querySelectorAll('.ab-vtab').forEach((b) => {
            b.addEventListener('click', () => { _view = b.dataset.view; _render(); });
        });
        h.querySelectorAll('.ab-seg').forEach((g) => {
            const pick = () => { _sel = g.dataset.station; _pending = null; _render(); };
            g.addEventListener('click', pick);
            g.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
            });
        });
        h.querySelectorAll('[data-dtab]').forEach((b) => {
            b.addEventListener('click', () => { _tab = b.dataset.dtab; _render(); });
            // Arrow keys move between tabs — the expected behaviour for a tablist, and the
            // only way a keyboard user reaches the second roster without traversing the first.
            b.addEventListener('keydown', (e) => {
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
                e.preventDefault();
                _tab = _tab === 'acts' ? 'speaks' : 'acts';
                _render();
                const n = document.querySelector(`[data-dtab="${_tab}"]`);
                if (n) n.focus();
            });
        });
        h.querySelectorAll('.ab-posture').forEach((sel) => {
            sel.addEventListener('change', () => _armPosture(sel.dataset.ctl, sel.value));
        });
        h.querySelectorAll('.ab-restore-ctl').forEach((b) => {
            b.addEventListener('click', () => _restorePosture(b.dataset.ctl));
        });
        h.querySelectorAll('[data-ctl-cancel]').forEach((b) => {
            b.addEventListener('click', () => { _pending = null; _render(); });
        });
        h.querySelectorAll('[data-ctl-go]').forEach((b) => {
            b.addEventListener('click', () => _applyPosture(
                b.dataset.ctlGo, _pending && _pending.posture));
        });
        h.querySelectorAll('.ab-pick').forEach((b) => {
            b.addEventListener('click', (e) => {
                e.preventDefault();
                _openPicker(b, b.dataset.prop, b.dataset.lane);
            });
        });
    }

    // ── installed packs ──────────────────────────────────────────────
    let _packs = null;

    function _packsHtml() {
        if (!_packs) return '<p class="ab-empty">Loading&hellip;</p>';
        const list = _packs.packs || [];
        if (!list.length) {
            const off = !(_packs.settings || {}).third_party_assurance_enabled;
            return `<p class="ab-empty">${off
                ? 'Third-party modules are turned off. Nothing outside the platform is loaded.'
                : 'No third-party pack is installed. Packs live in <code>~/.tda/assurance_modules/</code>.'}</p>`;
        }
        return `<div class="ab-packs">${list.map((p) => {
            const st = (p.trust || {}).state || 'unknown';
            const signed = st === 'trusted';
            const counts = [
                p.properties.length ? `${p.properties.length} propert${
                    p.properties.length === 1 ? 'y' : 'ies'}` : '',
                p.assessors.length ? `${p.assessors.length} module${
                    p.assessors.length === 1 ? '' : 's'}` : '',
            ].filter(Boolean).join(' · ');
            return `<div class="ab-pack">
                <div class="ab-pack-main">
                    <span class="ab-pack-name">${esc(p.display_name)}</span>
                    <span class="ab-tag">${esc(p.version)}</span>
                    ${_sealFor({ builtin: false, trust: p.trust })}
                    <span class="ab-sub">${esc(counts)}${
                        p.author ? ' · ' + esc(p.author) : ''}</span>
                    ${p.problems && p.problems.length ? '<span class="ab-note ab-note--warn">'
                        + p.problems.map((x) => esc(x)).join('<br>') + '</span>' : ''}
                </div>
                ${signed ? '' : `<button type="button" class="ab-plain ab-sign"
                    data-pack="${esc(p.pack_id)}">Sign this pack</button>`}
            </div>`;
        }).join('')}${(_packs.skipped || []).map((s) =>
            `<div class="ab-pack ab-pack--skipped"><span class="ab-sub">${
                esc(s.pack_id)} was not loaded: ${esc(s.reason)}</span></div>`).join('')}</div>`;
    }

    function _renderPacks() {
        const h = _packHost();
        if (!h) return;
        h.innerHTML = _packsHtml();
        const toggle = document.getElementById('assurance-modules-enabled');
        if (toggle && _packs) {
            toggle.checked = !!(_packs.settings || {}).third_party_assurance_enabled;
        }
        h.querySelectorAll('.ab-sign').forEach((b) => {
            b.addEventListener('click', async () => {
                b.disabled = true;
                const prev = b.textContent;
                b.textContent = 'Signing…';
                const r = await _api(
                    `/trust/assurance-module/${encodeURIComponent(b.dataset.pack)}/sign-off`,
                    { method: 'POST' });
                if (!r.ok || (r.body && r.body.signed === false)) {
                    // A refusal to sign is the most informative thing this surface can
                    // say — it is the validator naming what the pack claims and does not
                    // meet — so it is shown, not swallowed into a failed button.
                    const v = (r.body && r.body.validation) || {};
                    b.disabled = false;
                    b.textContent = prev;
                    const note = document.createElement('div');
                    note.className = 'ab-note ab-note--danger';
                    note.textContent = v.note || (r.body && r.body.error)
                        || 'this pack could not be signed';
                    b.parentElement.appendChild(note);
                    return;
                }
                await refresh();
            });
        });
    }

    /** Test seam: drive a view explicitly. The studio has two and both must be gated —
     *  a harness that only ever saw the default would let the other rot. */
    function _setViewForTest(v) { _view = v; _render(); }

    async function refresh() {
        const res = await _api('/assurance/bindings');
        _model = (res.ok && res.body) ? res.body : { rows: [], error: 'unavailable' };
        _render();
        const mres = await _api('/assurance/modules');
        _packs = (mres.ok && mres.body) ? mres.body : { packs: [], settings: {} };
        _renderPacks();
    }

    function mount() {
        if (!_host()) return;
        _injectCss();
        const toggle = document.getElementById('assurance-modules-enabled');
        if (toggle && !toggle.dataset.wired) {
            toggle.dataset.wired = '1';
            toggle.addEventListener('change', async () => {
                toggle.disabled = true;
                await _api('/assurance/modules/settings', {
                    method: 'POST',
                    body: JSON.stringify({
                        third_party_assurance_enabled: toggle.checked }),
                });
                toggle.disabled = false;
                await refresh();
            });
        }
        refresh();
    }

    // A binding change broadcasts a trust event; so does anything else on this surface.
    // Debounced, and only while the host is on screen.
    UderiaBus.on('uderia:trust-event', () => {
        if (!_host()) return;
        clearTimeout(_refreshTimer);
        _refreshTimer = setTimeout(refresh, 400);
    });

    function _injectCss() {
        if (document.getElementById('ab-css')) return;
        const s = document.createElement('style');
        s.id = 'ab-css';
        s.textContent = `
        /* Staging. A staged change must be visible as CHOSEN-BUT-NOT-IN-FORCE — the accent
           is the platform's "an act is owed" channel, which is exactly what this is. */
        .ab-dirty { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
                    border: 1px solid var(--teradata-orange); border-radius: 8px;
                    padding: 9px 12px; margin-bottom: 14px; font-size: 12.5px;
                    background: color-mix(in srgb, var(--teradata-orange) 8%, transparent);
                    color: var(--text-primary); }
        .ab-dirty-n { font-weight: 700; font-variant-numeric: tabular-nums;
                      background: var(--teradata-orange); color: #fff; border-radius: 999px;
                      min-width: 20px; text-align: center; padding: 0 6px; }
        .ab-dirty .ab-plain { margin-left: auto; }
        .ab-cell--staged { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
        .ab-staged-mark { width: 7px; height: 7px; border-radius: 50%; flex: none;
                          background: var(--teradata-orange); }
        .ab-staged-to { font-weight: 600; color: var(--text-primary); }
        .ab-note--staged { color: var(--teradata-orange); }
        .ab-undo { font-size: 11px; }
        .ab-err--row { flex-basis: 100%; margin-top: 4px; font-size: 11.5px;
                       color: var(--trust-danger, #dc2626); }

        /* The ring. Structural strokes only — no state fill. See STATIONS for why this
           must not look like the Agents lens's ring. */
        .ab-views { display: flex; gap: 2px; border-bottom: 1px solid var(--border-secondary);
                    margin-bottom: 14px; }
        .ab-vtab { font: inherit; font-size: 12px; font-weight: 600; color: var(--text-muted);
                   background: none; border: 0; border-bottom: 2px solid transparent;
                   padding: 7px 12px 9px; cursor: pointer; margin-bottom: -1px; }
        .ab-vtab:hover { color: var(--text-primary); }
        .ab-vtab[aria-selected="true"] { color: var(--text-primary);
                   border-bottom-color: var(--teradata-orange); }
        .ab-vtab:focus-visible { outline: 2px solid var(--teradata-orange);
                   outline-offset: -2px; }
        .ab-ringwrap { display: grid; grid-template-columns: 300px 1fr; gap: 0;
                       border: 1px solid var(--border-secondary); border-radius: 10px;
                       overflow: hidden; }
        @media (max-width: 900px) { .ab-ringwrap { grid-template-columns: 1fr; } }
        .ab-ringpane { padding: 16px 12px 18px; border-right: 1px solid var(--border-secondary);
                       display: flex; flex-direction: column; align-items: center; gap: 8px; }
        #ab-ring { width: 268px; height: 236px; overflow: visible; }
        .ab-ringhint { font-size: 11px; color: var(--text-muted); }
        .ab-seg { cursor: pointer; }
        .ab-seg-arc { fill: none; stroke: var(--border-primary); stroke-width: 11;
                      transition: stroke .15s ease; pointer-events: none; }
        .ab-seg-hit { fill: none; stroke: transparent; stroke-width: 34; pointer-events: stroke; }
        .ab-seg:hover .ab-seg-arc { stroke: var(--text-muted); }
        .ab-seg[data-sel="1"] .ab-seg-arc { stroke: var(--teradata-orange); }
        .ab-seg:focus-visible { outline: none; }
        .ab-seg:focus-visible .ab-seg-arc { stroke: var(--teradata-orange); stroke-width: 14; }
        .ab-seg-label, .ab-seg-count { pointer-events: none; }
        .ab-seg-label { font-size: 7.5px; font-weight: 700; letter-spacing: .09em;
                        fill: var(--text-muted); }
        .ab-seg[data-sel="1"] .ab-seg-label { fill: var(--text-primary); }
        .ab-seg-count { font-size: 7px; fill: var(--text-muted); }
        .ab-detail { padding: 16px 18px 18px; min-width: 0; }
        .ab-dhead { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
                    margin-bottom: 10px; }
        .ab-dhead b { font-size: 12px; letter-spacing: .06em; color: var(--text-primary); }
        .ab-dtabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border-secondary);
                    margin-bottom: 12px; }
        .ab-dtab { font: inherit; font-size: 12px; font-weight: 600; color: var(--text-muted);
                   background: none; border: 0; border-bottom: 2px solid transparent;
                   padding: 7px 11px 9px; cursor: pointer; margin-bottom: -1px;
                   display: inline-flex; align-items: center; gap: 7px; }
        .ab-dtab:hover { color: var(--text-primary); }
        .ab-dtab[aria-selected="true"] { color: var(--text-primary);
                   border-bottom-color: var(--teradata-orange); }
        .ab-dtab:focus-visible { outline: 2px solid var(--teradata-orange);
                   outline-offset: -2px; }
        .ab-dtab-n { font-size: 10.5px; font-variant-numeric: tabular-nums;
                     color: var(--text-muted); border: 1px solid var(--border-primary);
                     border-radius: 999px; padding: 0 6px; }
        .ab-dtab-owed { width: 6px; height: 6px; border-radius: 50%;
                        background: var(--trust-warn, #b45309); flex: none; }
        .ab-matrix { width: 100%; border-collapse: collapse; font-size: 12px; }
        .ab-matrix th, .ab-matrix td {
            text-align: left; vertical-align: top; padding: 7px 10px;
            border-bottom: 1px solid var(--border-secondary, rgba(148,163,184,0.18)); }
        .ab-matrix thead th {
            font-size: 10px; letter-spacing: .06em; text-transform: uppercase;
            color: var(--text-muted, #94a3b8); font-weight: 600; }
        .ab-prop { width: 190px; }
        .ab-prop-name { color: var(--text-primary, #e2e8f0); font-weight: 600; }
        .ab-cell { display: flex; flex-direction: column; gap: 2px; }
        .ab-cell--design em, .ab-cell--na { color: var(--text-muted, #94a3b8);
            font-style: italic; font-size: 11px; line-height: 1.35; }
        .ab-sub { color: var(--text-muted, #94a3b8); font-size: 10px; }
        .ab-pick { text-align: left; color: var(--text-primary, #e2e8f0);
            background: none; border: none; padding: 0; cursor: pointer;
            font-size: 12px; font-weight: 600; display: inline-flex; align-items: center;
            gap: 5px; }
        .ab-pick:hover, .ab-pick:focus-visible { text-decoration: underline; }
        .ab-cell--unbound .ab-pick { color: var(--trust-warn, #fbbf24); font-weight: 500; }
        .ab-mark { display: inline-flex; align-items: center; justify-content: center;
            width: 12px; height: 12px; border-radius: 3px; font-size: 8px; font-weight: 700;
            background: var(--text-primary, #e2e8f0); color: var(--card-bg, #0f172a);
            border: 1px solid var(--border-primary, rgba(148,163,184,0.35)); }
        .ab-tag { font-size: 9px; text-transform: uppercase; letter-spacing: .05em;
            padding: 1px 4px; border-radius: 3px; margin-left: 5px;
            color: var(--text-muted, #94a3b8);
            border: 1px solid var(--border-secondary, rgba(148,163,184,0.25)); }
        .ab-seal { font-size: 9px; padding: 1px 5px; border-radius: 999px;
            border: 1px solid currentColor; width: fit-content; }
        .ab-seal--pass { color: var(--trust-pass, #34d399); }
        .ab-seal--warn { color: var(--trust-warn, #fbbf24); }
        .ab-seal--danger { color: var(--trust-danger, #f87171); }
        .ab-note { font-size: 10px; line-height: 1.3; }
        .ab-note--warn { color: var(--trust-warn, #fbbf24); }
        .ab-note--ok { color: var(--trust-pass, #34d399); }
        .ab-note--danger { color: var(--trust-danger, #f87171); }
        .ab-cell--drift { border-left: 2px solid var(--trust-danger, #f87171);
            padding-left: 6px; margin-left: -6px; }
        .ab-sym-cell { width: 118px; }
        .ab-sym { font-size: 10px; color: var(--text-muted, #94a3b8);
            text-transform: uppercase; letter-spacing: .04em; }
        .ab-sym--shared { color: var(--trust-pass, #34d399); }
        .ab-sym--gap, .ab-sym--unbound { color: var(--trust-warn, #fbbf24); }
        .ab-empty { color: var(--text-muted, #94a3b8); font-size: 12px; }
        .ab-picker { position: absolute; z-index: 9999; width: 360px; max-height: 420px;
            overflow: auto; border-radius: 10px; padding: 10px;
            background: var(--card-bg, #111827);
            border: 1px solid var(--border-primary, rgba(148,163,184,0.35));
            box-shadow: 0 12px 34px rgba(0,0,0,0.45); }
        .ab-picker-head { font-size: 11px; color: var(--text-muted, #94a3b8);
            margin-bottom: 8px; }
        .ab-picker-load { font-size: 11px; color: var(--text-muted, #94a3b8); padding: 6px; }
        .ab-opt { display: block; width: 100%; text-align: left; padding: 7px 8px;
            border-radius: 7px; background: none; cursor: pointer; margin-bottom: 4px;
            border: 1px solid var(--border-secondary, rgba(148,163,184,0.2)); }
        .ab-opt:hover:not([disabled]) { background: var(--hover-bg-strong, rgba(148,163,184,0.12)); }
        .ab-opt[disabled] { cursor: not-allowed; opacity: .75; }
        .ab-opt-name { display: flex; align-items: center; gap: 5px; font-size: 12px;
            color: var(--text-primary, #e2e8f0); font-weight: 600; }
        .ab-opt-desc { display: block; font-size: 11px; line-height: 1.4; margin-top: 3px;
            color: var(--text-primary, #e2e8f0); opacity: .85; }
        .ab-opt-caps { display: block; font-size: 9px; color: var(--text-muted, #94a3b8);
            margin-top: 2px; }
        .ab-opt-why { display: block; font-size: 10px; margin-top: 4px; line-height: 1.35;
            color: var(--trust-warn, #fbbf24); }
        .ab-opt--current { border-color: var(--teradata-orange, #F15F22); }
        .ab-picker-foot { display: flex; flex-direction: column; gap: 4px; margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid var(--border-secondary, rgba(148,163,184,0.2)); }
        .ab-danger, .ab-plain, .ab-primary { font-size: 11px; padding: 6px 8px;
            border-radius: 6px; cursor: pointer; background: none;
            border: 1px solid var(--border-secondary, rgba(148,163,184,0.25));
            color: var(--text-primary, #e2e8f0); }
        .ab-danger { color: var(--trust-danger, #f87171); border-color: currentColor; }
        .ab-primary { background: var(--teradata-orange, #F15F22); color: #fff;
            border-color: var(--teradata-orange, #F15F22); }
        .ab-confirm { font-size: 12px; color: var(--text-primary, #e2e8f0); }
        .ab-from-to { margin-bottom: 6px; }
        .ab-warn { color: var(--trust-danger, #f87171); font-size: 11px; line-height: 1.4; }
        .ab-cons { color: var(--text-muted, #94a3b8); font-size: 11px; line-height: 1.4; }
        .ab-confirm-acts { display: flex; gap: 6px; justify-content: flex-end;
            margin-top: 10px; }
        .ab-err { margin-top: 8px; font-size: 11px; line-height: 1.4;
            color: var(--trust-danger, #f87171); }
        .ab-packs { display: flex; flex-direction: column; gap: 6px; }
        .ab-pack { display: flex; align-items: flex-start; justify-content: space-between;
            gap: 10px; padding: 8px 10px; border-radius: 8px;
            border: 1px solid var(--border-secondary, rgba(148,163,184,0.2)); }
        .ab-pack--skipped { opacity: .7; }
        .ab-pack-main { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
        .ab-pack-name { font-size: 12px; font-weight: 600;
            color: var(--text-primary, #e2e8f0); }
        `;
        document.head.appendChild(s);
    }

    window.AssuranceBindings = { mount: mount, refresh: refresh,
        applyStaged: applyStaged, isDirty: isDirty,
        _setViewForTest: _setViewForTest };
})();
