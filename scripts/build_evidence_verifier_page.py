#!/usr/bin/env python3
"""Build static/trust/verify-evidence.html — the offline evidence verifier page (PROP-009 §4.4).

The page inlines static/js/evidenceVerify.js VERBATIM (between the EVIDENCE-VERIFY markers) so
it works as one file, opened from disk, with no network. test/test_evidence_bundle.py asserts
the inline copy equals the module, so the page can never verify by different rules.

    python scripts/build_evidence_verifier_page.py          # write
    python scripts/build_evidence_verifier_page.py --check  # exit 1 when the page is stale
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = ROOT / "static" / "js" / "evidenceVerify.js"
PAGE = ROOT / "static" / "trust" / "verify-evidence.html"
BEGIN, END = "/* EVIDENCE-VERIFY:BEGIN */\n", "\n/* EVIDENCE-VERIFY:END */"
# Story pages that run the verifier in place (the reader verifies a real sample on the page).
# Their inline copy is kept equal to the module the same way; --check covers them too.
STORIES = [ROOT / "static" / "trust" / "the-anatomy-of-an-answers-evidence.html"]

TEMPLATE = r"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evidence Verifier</title>
<meta name="description" content="Verify an Uderia evidence bundle in this browser, offline — signatures, signed content, sealed chains and bills of materials.">
<style>
  :root {
    --ground: #0e1621; --panel: #172230; --hair: #2c3c4e;
    --text: #e9eff5; --muted: #93a4b6; --dim: #6b7d90;
    --pass: #6cbf92; --warn: #e0b45a; --danger: #e07a6a; --accent: #F15F22;
    --accent-line: rgba(241,95,34,0.45);
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) {
    --ground: #f6f8fa; --panel: #ffffff; --hair: #d5dde5; --text: #14202c; --muted: #4c5d6e; --dim: #6b7d90;
    --pass: #1f8a55; --warn: #9a6a08; --danger: #b8402e; } }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: var(--sans); color: var(--text); background: var(--ground); line-height: 1.55; font-size: 16px; }
  .wrap { max-width: 880px; margin: 0 auto; padding: 0 16px 64px; }
  header { padding: 56px 0 8px; }
  .eyebrow { font-family: var(--mono); font-size: 12px; letter-spacing: .2em; text-transform: uppercase; color: var(--accent); margin: 0 0 12px; }
  h1 { font-size: clamp(28px, 5vw, 42px); margin: 0 0 12px; letter-spacing: -.02em; }
  p.lead { color: var(--muted); max-width: 70ch; margin: 0 0 24px; }
  .panel { background: var(--panel); border: 1px solid var(--hair); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 0 0 6px; }
  input[type=text] { width: 100%; font-family: var(--mono); font-size: 13px; color: var(--text); background: var(--ground);
    border: 1px solid var(--hair); border-radius: 8px; padding: 9px 10px; }
  .drop { border: 1px dashed var(--hair); border-radius: 10px; padding: 26px; text-align: center; color: var(--muted); cursor: pointer; }
  .drop.over { border-color: var(--accent-line); color: var(--text); }
  button { font: inherit; font-size: 14px; color: var(--text); background: transparent; border: 1px solid var(--hair);
    border-radius: 8px; padding: 8px 14px; cursor: pointer; }
  button:focus-visible, input:focus-visible, .drop:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 14px; }
  .verdict { font-weight: 700; font-size: 18px; margin: 0 0 6px; }
  .pass { color: var(--pass); } .fail { color: var(--danger); } .na { color: var(--dim); } .warnc { color: var(--warn); }
  ul.items { list-style: none; padding: 0; margin: 12px 0 0; }
  ul.items li { border-top: 1px solid var(--hair); padding: 8px 0; font-size: 14px; display: grid; grid-template-columns: 5.5rem 1fr; gap: 10px; }
  .mark { font-family: var(--mono); font-size: 12px; }
  .what { color: var(--text); } .notes { color: var(--muted); font-size: 13px; }
  .small { color: var(--dim); font-size: 13px; }
  code { font-family: var(--mono); font-size: 13px; }
</style>
<div class="wrap">
  <header>
    <p class="eyebrow">Uderia · evidence</p>
    <h1>Verify an evidence bundle</h1>
    <p class="lead">Drop an exported bundle here. Everything is checked in this browser — signatures (Ed25519), the signed content where the bundle carries it, every answer's sealed chain and its bill of materials, the evidence ledger's signed checkpoint, and an embedded audit pack. Nothing is sent anywhere; this page works offline.</p>
  </header>
  <div class="panel">
    <label for="fp">The deployment's public-key fingerprint — get it from the deployment's operator, not from the bundle</label>
    <input id="fp" type="text" placeholder="64 hex characters (optional — without it the signer is not confirmed)" autocomplete="off" spellcheck="false">
    <div class="row">
      <div id="drop" class="drop" tabindex="0" role="button" aria-label="Choose an evidence bundle">Drop a bundle (.json) here, or press to choose one</div>
      <input id="file" type="file" accept="application/json,.json" hidden>
    </div>
  </div>
  <div class="panel" id="out" aria-live="polite" hidden></div>
  <p class="small">The same checks run in <code>scripts/verify_evidence.py</code>. A signature proves what was recorded and that it is unchanged — not that an answer is right. Uderia is a framework for trusted data agents.</p>
</div>
<script>
__MODULE__
</script>
<script>
(function () {
  var drop = document.getElementById('drop'), file = document.getElementById('file'),
      fp = document.getElementById('fp'), out = document.getElementById('out');
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function line(ok, what, notes) {
    var m = ok === true ? ['verified', 'pass'] : ok === false ? ['FAILED', 'fail'] : ['not checked', 'na'];
    return '<li><span class="mark ' + m[1] + '">' + m[0] + '</span><span><span class="what">' + esc(what)
      + '</span><br><span class="notes">' + esc((notes || []).join('; ')) + '</span></span></li>';
  }
  async function run(text, name) {
    out.hidden = false;
    out.innerHTML = '<p class="small">Checking ' + esc(name) + '…</p>';
    var res;
    try { res = await window.EvidenceVerify.verifyBundle(text, fp.value); }
    catch (e) { out.innerHTML = '<p class="verdict fail">Not a readable evidence bundle</p><p class="notes">' + esc(e.message) + '</p>'; return; }
    var who = res.signer_confirmed ? 'signer confirmed against the fingerprint you supplied'
      : (fp.value.trim() ? 'signer not confirmed for the whole file: ' + res.failed + ' record(s) did not verify (see below)'
                         : 'signer not confirmed — enter the fingerprint above to confirm who signed it');
    var head = res.failed
      ? '<p class="verdict fail">' + res.failed + ' of ' + res.checked + ' did NOT verify</p>'
      : '<p class="verdict pass">' + res.checked + ' of ' + res.checked + ' verify</p>';
    var items = res.attestations.map(function (a) { return line(a.ok, a.kind.replace(/_/g, ' ') + ' · ' + String(a.label || a.artifact_id).replace(/ext-([0-9a-f]{8})[0-9a-f-]{28}/g, 'ext-$1…'), a.notes); })
      .concat(res.turns.map(function (t) { return line(t.ok, 'answer · ' + t.session_id + ' #' + t.turn, t.notes); }));
    if (res.audit_pack) items.push(line(res.audit_pack.ok, 'audit pack', res.audit_pack.notes));
    if (res.ledger) items.push(line(res.ledger.ok, 'evidence ledger', res.ledger.notes));
    out.innerHTML = head + '<p class="' + (res.signer_confirmed ? 'notes pass' : 'notes warnc') + '">' + esc(who) + '</p><ul class="items">' + items.join('') + '</ul>';
  }
  function take(f) { if (!f) return; f.text().then(function (t) { run(t, f.name); }); }
  drop.addEventListener('click', function () { file.click(); });
  drop.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); } });
  file.addEventListener('change', function () { take(file.files[0]); });
  drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
  drop.addEventListener('drop', function (e) { e.preventDefault(); drop.classList.remove('over'); take(e.dataTransfer.files[0]); });
})();
</script>
</html>
"""


