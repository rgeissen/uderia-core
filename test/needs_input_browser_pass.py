#!/usr/bin/env python3
"""Needs input (PROP-011) — the browser pass: the faces a DOM stub cannot reach, in a real
Chromium against a running TEST server with the fire drill on.

    TDA_NEEDS_INPUT_INJECT=1 python -m trusted_data_agent.main &      # the test server, never prod
    python test/needs_input_browser_pass.py [--base http://127.0.0.1:8050] [--tag TCIDE] [--shots DIR]

What it drives, as a person would:
  1. sign in on the login page; the conversation opens
  2. ask the campaign agent a marked question — the turn stops, the card appears under the
     answer with its options, Send disabled until one is picked; the composer is bound
  3. pick an option, Send — the card settles ("You answered"), the re-entry answer arrives
  4. the split panel opens from the card's host (NeedsInput.openNeedsInputPanel) and lists the
     answered question under "Asked and answered"
  5. the agent editor's Authority card shows the Needs-input section; raisers → observe is
     saved, read back over REST as `authorityConfig.inputPolicy.raisers`, then restored

Screenshots land in --shots (default: the scratchpad is not known here, so ./needs_input_shots).
Needs: `pip install playwright && python -m playwright install chromium`. One LLM turn + one
re-entry (cents). Exit 1 on any failed check.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default=os.environ.get("UDERIA_BASE", "http://127.0.0.1:5050"))
ap.add_argument("--user", default="admin")
ap.add_argument("--password", default="admin")
ap.add_argument("--tag", default="TCIDE", help="an agent with the needs_input component (the campaign Ideate agent)")
ap.add_argument("--shots", default="./needs_input_shots")
ap.add_argument("--headed", action="store_true")
ap.add_argument("--channel", default="chrome", help="chrome (the installed browser — a per-binary firewall such as LuLu "
                "already allows it; the bundled headless shell cannot reach the CDN assets) · bundled")
args = ap.parse_args()
os.makedirs(args.shots, exist_ok=True)

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def api(method, path, tok=None, body=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    r = urllib.request.Request(args.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


st, login = api("POST", "/api/v1/auth/login", body={"username": args.user, "password": args.password})
tok = login.get("token")
if not tok:
    print("cannot sign in over REST:", st, login); sys.exit(2)
st, profs = api("GET", "/api/v1/profiles", tok)
plist = profs if isinstance(profs, list) else (profs.get("profiles") or [])
agent = next((p for p in plist if p.get("tag") == args.tag), None)
if not agent:
    print(f"no agent @{args.tag} — provision the campaign agents first (test/trust_campaign)"); sys.exit(2)
pid = agent["id"]
stored_ip = dict(((agent.get("authorityConfig") or {}).get("inputPolicy") or {}))
if stored_ip.get("raisers") == "observe":
    # a precondition, not a check: under `observe` the drill's ambiguity is recorded, never raised,
    # so no platform card can appear (a crashed earlier run may have left the agent there)
    print(f"@{args.tag} has inputPolicy.raisers = observe — reset it (the editor, or PUT the profile) and rerun"); sys.exit(2)


def shot(page, name):
    p = os.path.join(args.shots, name)
    page.screenshot(path=p, full_page=False)
    return p


def read_profile(pid):
    st, profs = api("GET", "/api/v1/profiles", tok)
    plist = profs if isinstance(profs, list) else (profs.get("profiles") or [])
    return next((p for p in plist if p.get("id") == pid), {})


def open_editor(page, pid):
    """Open the agent editor through the app's OWN module instance — the pinned URL main.js
    imports (an unpinned import is a second copy with an empty configState: "Add Profile")."""
    if not hasattr(open_editor, "url"):
        main_js = page.request.get(args.base + "/static/js/main.js").text()
        m = re.search(r"from '\./handlers/configurationHandler\.js(\?v=[\d.]+)'", main_js)
        open_editor.url = "/static/js/handlers/configurationHandler.js" + (m.group(1) if m else "")
    page.evaluate("async ([url, pid]) => { const m = await import(url); await m.openProfileForEdit(pid); }", [open_editor.url, pid])


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not args.headed, **({} if args.channel == "bundled" else {"channel": args.channel}))
    page = browser.new_page(viewport={"width": 1380, "height": 900})
    page.set_default_timeout(60_000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 1 — sign in on the real login page
    print("\n[1] sign in")
    page.goto(args.base + "/login", wait_until="domcontentloaded")
    page.fill("#username", args.user); page.fill("#password", args.password)
    page.click("#login-button")
    page.wait_for_function("() => !!localStorage.getItem('tda_auth_token')")
    # the welcome screen (when shown) leads to the conversation through Start Reasoning
    try:
        page.wait_for_selector("#welcome-configure-btn", state="visible", timeout=8_000)
        page.click("#welcome-configure-btn")
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_selector("#user-input", state="visible")
    page.wait_for_function("() => { const t = document.getElementById('user-input'); return t && !t.disabled; }")
    # the pass runs in a conversation of its own (the login may land on the last one)
    sid_before = page.evaluate("() => (window.state && window.state.currentSessionId) || null")
    page.click("#header-new-session-button")
    # a new session: the id changes (or is not yet minted) and the greeting bubble stands alone
    page.wait_for_function("(prev) => { const t = document.getElementById('user-input'); const sid = (window.state && window.state.currentSessionId) || null; "
                           "return t && !t.disabled && (prev === null || sid !== prev) && document.querySelectorAll('#chat-log .message-bubble').length <= 1; }", arg=sid_before)
    page.wait_for_timeout(1500)
    check("the conversation opens with a composer, in a new session", page.is_visible("#user-input"))
    shot(page, "0_conversation.png")

    # 2 — a marked question stops the turn; the card appears
    print("\n[2] the turn stops to ask")
    page.wait_for_timeout(1500)
    page.fill("#user-input", f"@{args.tag} Summarise the Sales graph for me. [needs-input:choose]")
    page.keyboard.press("Enter")
    try:
        page.wait_for_selector(".ni-card[data-state='pending'] .ni-opt", timeout=120_000)
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False; print("   card did not appear:", str(e)[:120])
    check("the card appears under the answer with options", ok)
    shot(page, "1_card_pending.png")
    if ok:
        drill = page.locator(".ni-card[data-state='pending']", has_text="fire_drill").first
        others = page.locator(".ni-card[data-state='pending']").count() - 1
        if others > 0:
            print(f"   note: the model asked on its own as well ({others} more pending card(s)) — the weak-model act, observed")
        send = drill.locator(".ni-send").first
        check("Send is disabled until an option is picked", send.is_disabled())
        check("the card names who asks (the agent) and that the platform found the ambiguity",
              drill.locator(".ni-head").inner_text().find(args.tag) >= 0
              and drill.locator(".ni-mark").first.inner_text().strip() == "platform")
        st, pend = api("GET", "/api/v1/input-requests?status=pending", tok)
        q_sid = ((pend.get("items") or [{}])[0]).get("session_id")
        bound = page.evaluate("(sid) => (window.NeedsInput && window.NeedsInput.pendingFor) ? !!window.NeedsInput.pendingFor(sid) : null", q_sid)
        check("the composer is bound to the pending question (NeedsInput.pendingFor)", bound is True, str(bound))

        # 3 — answer on the card
        print("\n[3] answer on the card")
        drill.locator(".ni-opt").first.click()
        check("picking an option enables Send", not send.is_disabled())
        shot(page, "2_card_picked.png")
        drill_id = drill.get_attribute("data-ni-id")
        send.click()
        try:
            page.wait_for_selector(f".ni-card[data-ni-id='{drill_id}'] .ni-outcome", timeout=30_000)
            settled = page.locator(f".ni-card[data-ni-id='{drill_id}'] .ni-outcome").first.inner_text()
        except Exception:  # noqa: BLE001
            settled = ""
        check("the card settles with the answer", settled.startswith("You answered"), settled[:80])
        # the re-entry turn: a new assistant message after the card's bubble
        before = page.locator("#chat-log .message-bubble").count()
        try:
            page.wait_for_function(f"() => document.querySelectorAll('#chat-log .message-bubble').length > {before}", timeout=120_000)
            page.wait_for_function("() => !window.state || !window.state.isProcessing", timeout=120_000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1500)
        after = page.locator("#chat-log .message-bubble").count()
        check("the re-entry answer arrives in the same conversation", after > before, f"{before} -> {after}")
        # a question the MODEL raised on its own (the re-entry may ask again on a weak model) is
        # declined here so the session ends clean — observed, not a failure of the platform
        model_asks = 0
        for _ in range(3):
            left = page.locator(".ni-card[data-state='pending']")
            if left.count() == 0:
                break
            if left.first.locator(".ni-mark").first.inner_text().strip() == "platform":
                break
            model_asks += 1
            left.first.locator("[data-ni-act='decline']").click()
            page.wait_for_timeout(4000)
        if model_asks:
            print(f"   note: declined {model_asks} question(s) the model asked on its own")
        pend2 = page.locator(".ni-card[data-state='pending']").count()
        check("no platform question is pending after the answer", pend2 == 0, str(pend2))
        shot(page, "3_reentry.png")

        # 4 — the split panel
        print("\n[4] the split panel")
        page.evaluate("() => window.NeedsInput.openNeedsInputPanel({})")
        try:
            page.wait_for_selector("#needs-input-split-panel.ni-split--open #ni-split-history .ni-card", timeout=20_000)
            ok4 = True
        except Exception:  # noqa: BLE001
            ok4 = False
        check("the panel opens and lists the answered question under Asked and answered", ok4)
        if ok4:
            # the count is pending across EVERY agent of this user (other sessions may wait too)
            st, pend_all = api("GET", "/api/v1/input-requests?status=pending", tok)
            n_rest = int(pend_all.get("pending") or 0) if isinstance(pend_all, dict) else 0
            shown = page.locator("#needs-input-split-panel .ni-count").inner_text()
            check("the panel's count agrees with the REST pending count across the user's agents",
                  shown.strip().startswith(f"{n_rest} waiting"), f"{shown!r} vs REST {n_rest}")
        shot(page, "4_split_panel.png")
        page.evaluate("() => window.NeedsInput.closeNeedsInputPanel()")

    # 5 — the agent editor's Needs-input section
    print("\n[5] the agent editor")
    open_editor(page, pid)
    page.wait_for_selector("#profile-modal:not(.hidden)")
    page.click('#profile-modal .profile-nav-item[data-profile-section="trust"]')
    page.wait_for_selector("#profile-section-trust:not(.hidden)")
    page.locator("#profile-section-trust summary", has_text="Agent Authority").first.click()
    page.wait_for_selector("#profile-authority-input-policy", state="visible")
    sec = page.locator("#profile-authority-input-policy")
    sec.scroll_into_view_if_needed()
    check("the section is in the Authority card with its four controls and standing answers",
          sec.locator("#profile-input-interactive").count() == 1 and sec.locator("#profile-input-unattended").count() == 1
          and sec.locator("#profile-input-raisers").count() == 1 and sec.locator("#profile-input-max").count() == 1
          and sec.locator("#profile-input-defaults").count() == 1)
    check("it displays the stored policy (raisers)", page.input_value("#profile-input-raisers") == (stored_ip.get("raisers") or "enforce"))
    shot(page, "5_editor_section.png")
    page.select_option("#profile-input-raisers", "observe")
    page.click("#profile-modal-save")
    # (a hidden element is never "visible" — wait on the class, not the selector)
    page.wait_for_function("() => document.getElementById('profile-modal').classList.contains('hidden')", timeout=30_000)
    time.sleep(1.0)
    got = (read_profile(pid).get("authorityConfig") or {}).get("inputPolicy") or {}
    check("the save lands as authorityConfig.inputPolicy.raisers = observe over REST", got.get("raisers") == "observe", json.dumps(got))
    # restore what was stored
    open_editor(page, pid)
    page.wait_for_selector("#profile-modal:not(.hidden)")
    page.click('#profile-modal .profile-nav-item[data-profile-section="trust"]')
    page.wait_for_selector("#profile-section-trust:not(.hidden)")
    page.locator("#profile-section-trust summary", has_text="Agent Authority").first.click()
    page.wait_for_selector("#profile-authority-input-policy", state="visible")
    check("re-opened, the editor displays observe (never the fallback)", page.input_value("#profile-input-raisers") == "observe")
    page.select_option("#profile-input-raisers", stored_ip.get("raisers") or "enforce")
    page.click("#profile-modal-save")
    # (a hidden element is never "visible" — wait on the class, not the selector)
    page.wait_for_function("() => document.getElementById('profile-modal').classList.contains('hidden')", timeout=30_000)
    time.sleep(1.0)
    got3 = (read_profile(pid).get("authorityConfig") or {}).get("inputPolicy") or {}
    check("restored: raisers back to what was stored (absent = enforce)", (got3.get("raisers") or "enforce") == (stored_ip.get("raisers") or "enforce"), json.dumps(got3))

    check("no page errors during the pass", not errors, "; ".join(errors)[:300])
    browser.close()

print(f"\nshots: {os.path.abspath(args.shots)}")
print(f"\n{'FAILED' if FAIL else 'ALL PASS'} — {PASS + FAIL} checks ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
