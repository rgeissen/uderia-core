/*
 * Uderia evidence verifier — the browser twin of scripts/verify_evidence.py (PROP-009 §4.4).
 *
 * Checks an exported evidence bundle with WebCrypto alone (Ed25519 + SHA-256), nothing asked
 * of any server:
 *   attestations  the Ed25519 signature over "<kind>|<version_hash>" with the carried key; that
 *                 the key's fingerprint is the one the record names; and, when the content was
 *                 included, that sha256(content) equals the signed version hash.
 *   answers       every provenance-chain step's hash, link and signature; root, tip and step
 *                 count; and that the sealed "trust.bom" step is the digest of the bill of
 *                 materials the bundle carries.
 *
 * The bill of materials is re-encoded exactly as Python's json.dumps(sort_keys=True,
 * separators=(",", ":")) does (ASCII escapes, code-point key order). Numbers are taken from the
 * bundle TEXT, never re-formatted, so a float like 1.0 stays "1.0" — pass the bundle as text.
 *
 * Who signed it: the bundle carries its keys, so alone it proves the record is consistent.
 * A fingerprint supplied separately (from the deployment's operator) proves who signed it.
 *
 * This file is inlined verbatim into static/trust/verify-evidence.html (a gate asserts the
 * two are identical) so that page works offline as a single file.
 */
