"""#22/#23 — a `partial` signature must say what it did not cover.

Every validator returns a 3-band verdict (`clean` / `partial` / `blocked`) and the band
is stored in `trust_attestations.verdict_json` at sign time. No surface read it back, so:

  #22  the guided sign discarded each ingredient's `validation` body and reported
       "Signed — content-bound and offline-verifiable" for a signature whose own record
       said "content not fully scanned for injection" and "every answer gate is in
       observe mode". The signer was told a check had PASSED when they had in fact
       ASSERTED the unchecked remainder.
  #23  a partial signature then rendered pixel-identical to an earned one everywhere.

The band QUALIFIES a signed state; it never becomes one. Rule 8.4 is explicit — where a
second channel exists, use it rather than a fifth state — and the same reasoning that
kept `unsigned`/`deployment_unattested` from becoming new colours applies here.

Pure: no server, no network.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.trust import store as trust_store  # noqa: E402
from trusted_data_agent.api.trust_routes import signed_caveat   # noqa: E402

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")


# The two real verdicts observed live during the walkthrough.
PARTIAL_KNOWLEDGE = {
    "signable": True, "status": "integrity-only", "band": "partial",
    "note": "137 document(s) · integrity + embedding verified · content not fully "
            "scanned for injection",
}
PARTIAL_AGENT = {
    "signable": True, "status": "observe_only", "band": "partial",
    "note": "signable — but every answer gate is in observe mode: grounding, guardrails, "
            "honesty measure only and never alter an answer",
}
CLEAN_KNOWLEDGE = {
    "signable": True, "status": "screened", "band": "clean",
    "note": "137 document(s) · 100% screened · scan report bound into the signature",
}

with tempfile.TemporaryDirectory() as d:
    db = os.path.join(d, "t.db")

    def _record(kind, aid, verdict):
        trust_store.record_attestation(kind=kind, artifact_id=aid,
                                       version_hash="h-" + aid, verdict=verdict,
                                       signer_uuid="u1", db_path=db)

    _record("knowledge", "6", PARTIAL_KNOWLEDGE)
    _record("agent", "profile-x", PARTIAL_AGENT)
    _record("knowledge", "7", CLEAN_KNOWLEDGE)

    # The store round-trips the verdict — the data was always there.
    rec = trust_store.load_attestation("knowledge", "6", db_path=db)
    check("#22 the band is persisted at sign time",
          ((rec or {}).get("verdict") or {}).get("band"), "partial")
    check("#22 …with the note that explains it",
          "not fully scanned" in ((rec or {}).get("verdict") or {}).get("note", ""), True)


# `signed_caveat` reads the DEFAULT database, so exercise it against the real store the
# app uses rather than a temp one — it is the accessor the endpoints call.
def _live(kind, aid):
    return signed_caveat(kind, aid)


# It must never raise, whatever it is handed — a badge has to render regardless.
for kind, aid in (("knowledge", "does-not-exist"), ("", ""), ("agent", "nope")):
    try:
        _live(kind, aid)
    except Exception as e:  # noqa: BLE001
        failures.append(f"signed_caveat raised for {kind}/{aid}: {e}")
check("#23 an unknown artefact has no caveat", _live("knowledge", "does-not-exist"), None)

# The accessor must use a store function that EXISTS. The first implementation called a
# method that does not (`trust_store.get`), and a blanket `except` swallowed the
# AttributeError — so the caveat silently never appeared, which is precisely the failure
# this fix removes. Pin the real API so that cannot recur.
check("#22 the store exposes the accessor used", hasattr(trust_store, "load_attestation"), True)

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


routes = _src("src", "trusted_data_agent", "api", "trust_routes.py")
badge = _src("static", "js", "handlers", "trustBadge.js")
handler = _src("static", "js", "handlers", "agentPerformanceHandler.js")

check("#23 the accessor calls the real store API",
      "trust_store.load_attestation(kind, artifact_id)" in routes, True)
check("#23 …and never the method that does not exist",
      "trust_store.get(kind" in routes, False)
# A silent except is how the defect hid; the failure must at least be logged.
check("#23 a caveat lookup failure is logged, not swallowed",
      "caveat unavailable for" in routes, True)

# Exposed on BOTH surfaces a consumer reads.
check("#23 the per-artefact endpoint exposes it", '"caveat"' in routes, True)
check("#23 the agents endpoint exposes the agent's own", 'signed_caveat("agent", pid)' in routes, True)
check("#23 …and its ingredients'", '"ingredient_caveats"' in routes, True)
# A clean signature must add nothing — a caveat saying "no caveat" is noise.
check("#23 clean bands yield no caveat",
      'if not band or band == "clean":' in routes, True)
# An UNSIGNED artefact has no signature to qualify.
check("#23 unsigned artefacts are not given caveats",
      'if state != "unsigned":' in routes, True)

check("#22 the guided sign collects per-ingredient caveats", "caveats.push({" in badge, True)
check("#22 …and the agent's own verdict", "caveats.unshift({" in badge, True)
check("#22 …and returns them to the caller", "return { ok: true, state, caveats };" in badge, True)
check("#22 the sign outcome reports them", "_signCaveatNote(r.caveats)" in handler, True)
check("#23 the badge popover shows the caveat", "Signed with a caveat." in badge, True)

# It must remain a QUALIFIER: no new state, no new colour (Rule 8.4).
for token in ("'partial'", '"partial"'):
    check(f"#23 partial is not added to the state vocabulary ({token})",
          f"META[{token}]" in badge or f"_INTEGRITY_STATE = {{{token}" in handler, False)

# ── report ──────────────────────────────────────────────────────────────────
print()
if failures:
    print(f"FAILED — {len(failures)} assertion(s)\n")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print("ALL PASS — #22/#23: a partial signature says what it did not cover.")
