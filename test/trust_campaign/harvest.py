#!/usr/bin/env python3
"""Harvest new provocations from REAL traffic — the prompts on which a gate actually acted.

    python3 test/trust_campaign/harvest.py                 # this instance, the caller's own turns
    python3 test/trust_campaign/harvest.py --days 30 --limit 500
    python3 test/trust_campaign/harvest.py --all           # include prompts a case already resembles
    python3 test/trust_campaign/harvest.py --ledger        # also record the candidates in harvest_ledger.json

THE LEDGER (`harvest_ledger.json`, committed) is where a candidate is DISPOSITIONED: each
entry is `new` until someone sets it `adopted` (naming the case id written for it) or
`declined` (with a reason). `run_campaign.py --baseline write` REFUSES while any entry is
`new`, and the battery gate checks every adopted entry names a case that exists — so a run
cannot be accepted without either new cases or a recorded no. That is the one place the
"keep adding cases" rule can bite without a server in CI.

WHY: the suite's cases are what the campaign's authors could think of. The engine itself
finds more — every turn where a gate blocked, annotated, parked or rewrote leaves a trust
event, and the prompt behind it is a provocation that is PROVEN to reach a gate on a real
agent. A harvested prompt that resembles no existing case is a candidate case, with its
station already known from the event that fired.

The campaign's own sessions are excluded (their prompts ARE the cases). Prompts are
compared to the suite by token overlap; `--all` shows the rest too.

Reads trust events and review items through the platform's own modules (fail-open, same
DB the server uses) and each originating turn through the REST turn-details route, so it
works on file or Postgres session stores alike.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
from agents import TAGS, login, req  # noqa: E402
from suite import CASES  # noqa: E402

LEDGER = os.path.join(HERE, "harvest_ledger.json")


def load_ledger() -> dict:
    try:
        return json.load(open(LEDGER))
    except Exception:  # noqa: BLE001
        return {"entries": {}}


def save_ledger(led: dict) -> None:
    json.dump(led, open(LEDGER, "w"), indent=1, sort_keys=True)


def undispositioned(led: dict | None = None) -> list:
    led = led or load_ledger()
    return [k for k, e in (led.get("entries") or {}).items() if e.get("status") == "new"]

ACTED = {
    "grounding_block": "grounded", "grounding_annotate": "grounded",
    "guardrail_outbound_block": "guarded", "guardrail_outbound_redact": "guarded",
    "guardrail_outbound_annotate": "guarded", "guardrail_inbound_block": "guarded",
    "guardrail_inbound_redact": "guarded", "guardrail_inbound_annotate": "guarded",
    "honesty_rewrite": "grounded", "honesty_flag": "grounded",
    "surface_breach": "bounded", "policy_observed": "bounded",
}
CAMPAIGN_TAGS = set(TAGS.values())
_WORD = re.compile(r"[a-z0-9_]{3,}")


def _tokens(s: str) -> set:
    return set(_WORD.findall((s or "").lower()))


def resembles(prompt: str, threshold: float = 0.45):
    """The case this prompt most resembles (Jaccard over tokens), if above the threshold."""
    pt = _tokens(prompt)
    best, score = None, 0.0
    for c in CASES:
        ct = _tokens(c["prompt"])
        if not pt or not ct:
            continue
        j = len(pt & ct) / len(pt | ct)
        if j > score:
            best, score = c["id"], j
    return (best, round(score, 2)) if score >= threshold else (None, round(score, 2))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ledger", action="store_true", help="record NEW candidates in harvest_ledger.json")
    a = ap.parse_args(argv)

    token = login()
    me = req("GET", "/api/v1/auth/me", token)
    uid = me.get("id") or (me.get("user") or {}).get("id")
    if not uid:
        sys.exit(f"could not read the caller's id: {str(me)[:200]}")

    from trusted_data_agent.core import review_queue, trust_events
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).isoformat()
    events = [e for e in trust_events.list_events(uid, include_acked=True, limit=a.limit)
              if e.get("event_type") in ACTED and (e.get("created_at") or "") >= since]
    parked = [i for i in review_queue.list_items(uid, status=None, limit=a.limit)
              if (i.get("created_at") or "") >= since]

    seen = {}
    for e in events:
        key = (e.get("session_id"), str(e.get("turn_id")))
        if not e.get("session_id") or e.get("profile_tag") in CAMPAIGN_TAGS:
            continue
        seen.setdefault(key, {"tag": e.get("profile_tag"), "acts": set(), "stations": set(),
                              "summary": e.get("summary")})
        seen[key]["acts"].add(e["event_type"])
        seen[key]["stations"].add(ACTED[e["event_type"]])
    for i in parked:
        key = (i.get("session_id"), str(i.get("turn_id")))
        if not i.get("session_id") or i.get("profile_tag") in CAMPAIGN_TAGS:
            continue
        seen.setdefault(key, {"tag": i.get("profile_tag"), "acts": set(), "stations": set(),
                              "summary": i.get("reason")})
        seen[key]["acts"].add(f"parked:{i.get('source')}")
        seen[key]["stations"].add("reviewed")

    print(f"{len(events)} gate events · {len(parked)} parked items · {len(seen)} distinct turns "
          f"(last {a.days} days, campaign agents excluded)\n")
    rows = []
    for (sid, tid), info in seen.items():
        t = req("GET", f"/api/session/{sid}/turn/{tid}/details", token)
        prompt = (t.get("user_query") or "").strip()
        if not prompt:
            continue
        like, score = resembles(prompt)
        rows.append((like, score, info, prompt, sid, tid, (t.get("profile_type") or "?")))
    rows.sort(key=lambda r: (r[0] is not None, -len(r[2]["acts"])))
    new = 0
    for like, score, info, prompt, sid, tid, cls in rows:
        if like and not a.all:
            continue
        new += like is None
        print(f"{'NEW' if like is None else f'~{like}({score})':12s} @{info['tag'] or '?':8s} {cls:22s} "
              f"{','.join(sorted(info['stations'])):18s} {','.join(sorted(info['acts']))}")
        print(f"             {prompt[:160].replace(chr(10), ' ')}")
        print(f"             session {sid} turn {tid}")
    print(f"\n{new} candidate prompt(s) resembling no existing case" if rows else "\nnothing harvested")
    if a.ledger:
        led = load_ledger()
        added = 0
        for like, score, info, prompt, sid, tid, cls in rows:
            if like:
                continue
            key = hashlib.sha1(prompt.strip().lower().encode()).hexdigest()[:12]
            if key in led["entries"]:
                continue
            led["entries"][key] = {"status": "new", "prompt": prompt[:400], "tag": info["tag"],
                                   "class": cls, "stations": sorted(info["stations"]),
                                   "acts": sorted(info["acts"]), "session_id": sid, "turn_id": tid,
                                   "harvested": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "case": None, "reason": None}
            added += 1
        save_ledger(led)
        print(f"ledger: {added} new entr{'y' if added == 1 else 'ies'} · "
              f"{len(undispositioned(led))} awaiting a disposition (adopt → case id · decline → reason)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
