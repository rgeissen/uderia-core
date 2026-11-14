#!/usr/bin/env python3
"""
iPad layout probe — lays the REAL app out in WebKit (Playwright, mobile emulation) at iPad sizes
and fails on the three ways the conversation has been cut:

  right   something in a message is wider than the chat (and is not inside its own sideways
          scroller): the chat scrolls sideways, or a clipping box cuts it
  bottom  with the keyboard up (the shell is what appViewport.js makes it: the visible height)
          the composer runs past the bottom of the shell — tool header open, a long message
  top     a box of the shell has been scrolled (iOS reveals a focused field, scrollIntoView())

Static gate for the same contract: test/test_app_shell.mjs. This one needs a running server and
`python -m playwright install webkit`; it is a support/regression probe, not part of the battery.

  python test/ipad_layout_probe.py                      # admin/admin, the 40 latest sessions
  python test/ipad_layout_probe.py --sessions 400 --user admin --password admin
"""
import argparse, json, sys, urllib.request

from playwright.sync_api import sync_playwright

WIDTHS = [(1180, 820), (820, 1180), (768, 1024), (590, 820), (375, 820), (320, 820)]   # landscape, portrait, mini, Split View ½ / ⅓, Slide Over
SHORT_SHELLS = [(1180, 820, 420), (1180, 820, 360), (1180, 820, 300), (820, 1180, 600)]  # keyboard up

OVERFLOW_JS = r"""() => {
  const out = []; const cc = document.getElementById('chat-container'); if (!cc) return {chat: null, out};
  const bound = cc.getBoundingClientRect().right;
  const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '');
  for (const el of cc.querySelectorAll('*')) {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height || r.right <= bound + 1) continue;
    if (el.closest('.sr-only, [aria-hidden="true"]')) continue;
    let p = el.parentElement, outer = false;
    while (p && p !== cc) { if (p.getBoundingClientRect().right > bound + 1 && getComputedStyle(p).overflowX === 'visible') { outer = true; break; } p = p.parentElement; }
    if (outer) continue;
    let scroller = false; p = el.parentElement;
    while (p && p !== cc) { const ox = getComputedStyle(p).overflowX; if (ox !== 'visible' && p.getBoundingClientRect().right <= bound + 1) { scroller = ox === 'auto' || ox === 'scroll'; break; } p = p.parentElement; }
    if (!scroller) out.push(name(el) + ' +' + Math.round(r.right - bound) + 'px');
  }
  return {chat: [cc.scrollWidth, cc.clientWidth], out: out.slice(0, 5)};
}"""

