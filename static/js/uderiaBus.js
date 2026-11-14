/**
 * UderiaBus — the one page bus components talk over (Oct 2026).
 *
 * ONE target (document), ONE prefix (`uderia:`), ONE registry of names below. A renderer, a
 * handler, a panel emits with `UderiaBus.emit(name, detail)` and listens with
 * `UderiaBus.on(name, fn)`; nothing dispatches a CustomEvent on document or window itself, so a
 * name cannot drift between its emitter and its listener (before this registry three events
 * fired into the void and one listener waited for an event nobody sent — five naming styles,
 * two targets, no gate).
 *
 * A change to what a component KEEPS is one event for every component, `uderia:component-changed`
 * `{component_id, action, ids, aspect, session_id, profile_id, origin}`: the server raises it at
 * the store's write seam (notifications.js relays the `component_changed` notice, origin
 * 'server'), a page writer may raise it for immediacy (origin 'page'); `onComponent(id, fn)`
 * subscribes to one component (or '*'), and the same fact from the other origin within DEDUPE_MS —
 * the page's own announcement followed by the server's echo — reaches a listener once.
 *
 * Own or elsewhere: every tab has a CLIENT_ID, stamped on every same-origin fetch as
 * `X-Uderia-Client` (auth.js), carried back on the notice as `client`; the bus marks the event
 * `own` when it is this page's announcement or the echo of this page's own write. A panel that
 * holds what the user is editing (the canvas, the flow, the dashboard's layout) reads `own` to
 * tell its own save from a write made elsewhere — another tab, a REST script, a scheduled run —
 * instead of ignoring every notice. The 400 ms dedupe stays as the second line; `own` is the rule.
 *
 * Classic script, loaded in <head> before every module and classic handler (the bus must exist
 * when a classic handler wires its listeners at parse time); ES modules use the global.
 * Contract: src/trusted_data_agent/components/signals.py. Gate: test/test_component_bus.mjs.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || window.UderiaBus) return;

    /** Every event on the bus: its detail fields and the fact it carries. `public` marks a hook
     *  raised for whatever follows it (extensions, a later panel) — the gate asks no listener of it. */
    const EVENTS = Object.freeze({
        // ── what a component keeps changed (server seam or page writer) ──
        'uderia:component-changed':        { detail: ['component_id', 'action', 'ids', 'aspect', 'session_id', 'profile_id', 'origin', 'client', 'own'], note: 'something a component keeps changed — the one change event for every component; own = this page\'s announcement or the echo of its own write' },
        // ── the agent / its configuration ──
        'uderia:profile-config-changed':   { detail: ['profileId', 'aspect'], note: 'an agent\'s configuration changed (server echo or page writer); the resource panel reloads' },
        'uderia:components-changed':       { detail: ['profileId'], note: 'an agent\'s component toggles changed (the older spelling of a profile-config change)' },
        'uderia:profile-modified':         { detail: ['profileId', 'action'], note: 'the profile list changed: created · updated · deleted · activated' },
        'uderia:default-profile-changed':  { detail: ['profileId'], note: 'another agent is the default' },
        'uderia:profile-selection-changed':{ detail: ['profileId', 'source'], note: 'the composer\'s selected agent changed' },
        'uderia:effective-profile':        { detail: ['profileId', 'profile'], note: 'the agent that will answer the next turn (selection or @TAG)' },
        'uderia:trust-changed':            { detail: ['profileId', 'state'], note: 'an agent\'s trust state changed (signed, attested, withdrawn)' },
        // ── the turn ──
        'uderia:turn-complete':            { detail: ['session_id'], note: 'a turn finished (any lane); panels that list what a turn may have created refresh', public: true },
        'uderia:turn-scored':              { detail: ['session_id', 'turn'], note: 'a turn\'s scores landed' },
        'uderia:answer-verdict':           { detail: ['sid', 'turnId', 'verdict'], note: 'the Living Answer\'s verdict changed' },
        'uderia:review-decision':          { detail: ['session_id', 'turn_id', 'decision'], note: 'an owner decided a parked answer' },
        'uderia:input-required':           { detail: ['session_id', 'request_id'], note: 'a turn stopped to ask the person something (PROP-011); the status strip reads Waiting for you' },
        'uderia:input-answered':           { detail: ['session_id', 'request_id', 'decision'], note: 'the person answered, declined, dismissed or moved past a pending question' },
        'uderia:trust-event':              { detail: ['event_type', 'summary', 'session_id', 'profile_tag', 'severity'], note: 'a trust event (the assurance inbox, the dial)' },
        'uderia:action-performed':         { detail: ['action', 'outcome', 'session_id', 'record'], note: 'an OBDA-ACT action ran' },
        'uderia:session-title-renamed':    { detail: ['sessionId', 'title'], note: 'the active conversation was renamed' },
        // ── panels, cards and views ──
        'uderia:panel-state':              { detail: ['component', 'id'], note: 'a side panel opened, switched item or closed (id null)', public: true },
        'uderia:open-govern':              { detail: ['kgId', 'kgName'], note: 'open the graph governance dialog' },
        'uderia:open-genie-split-view':    { detail: ['sessionId', 'turnId'], note: 'open the coordinator\'s split view' },
        'uderia:canvas-split-mode-changed':{ detail: ['on'], note: 'the canvas split mode toggled' },
        'uderia:scheduler-background-run': { detail: [], note: 'a scheduled task ran in the background' },
        'uderia:filterable-added':         { detail: [], note: 'a filterable station entered the dial' },
        'uderia:config-saved':             { detail: ['section'], note: 'an App Config section was saved' },
        // ── knowledge graph (page state, not writes) ──
        'uderia:kg-rendered':              { detail: ['container'], note: 'the graph finished drawing' },
        'uderia:kg-node-select':           { detail: ['node', 'spec'], note: 'a node is selected' },
        'uderia:kg-node-deselect':         { detail: [], note: 'the selection cleared' },
        'uderia:kg-node-dblclick':         { detail: ['node', 'spec'], note: 'a node was double-clicked (open it)' },
        // ── flow canvas (page state) ──
        'uderia:flow-patch':               { detail: ['flow_id', 'patch'], note: 'a patch for the open flow' },
        'uderia:flow-run-event':           { detail: ['flow_id', 'run_id', 'type', 'payload'], note: 'a flow run event (relayed server notice)' },
        'uderia:flow-fix-node':            { detail: ['node_id', 'node'], note: 'ask the agent to fix a node' },
        'uderia:flow-node-extensions-save':{ detail: ['node_id', 'extensions'], note: 'a node\'s extensions were edited' },
        'uderia:flow-scope-rounds':        { detail: ['scope_id', 'rounds'], note: 'a scope\'s rounds changed' },
        'uderia:flow-scope-edit':          { detail: ['scope_id'], note: 'edit a scope' },
        'uderia:flow-open-canvas':         { detail: ['canvas_id', 'title'], note: 'a flow node opens its canvas' },
        'uderia:flow-open-file':           { detail: ['file_id', 'name'], note: 'a flow node opens its file' },
    });

    // resolved per call: a test harness installs its document stub after this script loads
    const target = () => document;
    const DEDUPE_MS = 400;

    /** This tab's id — kept for the tab's life (sessionStorage), new per tab. auth.js stamps it
     *  on every same-origin fetch as `X-Uderia-Client`; the server carries it back on the notice. */
    const CLIENT_ID = (() => {
        const make = () => 'c' + Math.random().toString(16).slice(2, 10) + Date.now().toString(16).slice(-6);
        try {
            const kept = window.sessionStorage && sessionStorage.getItem('uderia_client');
            if (kept) return kept;
            const id = make();
            if (window.sessionStorage) sessionStorage.setItem('uderia_client', id);
            return id;
        } catch (_) { return make(); }
    })();
    const _recent = new Map();   // dedupe key → timestamp (component-changed only)
    const _warned = new Set();

    function _check(name, verb) {
        if (EVENTS[name]) return true;
        if (!_warned.has(name)) {
            _warned.add(name);
            console.warn(`[UderiaBus] ${verb} of an unregistered event '${name}' — register it in static/js/uderiaBus.js`);
        }
        return false;
    }

    function _dedupeKey(d) {
        return [d.component_id, d.action, d.aspect || '', (d.ids || []).join(',')].join('|');
    }

    /** Emit `name` with `detail`. Returns false when a repeat of the same component change was
     *  swallowed (the page's own announcement, then the server's echo). Never throws. */
    function emit(name, detail) {
        _check(name, 'emit');
        const d = detail || {};
        try {
            if (name === 'uderia:component-changed') {
                // own: this page announced it, or the server echoes a write this page made
                d.own = (d.origin || 'page') === 'page' || (!!d.client && d.client === CLIENT_ID);
            }
            if (name === 'uderia:component-changed' && Array.isArray(d.ids) && d.ids.length) {
                // only a change that names its ids can be recognised as a repeat (the server echo
                // of a page announcement); two id-less announcements may be two different facts
                // …and only across origins: the page's announcement followed by the server's echo
                // (or the echo first). Two page announcements, or two server writes, are two facts.
                const key = _dedupeKey(d);
                const now = Date.now();
                const last = _recent.get(key);
                if (last && now - last.t < DEDUPE_MS && last.origin !== (d.origin || 'page')) return false;
                _recent.set(key, { t: now, origin: d.origin || 'page' });
                if (_recent.size > 200) for (const k of _recent.keys()) { if (now - _recent.get(k).t > DEDUPE_MS) _recent.delete(k); }
            }
            target().dispatchEvent(new CustomEvent(name, { detail: d }));
            return true;
        } catch (err) {
            console.error(`[UderiaBus] emit '${name}' failed:`, err);
            return false;
        }
    }

    /** Listen with DOM semantics — `fn(event)`, the payload in `event.detail` — registered as-is,
     *  so `off(name, fn)` removes it. Returns the unsubscribe function. `opts` as on addEventListener. */
    function on(name, fn, opts) {
        _check(name, 'listen');
        target().addEventListener(name, fn, opts);
        return () => target().removeEventListener(name, fn, opts);
    }

    function off(name, fn, opts) { target().removeEventListener(name, fn, opts); }

    function once(name, fn) { return on(name, fn, { once: true }); }

    /** A component's changes — `componentId` or '*' (every component); `fn(event)` with the
     *  change in `event.detail`. Returns the unsubscribe function (the handler is wrapped). */
    function onComponent(componentId, fn, opts) {
        const wrapped = (e) => {
            const d = (e && e.detail) || {};
            if (componentId === '*' || !componentId || d.component_id === componentId) fn(e);
        };
        return on('uderia:component-changed', wrapped, opts);
    }

    /** A page writer's own announcement of a change it just made (origin 'page'). The server's
     *  echo of the same write arrives within DEDUPE_MS and is swallowed. */
    function announce(componentId, action, ids, extra) {
        return emit('uderia:component-changed', Object.assign(
            { component_id: componentId, action: action || 'changed', ids: ids || [], aspect: null,
              session_id: null, profile_id: null, origin: 'page' }, extra || {}));
    }

    window.UderiaBus = Object.freeze({ EVENTS, emit, on, off, once, onComponent, announce, DEDUPE_MS, CLIENT_ID });
})();
