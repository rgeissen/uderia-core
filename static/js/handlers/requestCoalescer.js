/**
 * In-flight request coalescing.
 *
 * WHY THIS EXISTS: the login path fans out to several independent panels — Skills,
 * Components, Workspace, Context, Knowledge Graphs — and each fetches what it needs.
 * Several of them need the SAME thing, so the same URL is requested two or three times
 * within a few milliseconds. Measured on production: /profiles/<id>/skills 5x,
 * /components 4x, /knowledge-graph/list 4x. Every duplicate is a full round trip on a
 * link with ~1.3s of latency, and the server resolves each one from scratch.
 *
 * WHAT THIS IS NOT: a cache. Nothing is retained after a request settles, so a later
 * call always hits the network and can never read stale data. The ONLY thing merged is
 * requests that genuinely overlap in time — and two callers issuing the same GET in the
 * same instant would have received the same bytes anyway. That makes this semantically
 * invisible, which is what lets it be applied broadly without auditing every caller.
 *
 * THE DETAIL THAT MATTERS: a Response body can be read only once. Every caller
 * therefore receives its OWN clone; the shared original is never handed out. Without
 * that the second caller gets a consumed stream and silently fails.
 *
 * GET only by default. A POST may be coalesced but must opt in explicitly at the call
 * site, with a stated reason why re-sending it is equivalent (see testProfile).
 */

const _inflight = new Map();   // key -> Promise<Response>

function _key(url, init) {
  const m = (init?.method || 'GET').toUpperCase();
  // The auth token is part of the identity of the response: two users must never share
  // one in-flight request.
  const auth = init?.headers?.Authorization || init?.headers?.authorization || '';
  return `${m} ${url} ${auth}`;
}

/**
 * @param {string} url
 * @param {object} [init]      standard fetch init
 * @param {object} [opts]
 * @param {boolean} [opts.coalescePost=false]  opt in for a POST that is safe to merge
 * @returns {Promise<Response>} a private clone, safe to consume
 */
export function coalescedFetch(url, init, opts = {}) {
  const method = (init?.method || 'GET').toUpperCase();
  const mergeable = method === 'GET' || (method === 'POST' && opts.coalescePost === true);
  if (!mergeable) return fetch(url, init);

  const key = _key(url, init);
  let shared = _inflight.get(key);
  if (!shared) {
    shared = fetch(url, init).finally(() => {
      // Release only if the slot is still ours — a newer request may already own it.
      if (_inflight.get(key) === shared) _inflight.delete(key);
    });
    _inflight.set(key, shared);
  }
  // Each caller consumes its own clone; the shared response is never handed out.
  return shared.then((r) => r.clone());
}

/** Test/diagnostic only: how many requests are currently in flight. */
export function inflightCount() { return _inflight.size; }

if (typeof window !== 'undefined') {
  window.coalescedFetch = coalescedFetch;   // for the non-module (inline) handlers
}