def render() -> str:
    return TEMPLATE.replace("__MODULE__", BEGIN + MODULE.read_text(encoding="utf-8").rstrip("\n") + END)


def inline_copy(page_text: str) -> str:
    a = page_text.index(BEGIN) + len(BEGIN)
    return page_text[a:page_text.index(END, a)]


def with_module(page_text: str) -> str:
    """``page_text`` with its EVIDENCE-VERIFY block replaced by the current module."""
    a = page_text.index(BEGIN) + len(BEGIN)
    return page_text[:a] + MODULE.read_text(encoding="utf-8").rstrip("\n") + page_text[page_text.index(END, a):]


if __name__ == "__main__":
    html = render()
    if "--check" in sys.argv:
        cur = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        if cur != html:
            print(f"{PAGE.relative_to(ROOT)} is stale — run scripts/build_evidence_verifier_page.py")
            sys.exit(1)
        stale = [p for p in STORIES if p.exists() and p.read_text(encoding="utf-8") != with_module(p.read_text(encoding="utf-8"))]
        if stale:
            print("stale story copies — run scripts/build_evidence_verifier_page.py: "
                  + ", ".join(str(p.relative_to(ROOT)) for p in stale))
            sys.exit(1)
        print("verify-evidence.html current")
        sys.exit(0)
    PAGE.write_text(html, encoding="utf-8")
    print(f"wrote {PAGE.relative_to(ROOT)}")
    for p in STORIES:
        if p.exists():
            p.write_text(with_module(p.read_text(encoding="utf-8")), encoding="utf-8")
            print(f"synced {p.relative_to(ROOT)}")