(function (root) {
  'use strict';

  var FORMAT = 'uderia.evidence.bundle';
  var GENESIS = '0000000000000000000000000000000000000000000000000000000000000000';

  // ── JSON that keeps every number's original text ──────────────────────────
  function Num(raw) { this.raw = raw; }
  Num.prototype.valueOf = function () { return Number(this.raw); };
  Num.prototype.toString = function () { return this.raw; };

  function parseRaw(text) {
    var i = 0;
    function ws() { while (i < text.length && ' \t\n\r'.indexOf(text[i]) >= 0) i++; }
    function val() {
      ws();
      var c = text[i];
      if (c === '{') {
        i++; var o = {}; ws();
        if (text[i] === '}') { i++; return o; }
        for (;;) {
          ws(); var k = str(); ws(); if (text[i++] !== ':') throw new Error('bad JSON');
          o[k] = val(); ws();
          if (text[i] === ',') { i++; continue; }
          if (text[i] === '}') { i++; return o; }
          throw new Error('bad JSON');
        }
      }
      if (c === '[') {
        i++; var a = []; ws();
        if (text[i] === ']') { i++; return a; }
        for (;;) {
          a.push(val()); ws();
          if (text[i] === ',') { i++; continue; }
          if (text[i] === ']') { i++; return a; }
          throw new Error('bad JSON');
        }
      }
      if (c === '"') return str();
      if (text.startsWith('true', i)) { i += 4; return true; }
      if (text.startsWith('false', i)) { i += 5; return false; }
      if (text.startsWith('null', i)) { i += 4; return null; }
      var m = /^-?\d+(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i, i + 64));
      if (!m) throw new Error('bad JSON at ' + i);
      i += m[0].length;
      return new Num(m[0]);
    }
    function str() {
      if (text[i] !== '"') throw new Error('bad JSON string');
      var j = i + 1, esc = false;
      while (j < text.length) {
        var ch = text[j];
        if (esc) esc = false; else if (ch === '\\') esc = true; else if (ch === '"') break;
        j++;
      }
      var s = JSON.parse(text.slice(i, j + 1));
      i = j + 1;
      return s;
    }
    var v = val(); ws();
    if (i !== text.length) throw new Error('trailing data');
    return v;
  }

  // Plain values (numbers as JS numbers) for everything that is not re-encoded.
  function plain(v) {
    if (v instanceof Num) return Number(v.raw);
    if (Array.isArray(v)) return v.map(plain);
    if (v && typeof v === 'object') {
      var o = {}; Object.keys(v).forEach(function (k) { o[k] = plain(v[k]); }); return o;
    }
    return v;
  }

  // ── Python json.dumps(sort_keys=True, separators=(",", ":")) ───────────────
  function pyStr(s, unicode) {
    var out = '"';
    for (var k = 0; k < s.length; k++) {
      var c = s.charCodeAt(k);
      if (c === 0x22) out += '\\"';
      else if (c === 0x5c) out += '\\\\';
      else if (c === 0x0a) out += '\\n';
      else if (c === 0x0d) out += '\\r';
      else if (c === 0x09) out += '\\t';
      else if (c === 0x08) out += '\\b';
      else if (c === 0x0c) out += '\\f';
      else if (c < 0x20 || (!unicode && c > 0x7e && c !== 0x7f)) out += '\\u' + ('0000' + c.toString(16)).slice(-4);
      else out += s[k];
    }
    return out + '"';
  }
  function cpCompare(a, b) {
    var x = Array.from(a), y = Array.from(b);
    for (var k = 0; k < Math.min(x.length, y.length); k++) {
      var d = x[k].codePointAt(0) - y[k].codePointAt(0);
      if (d) return d;
    }
    return x.length - y.length;
  }
  // ``unicode`` = ensure_ascii=False (the audit pack); default is Python's ASCII escaping.
  function pyDumps(v, unicode) {
    if (v === null || v === undefined) return 'null';
    if (v === true) return 'true';
    if (v === false) return 'false';
    if (v instanceof Num) return v.raw;
    if (typeof v === 'number') return String(v);
    if (typeof v === 'string') return pyStr(v, unicode);
    if (Array.isArray(v)) return '[' + v.map(function (x) { return pyDumps(x, unicode); }).join(',') + ']';
    var keys = Object.keys(v).sort(cpCompare);
    return '{' + keys.map(function (k) { return pyStr(k, unicode) + ':' + pyDumps(v[k], unicode); }).join(',') + '}';
  }
  function clean(v) {
    if (Array.isArray(v)) return v.map(clean);
    if (v && typeof v === 'object' && !(v instanceof Num)) {
      var o = {};
      Object.keys(v).forEach(function (k) { if (k.charAt(0) !== '_') o[k] = clean(v[k]); });
      return o;
    }
    return v;
  }
  function bomCanonical(bom) { return pyDumps(clean(bom)); }

  // ── crypto ────────────────────────────────────────────────────────────────
  var subtle = (root.crypto && root.crypto.subtle) || null;
  function hex(buf) {
    return Array.from(new Uint8Array(buf)).map(function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  async function sha256(s) {
    return hex(await subtle.digest('SHA-256', new TextEncoder().encode(s)));
  }
  function b64bytes(b64) {
    var bin = atob(b64), u = new Uint8Array(bin.length);
    for (var k = 0; k < bin.length; k++) u[k] = bin.charCodeAt(k);
    return u;
  }
  var keyCache = new Map();
  async function loadKey(pem) {
    if (!pem) return null;
    if (keyCache.has(pem)) return keyCache.get(pem);
    var der = b64bytes(String(pem).replace(/-----[^-]+-----/g, '').replace(/\s+/g, ''));
    // An Ed25519 SPKI is 44 bytes; the raw key is its last 32 — what the fingerprint hashes.
    var raw = der.slice(der.length - 32);
    var key = await subtle.importKey('spki', der, { name: 'Ed25519' }, false, ['verify']);
    var out = { key: key, fingerprint: hex(await subtle.digest('SHA-256', raw)) };
    keyCache.set(pem, out);
    return out;
  }
  async function verifySig(key, sigB64, message) {
    try {
      return await subtle.verify({ name: 'Ed25519' }, key, b64bytes(sigB64), new TextEncoder().encode(message));
    } catch (e) { return false; }
  }

  async function bomStepContent(bom) {
    var n = 0;
    ['agent', 'deployment'].forEach(function (k) { if (bom[k] && typeof bom[k] === 'object') n++; });
    (bom.parts || []).forEach(function (p) { if (p && typeof p === 'object') n++; });
    return 'bom:v1:' + (await sha256(bomCanonical(bom))) + ':' + n;
  }

  // ── checks ────────────────────────────────────────────────────────────────
  async function checkAttestation(a, trustedFp) {
    var out = { kind: a.kind, artifact_id: a.artifact_id, label: a.label, ok: false, notes: [] };
    if (!a.signed || !a.signature) {
      out.ok = null; out.notes.push('recorded without a signature — nothing to verify'); return out;
    }
    var k;
    try { k = await loadKey(a.signer_public_key); } catch (e) { k = null; }
    if (!k) { out.notes.push('the bundle carries no readable public key for this signature'); return out; }
    if (a.signer_fingerprint && k.fingerprint !== a.signer_fingerprint) {
      out.notes.push('the carried key is not the key the record names'); return out;
    }
    var payloads = [a.kind + '|' + a.version_hash];
    if (a.legacy_payload) payloads.push(a.legacy_payload);
    var good = false;
    for (var p = 0; p < payloads.length && !good; p++) good = await verifySig(k.key, a.signature, payloads[p]);
    if (!good) { out.notes.push('the signature does not verify'); return out; }
    var c = a.content || {};
    if (c.included) {
      if ((await sha256(c.data || '')) !== a.version_hash) {
        out.notes.push('the included content does NOT hash to the signed version'); return out;
      }
      out.notes.push('signature verifies and the content hashes to the signed version');
    } else {
      out.notes.push('signature verifies; content not included' + (c.reason ? ' (' + c.reason + ')' : ''));
    }
    out.signer_confirmed = trustedFp ? trustedFp === k.fingerprint : null;
    if (trustedFp && trustedFp !== k.fingerprint) {
      out.notes.push('signed by a key OTHER than the fingerprint you supplied'); return out;
    }
    out.ok = true;
    return out;
  }

  // The chain seals the question (query_intake) and the answer (turn_complete) as the SHA-256
  // of their first CONTENT_MAX_LEN characters (Python code points, not UTF-16 units). An
  // exported text is checked against them, never only shown: an edited answer must not
  // read as verified.
  var CONTENT_MAX_LEN = 4096;
  async function checkReceiptText(receipt, chain, notes) {
    if (!receipt || typeof receipt !== 'object') return [];
    var errors = [], matched = [];
    var fields = [['user_query', 'query_intake', 'question'], ['answer', 'turn_complete', 'answer']];
    for (var i = 0; i < fields.length; i++) {
      var text = receipt[fields[i][0]], label = fields[i][2];
      var step = chain.filter(function (x) { return x.step_type === fields[i][1]; })[0];
      if (typeof text !== 'string' || !step) continue;
      if (fields[i][0] === 'answer' && receipt.answer_not_sealed) {
        notes.push('the answer text is NOT checked: ' + receipt.answer_not_sealed); continue;
      }
      var cps = Array.from(text);
      if ((await sha256(cps.slice(0, CONTENT_MAX_LEN).join(''))) !== step.content_hash) {
        errors.push('the ' + label + ' text does not match what was sealed'); continue;
      }
      matched.push(label);
      if (cps.length > CONTENT_MAX_LEN) notes.push('only the first ' + CONTENT_MAX_LEN + ' characters of the ' + label + ' are sealed and checked');
    }
    if (matched.length) notes.push('the ' + matched.join(' and ') + ' text ' + (matched.length > 1 ? 'match' : 'matches') + ' what was sealed');
    return errors;
  }

  async function checkTurn(t, rawBom, keys, trustedFp) {
    var out = { session_id: t.session_id, turn: t.turn, ok: false, notes: [] };
    var chain = t.provenance_chain || [], meta = t.provenance_meta || {};
    if (!chain.length) { out.ok = null; out.notes.push('this answer carries no sealed chain'); return out; }
    var fp = meta.key_fingerprint, pem = keys[fp];
    if (!pem) { out.notes.push('the bundle carries no public key for this chain'); return out; }
    var k = await loadKey(pem);
    if (k.fingerprint !== fp) { out.notes.push('the carried key is not the key the chain names'); return out; }
    var prev = meta.previous_turn_tip_hash || GENESIS, errors = [];
    for (var n = 0; n < chain.length; n++) {
      var s = chain[n];
      var expectPrev = n === 0 ? prev : chain[n - 1].chain_hash;
      if (s.previous_hash !== expectPrev) errors.push('step ' + n + ': broken link');
      var h = await sha256(s.step_index + ':' + s.step_type + ':' + s.content_hash + ':' + s.previous_hash);
      if (s.chain_hash !== h) errors.push('step ' + n + ': hash mismatch');
      if (!s.signature || !(await verifySig(k.key, s.signature, s.chain_hash || ''))) {
        errors.push('step ' + n + ': signature does not verify');
      }
    }
    if (meta.chain_tip_hash !== chain[chain.length - 1].chain_hash) errors.push('tip does not match the last step');
    if (meta.chain_root_hash !== chain[0].chain_hash) errors.push('root does not match the first step');
    if (meta.step_count !== chain.length) errors.push('step count does not match');
    var bomSteps = chain.filter(function (x) { return x.step_type === 'trust.bom'; });
    if (bomSteps.length) {
      if (!rawBom || typeof rawBom !== 'object') errors.push('a bill of materials was sealed but the bundle carries none');
      else if ((await sha256(await bomStepContent(rawBom))) !== bomSteps[0].content_hash) {
        errors.push('the bill of materials does not match what was sealed');
      } else out.notes.push('the bill of materials matches what was sealed');
    }
    errors = errors.concat(await checkReceiptText(t.receipt, chain, out.notes));
    if (errors.length) { out.notes = out.notes.concat(errors); return out; }
    out.notes.unshift('chain of ' + chain.length + ' steps verifies');
    if (trustedFp && trustedFp !== fp) { out.notes.push('signed by a key OTHER than the fingerprint you supplied'); return out; }
    out.signer_confirmed = trustedFp ? trustedFp === fp : null;
    out.ok = true;
    return out;
  }

  async function checkAuditPack(rawPack, att, keys, trustedFp) {
    var out = { ok: false, notes: [] };
    if (!att || !att.signed || !att.signature) { out.ok = null; out.notes.push('the audit pack is not signed'); return out; }
    if ((await sha256(pyDumps(rawPack, true))) !== att.content_sha256) {
      out.notes.push('the audit pack does NOT hash to the signed content hash'); return out;
    }
    var pem = keys[att.key_fingerprint];
    if (!pem) { out.notes.push('the bundle carries no public key for the audit pack'); return out; }
    var k = await loadKey(pem);
    if (k.fingerprint !== att.key_fingerprint || !(await verifySig(k.key, att.signature, att.content_sha256))) {
      out.notes.push('the audit pack signature does not verify'); return out;
    }
    if (trustedFp && trustedFp !== att.key_fingerprint) { out.notes.push('signed by a key OTHER than the fingerprint you supplied'); return out; }
    out.notes.push('the audit pack hashes to its signed content hash and the signature verifies');
    out.ok = true;
    return out;
  }

  var LEDGER = 'ledger:v1';
  function entryHash(e) {
    return sha256(LEDGER + ':' + e.seq + ':' + e.entry_type + ':' + e.ref + ':' + e.digest + ':' + e.prev_hash);
  }

  async function checkLedger(raw, b, keys, trustedFp) {
    var out = { ok: false, notes: [] };
    var led = b.ledger || {}, cp = led.checkpoint || {};
    if (!cp.signed || !cp.signature) { out.ok = null; out.notes.push('the ledger checkpoint is not signed'); return out; }
    var pem = keys[cp.key_fingerprint];
    if (!pem) { out.notes.push('the bundle carries no public key for the ledger checkpoint'); return out; }
    var k = await loadKey(pem);
    var payload = LEDGER + ':checkpoint:' + cp.seq + ':' + cp.tip_hash;
    if (k.fingerprint !== cp.key_fingerprint || !(await verifySig(k.key, cp.signature, payload))) {
      out.notes.push('the ledger checkpoint signature does not verify'); return out;
    }
    var errors = [], entries = led.entries || [], byRef = {};
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      if ((await entryHash(e)) !== e.entry_hash) errors.push('ledger entry ' + e.seq + ': hash does not recompute');
      if (led.complete_segment && i) {
        var p = entries[i - 1];
        if (e.seq !== p.seq + 1 || e.prev_hash !== p.entry_hash) errors.push('ledger entry ' + e.seq + ': gap or broken link');
      }
      byRef[e.ref] = e;
    }
    if (led.complete_segment && entries.length) {
      var last = entries[entries.length - 1];
      if (last.seq !== cp.seq || last.entry_hash !== cp.tip_hash) errors.push('the ledger segment does not end at the signed checkpoint');
    } else if (!led.complete_segment) {
      out.notes.push('chain to the checkpoint not carried — each record\'s own entry checked');
    }
    var covered = 0, ra = raw.attestations || [];
    for (var j = 0; j < (b.attestations || []).length; j++) {
      var a = b.attestations[j], rec = (ra[j] || {}).ledger_record;
      if (!a.ledger_record || !a.history_id) continue;
      var en = byRef['h:' + a.history_id], name = a.kind + ' ' + (a.label || a.artifact_id);
      if (!en) { errors.push(name + ': not in the ledger'); continue; }
      var lr = a.ledger_record;
      var same = lr.kind === a.kind && String(lr.artifact_id) === String(a.artifact_id)
        && lr.version_hash === a.version_hash && (lr.signature || null) === (a.signature || null);
      if (!same || (await sha256(pyDumps(rec))) !== en.digest) { errors.push(name + ': differs from what the ledger recorded'); continue; }
      covered++;
    }
    var rt = raw.turns || [];
    for (var n = 0; n < (b.turns || []).length; n++) {
      var t = b.turns[n];
      if (!t.ledger_ref) continue;
      var et = byRef[t.ledger_ref], tname = 'answer ' + t.session_id + ' #' + t.turn;
      if (!et) { errors.push(tname + ': not in the ledger'); continue; }
      var tip = (t.provenance_meta || {}).chain_tip_hash || '';
      var rb = (rt[n] || {}).bom;
      var bd = rb && typeof rb === 'object' ? await sha256(bomCanonical(rb)) : '';
      if ((await sha256('turn:v1:' + tip + ':' + bd)) !== et.digest) { errors.push(tname + ': differs from what the ledger recorded'); continue; }
      covered++;
    }
    if (errors.length) { out.notes = out.notes.concat(errors); return out; }
    if (trustedFp && trustedFp !== cp.key_fingerprint) { out.notes.push('signed by a key OTHER than the fingerprint you supplied'); return out; }
    out.notes.unshift(covered + ' record(s) match their ledger entries under the signed checkpoint at entry ' + cp.seq);
    out.ok = true;
    return out;
  }

  /**
   * verifyBundle(text, trustedFingerprint?) → {attestations, turns, checked, failed, ok,
   * signer_confirmed}. ``text`` is the bundle as exported (a string); an already-parsed
   * object is accepted, but then a bill of materials containing integral floats may not
   * re-encode exactly — pass text.
   */
  async function verifyBundle(input, trustedFp) {
    if (!subtle) throw new Error('this browser has no WebCrypto');
    var raw = typeof input === 'string' ? parseRaw(input) : input;
    var b = plain(raw);
    if (!b || b.format !== FORMAT) throw new Error('not an Uderia evidence bundle');
    trustedFp = (trustedFp || '').trim().toLowerCase() || null;
    var keys = {};
    (b.keys || []).forEach(function (k) { if (k && k.fingerprint) keys[k.fingerprint] = k.public_key_pem; });
    var atts = [];
    for (var a = 0; a < (b.attestations || []).length; a++) atts.push(await checkAttestation(b.attestations[a], trustedFp));
    var turns = [];
    for (var t = 0; t < (b.turns || []).length; t++) {
      turns.push(await checkTurn(b.turns[t], ((raw.turns || [])[t] || {}).bom, keys, trustedFp));
    }
    var audit = Object.prototype.hasOwnProperty.call(b, 'audit_pack')
      ? await checkAuditPack(raw.audit_pack, b.audit_pack_attestation, keys, trustedFp) : null;
    var ledger = b.ledger ? await checkLedger(raw, b, keys, trustedFp) : null;
    var checked = atts.concat(turns, audit ? [audit] : [], ledger ? [ledger] : []).filter(function (r) { return r.ok !== null; });
    var failed = checked.filter(function (r) { return r.ok === false; });
    return { attestations: atts, turns: turns, audit_pack: audit, ledger: ledger, checked: checked.length, failed: failed.length,
             signer_confirmed: !!trustedFp && !failed.length, ok: !failed.length };
  }

  var api = { verifyBundle: verifyBundle, parseRaw: parseRaw, bomCanonical: bomCanonical,
              checkReceiptText: checkReceiptText,
              pyDumps: pyDumps, FORMAT: FORMAT };
  root.EvidenceVerify = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