SHELL_JS = r"""() => {
  const b = document.body.getBoundingClientRect(), f = document.getElementById('chat-footer').getBoundingClientRect();
  const scrolled = [];
  for (const el of [document.body, ...document.querySelectorAll('#main-content-area, #main-content-area *')]) {
    const cs = getComputedStyle(el); if (el.id === 'chat-container' && !el.scrollLeft) continue;
    if (el.closest('#status-window.collapsed, #session-history-panel.collapsed')) continue;   // not shown; checked once opened
    if ((el.scrollTop && cs.overflowY !== 'auto' && cs.overflowY !== 'scroll') || (el.scrollLeft && cs.overflowX !== 'auto' && cs.overflowX !== 'scroll'))
      scrolled.push((el.id || el.tagName) + ' ' + el.scrollLeft + ',' + el.scrollTop);
  }
  return {shellBottom: Math.round(b.bottom), footerBottom: Math.round(f.bottom), navTop: Math.round(document.querySelector('body > nav').getBoundingClientRect().top), scrolled};
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5050")
    ap.add_argument("--user", default="admin"); ap.add_argument("--password", default="admin")
    ap.add_argument("--sessions", type=int, default=40)
    a = ap.parse_args()

    req = urllib.request.Request(a.base + "/api/v1/auth/login", json.dumps({"username": a.user, "password": a.password}).encode(), {"Content-Type": "application/json"})
    jwt = json.load(urllib.request.urlopen(req))["token"]
    req = urllib.request.Request(f"{a.base}/api/v1/sessions?limit={a.sessions}", headers={"Authorization": f"Bearer {jwt}"})
    d = json.load(urllib.request.urlopen(req)); sessions = [s["id"] for s in (d.get("sessions", d) if isinstance(d, dict) else d)][: a.sessions]
    failures = []

    def page_for(browser, w, h):
        ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
        ctx.add_init_script(f"localStorage.setItem('tda_auth_token', {json.dumps(jwt)});")
        pg = ctx.new_page(); pg.goto(a.base + "/", wait_until="networkidle"); pg.wait_for_timeout(2500)
        return ctx, pg

    def load(pg, sid):
        pg.evaluate("async (sid) => { const m = await import('/static/js/handlers/sessionManagement.js'); await m.handleLoadSession(sid, false, true); }", sid)
        pg.wait_for_timeout(1500)

    with sync_playwright() as p:
        browser = p.webkit.launch()
        # right: every session at every width
        for w, h in WIDTHS:
            ctx, pg = page_for(browser, w, h); bad = 0
            for sid in sessions:
                load(pg, sid); r = pg.evaluate(OVERFLOW_JS)
                if r["chat"] and (r["chat"][0] > r["chat"][1] + 1 or r["out"]):
                    bad += 1; failures.append(f"right  {w}x{h} {sid}: chat {r['chat']} {r['out']}")
            print(f"right  {w}x{h}: {len(sessions) - bad}/{len(sessions)} sessions fit"); ctx.close()
        # bottom + top: keyboard-up shells, resource panel open, a 12-line message; then the scrolls code makes
        for w, h, shell in SHORT_SHELLS:
            ctx, pg = page_for(browser, w, h); load(pg, sessions[0])
            pg.evaluate("() => document.getElementById('tool-header').classList.remove('collapsed')"); pg.wait_for_timeout(500)
            pg.evaluate("() => { const i = document.getElementById('user-input'); i.value = 'line\\n'.repeat(12); i.dispatchEvent(new Event('input', {bubbles: true})); }")
            pg.evaluate(f"() => document.documentElement.style.setProperty('--app-h', '{shell}px')"); pg.wait_for_timeout(500)
            pg.evaluate("() => { document.getElementById('user-input').focus(); for (const s of document.querySelectorAll('#status-window-content .status-step, #chat-log .message-bubble')) s.scrollIntoView({block: 'end'}); }")
            pg.wait_for_timeout(300); r = pg.evaluate(SHELL_JS)
            # what was scrolled while Live Status was shut must not show once it opens
            pg.evaluate("() => document.getElementById('status-window').classList.remove('collapsed')"); pg.wait_for_timeout(700)
            sw = pg.evaluate("() => { const w = document.getElementById('status-window'), c = document.getElementById('status-window-content'); return {left: c.scrollLeft + w.scrollLeft, top: w.scrollTop, header: Math.round(w.querySelector('header').getBoundingClientRect().top - w.getBoundingClientRect().top)}; }")
            r["live_status_opened"] = sw
            ok = (r["footerBottom"] <= r["shellBottom"] + 0.5 and r["navTop"] == 0 and not r["scrolled"]
                  and sw["left"] == 0 and sw["top"] == 0 and abs(sw["header"]) <= 2)
            print(f"shell  {w}x{h} keyboard-up {shell}px: composer ends {r['footerBottom']} / shell {r['shellBottom']}, top bar at {r['navTop']}, scrolled boxes {r['scrolled'] or 'none'}, Live Status opened {sw} -> {'ok' if ok else 'CUT'}")
            if not ok: failures.append(f"shell  {w}x{h}@{shell}: {r}")
            ctx.close()
        browser.close()

    for f in failures[:40]: print("  FAIL", f)
    print("ALL PASS — the conversation is never cut" if not failures else f"{len(failures)} FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
