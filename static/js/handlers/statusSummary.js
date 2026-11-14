/**
 * statusSummary.js
 *
 * Collapses the six connection LEDs (MCP / LLM / CTX / KNW / CCR / SSE) into a
 * single "Status" indicator in the top bar.
 *
 * State rules (per design):
 *   - Base state mirrors the SSE LED (connected / disconnected / reconnecting …).
 *   - If ANY individual LED is busy (or pulsing), the summary is amber and rippling and
 *     reads Working — this takes priority over the SSE base state.
 *   - Otherwise, if any LED is reconnecting, the summary is amber and rippling and reads
 *     Reconnecting (a stream finding its way back is not work being done).
 *
 * The summary leads with a verdict word (Signed out · Connecting · Ready · Working ·
 * Reconnecting · Offline). Until the SSE stream reports, its dot is `pending` (the markup's
 * start state): with no session that reads Signed out — nothing to report, nothing to open —
 * and with one it reads Connecting. Offline is said only when the stream itself failed: the
 * platform answered the page, so a stream that has not started is not an outage.
 * Clicking the summary dot or the word reveals the full LED strip in
 * addition (inline, to the right of the summary). The individual LEDs keep their
 * original ids, so all existing status-setting code continues to drive them.
 *
 * Self-contained IIFE — no build step, no dependencies.
 */
(function () {
  'use strict';

  const DOT_IDS = [
    'mcp-status-dot', 'llm-status-dot', 'context-status-dot',
    'knowledge-status-dot', 'ccr-status-dot', 'sse-status-dot',
  ];
  // Amber states (see .connection-dot.* in main.css): busy reads Working, reconnecting reads
  // Reconnecting.

  function _dots() {
    return DOT_IDS.map(id => document.getElementById(id)).filter(Boolean);
  }

  // A LED is working if it is pulsing or busy; reconnecting is told apart (its own verdict).
  function _isWorking(dot) {
    return dot.classList.contains('pulsing') || dot.classList.contains('busy');
  }
  function _isReconnecting(dot) {
    return dot.classList.contains('reconnecting');
  }

  // The verdict the summary LEADS with — one word from the classes it already picks.
  // No session → 'signed-out' whatever the LEDs say (they belong to the user's agent);
  // 'working' = any LED busy/pulsing; otherwise the SSE state decides, and a stream that
  // has not reported yet (`pending`) is 'connecting'.
  const VERDICTS = {
    'signed-out': 'Signed out',
    connecting:   'Connecting',
    working:      'Working',
    reconnecting: 'Reconnecting',
    offline:      'Offline',
    ready:        'Ready',
    waiting:      'Waiting for you',
  };
  // PROP-011 — a question the agent put to the person is a READING, not an alarm (the
  // trust-status contract): quiet posture colour, never amber. Held per session; the
  // verdict shows it only while that session is the one on screen and nothing is working.
  const _waiting = new Set();
  function _viewedSession() { return (window.state && window.state.currentSessionId) || window.__currentSessionId || null; }
  function _isWaiting() { const s = _viewedSession(); return !!(s && _waiting.has(s)); }
  function _verdictFor(states) {
    if (states.includes('busy'))          return 'working';
    if (states.includes('reconnecting'))  return 'reconnecting';
    if (states.includes('disconnected'))  return 'offline';
    if (states.includes('connected'))     return 'ready';
    return 'connecting';
  }

  // A session = a stored token that has not expired (read-only — AuthClient owns clearing it).
  function _signedIn() {
    try {
      if (!localStorage.getItem('tda_auth_token')) return false;
      const exp = localStorage.getItem('tda_token_expiry');
      return !(exp && new Date(exp) < new Date());
    } catch (_) { return false; }
  }
  const TITLES = {
    'signed-out': 'Sign in to see the status of your agent',
    connecting:   'Opening the real-time connection',
    waiting:      'The agent stopped to ask you something — answer in the conversation',
  };
  const TITLE_DEFAULT = 'System status — click to show every indicator';
  function _writeVerdict(key) {
    const host = document.getElementById('status-summary');
    const word = document.getElementById('status-summary-word');
    if (host) {
      host.setAttribute('data-verdict', key);
      const title = TITLES[key] || TITLE_DEFAULT;
      if (host.title !== title) host.title = title;
      if (key === 'signed-out') {
        host.setAttribute('aria-disabled', 'true');
        const detail = document.getElementById('status-strip-detail');
        if (detail && detail.classList.contains('is-open')) {
          detail.classList.remove('is-open');
          host.setAttribute('aria-expanded', 'false');
        }
      } else host.removeAttribute('aria-disabled');
    }
    if (word && word.textContent !== VERDICTS[key]) word.textContent = VERDICTS[key];
  }

  function _update() {
    const summary = document.getElementById('status-summary-dot');
    if (!summary) return;

    if (!_signedIn()) {
      // No session: the LEDs describe nobody's agent yet — nothing to report.
      summary.className = 'connection-dot pending';
      _writeVerdict('signed-out');
      return;
    }
    if (_dots().some(_isWorking)) {
      // Any subsystem working → summary is amber and ripples.
      summary.className = 'connection-dot busy pulsing';
      _writeVerdict('working');
      return;
    }
    if (_dots().some(_isReconnecting)) {
      summary.className = 'connection-dot reconnecting pulsing';
      _writeVerdict('reconnecting');
      return;
    }
    // Otherwise inherit the SSE LED's state classes verbatim.
    const sse = document.getElementById('sse-status-dot');
    const states = sse ? [...sse.classList].filter(c => c !== 'connection-dot') : [];
    if (states.includes('connected') && _isWaiting()) {
      // a question waits for the person: a reading in the quiet posture colour, hollow
      summary.className = 'connection-dot connected waiting';
      _writeVerdict('waiting');
      return;
    }
    summary.className = 'connection-dot ' + (states.length ? states.join(' ') : 'pending');
    _writeVerdict(_verdictFor(states));
  }

  function _init() {
    const summary = document.getElementById('status-summary');
    const detail  = document.getElementById('status-strip-detail');
    if (summary && detail) {
      const toggle = () => {
        if (summary.getAttribute('data-verdict') === 'signed-out') return;   // nothing to open
        const shown = detail.classList.toggle('is-open');   // CSS animates max-width + fade
        summary.setAttribute('aria-expanded', shown ? 'true' : 'false');
      };
      summary.addEventListener('click', toggle);
      summary.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
    }

    _update();
    // Mirror state live: observe each LED's class attribute.
    const mo = new MutationObserver(_update);
    _dots().forEach(d => mo.observe(d, { attributes: true, attributeFilter: ['class'] }));
    // A sign-in or sign-out in another tab changes the session without touching a LED.
    window.addEventListener('storage', (e) => { if (!e.key || e.key === 'tda_auth_token') _update(); });
    // PROP-011 — the question card says when a turn stopped to ask and when it was answered.
    if (window.UderiaBus) {
      window.UderiaBus.on('uderia:input-required', (ev) => { const d = (ev && ev.detail) || {}; if (d.session_id) { _waiting.add(d.session_id); _update(); } });
      window.UderiaBus.on('uderia:input-answered', (ev) => { const d = (ev && ev.detail) || {}; if (d.session_id) { _waiting.delete(d.session_id); _update(); } });
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();
})();
