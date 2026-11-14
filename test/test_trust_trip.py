"""Trust Trip — the resettable leg on the Trust Circle (marker store + lens).

Pins the invariants of docs/roadmap/TRUST_TRIP_RESET.md:
  W1  a marker, never a deletion — the store only ever appends; the module
      contains no UPDATE and no DELETE path at all
  W2  the reset is audited — who/when/why + the integrity snapshot ride the row
  W4  one aggregator, run twice — the trip cell is produced by the SAME
      ``online_quality_by_agent`` over the lens-filtered subset, so its shape is
      identical to the total cell's by construction (asserted key-by-key)
  W6  the lens is per-agent — unmarked agents are excluded from the subset
      (their trip reading IS their whole record), and one user's markers can
      never scope another user's spans

Runs CI-pure: a temp sqlite file, synthetic spans, no server.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import trust_trip as TT                  # noqa: E402
from trusted_data_agent.eval.analysis import online_quality_by_agent  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


DB = os.path.join(tempfile.mkdtemp(prefix="tda_trip_"), "trip.db")


def span(tag, ns, failed=0, user="u1", **attrs):
    return {"start_time_unix_nano": int(ns),
            "attributes": {"agent.profile_tag": tag, "user.uuid": user,
                           "eval.gating_failures": int(failed),
                           "eval.profile_class": "rag_focused", **attrs}}


# ── 1 · the store — append-only, audited, isolated ───────────────────────────
print("\n[1] the marker store")
m1 = TT.reset_trip("u1", "@OPTIM", profile_id="p-1", note="after the corpus quarantine",
                   integrity_state="trusted", db_path=DB)
check("reset returns the marker with server time in BOTH units",
      bool(m1["id"]) and bool(m1["created_at"]) and m1["created_at_ns"] > 0)
check("the @ is stripped — markers match spans by bare tag", m1["profile_tag"] == "OPTIM")
check("W2: the reason and the integrity snapshot ride the row",
      m1["note"] == "after the corpus quarantine" and m1["integrity_state"] == "trusted")

time.sleep(0.002)   # distinct created_at_ns for latest-wins ordering
m2 = TT.reset_trip("u1", "OPTIM", profile_id="p-1", db_path=DB)
latest = TT.latest_markers("u1", db_path=DB)
check("latest marker wins as the current leg boundary",
      latest["OPTIM"]["id"] == m2["id"])
check("…and carries the leg number", latest["OPTIM"]["leg_n"] == 2)

book = TT.list_markers("u1", "OPTIM", db_path=DB)
check("W1: the logbook keeps every leg, newest first",
      [b["id"] for b in book] == [m2["id"], m1["id"]])

TT.reset_trip("u2", "OPTIM", db_path=DB)
check("per-user isolation — u2's marker never appears in u1's map",
      TT.latest_markers("u1", db_path=DB)["OPTIM"]["id"] == m2["id"]
      and len(TT.list_markers("u1", "OPTIM", db_path=DB)) == 2)

try:
    TT.reset_trip("u1", "  @ ", db_path=DB)
    check("an empty tag is refused (a marker matching no spans is not a leg)", False)
except ValueError:
    check("an empty tag is refused (a marker matching no spans is not a leg)", True)

long_note = "x" * 1000
m3 = TT.reset_trip("u1", "FOCUS", note=long_note, db_path=DB)
check("the note is bounded — a paste cannot become the panel",
      len(m3["note"]) == TT.NOTE_MAX_CHARS)

src = open(os.path.join(os.path.dirname(__file__), "..",
                        "src/trusted_data_agent/core/trust_trip.py"),
           encoding="utf-8").read()
check("W1: the module contains NO update and NO delete SQL path",
      "UPDATE trust_trip_markers" not in src and "DELETE FROM" not in src)

check("reads fail OPEN on a broken store (lens falls back to the whole record)",
      TT.latest_markers("u1", db_path="/nonexistent/dir/x.db") == {}
      and TT.list_markers("u1", db_path="/nonexistent/dir/x.db") == [])
try:
    TT.reset_trip("u1", "OPTIM", db_path="/nonexistent/dir/x.db")
    check("the WRITE fails LOUD — the reset is the act, never silently dropped", False)
except Exception:
    check("the WRITE fails LOUD — the reset is the act, never silently dropped", True)


# ── 2 · the lens filter — pure, per-agent, boundary-exact ────────────────────
print("\n[2] the lens filter")
T0 = 1_000_000_000_000_000_000
markers = {"OPTIM": {"created_at_ns": T0 + 100}}
spans = [
    span("OPTIM", T0 + 50),          # before the reset — previous leg
    span("OPTIM", T0 + 100),         # exactly the boundary — belongs to the new leg
    span("OPTIM", T0 + 200, failed=1),
    span("FOCUS", T0 + 300),         # unmarked agent — no leg, excluded
    {"attributes": {"agent.profile_tag": "OPTIM"}},   # untimed — cannot prove its leg
]
trip = TT.spans_since(spans, markers)
check("only the marked agent's post-boundary spans pass",
      [TT.span_ns(s) for s in trip] == [T0 + 100, T0 + 200])
check("W6: an unmarked agent is excluded entirely (its trip IS its whole record)",
      not any((s["attributes"]["agent.profile_tag"]) == "FOCUS" for s in trip))
check("an untimed span stays out — it cannot prove it belongs to the leg",
      len(trip) == 2)
check("no markers ⇒ empty subset (the lens has nothing to say)",
      TT.spans_since(spans, {}) == [])

prev = TT.spans_between(spans, "OPTIM", None, T0 + 100)
check("the previous leg is end-EXCLUSIVE — the boundary span belongs to one leg only",
      [TT.span_ns(s) for s in prev] == [T0 + 50])
check("a bounded previous leg respects both ends",
      [TT.span_ns(s) for s in TT.spans_between(spans, "OPTIM", T0 + 60, T0 + 300)]
      == [T0 + 100, T0 + 200])


# ── 3 · W4 — one aggregator, two lenses, one shape ───────────────────────────
print("\n[3] the trip cell is the SAME aggregator's output (shape contract)")
many = [span("OPTIM", T0 + i * 10, failed=(1 if i in (2, 3) else 0),
             **{"eval.dim.cost.score": 0.01, "eval.dim.latency.score": 900})
        for i in range(10)]
total_cell = online_quality_by_agent(many)["OPTIM"]
trip_cell = online_quality_by_agent(
    TT.spans_since(many, {"OPTIM": {"created_at_ns": T0 + 50}}))["OPTIM"]
check("both lenses produce a cell for the agent", bool(total_cell) and bool(trip_cell))
check("W4: the trip cell's shape is IDENTICAL to the total cell's, key for key",
      set(trip_cell.keys()) == set(total_cell.keys()),
      f"diff: {sorted(set(trip_cell) ^ set(total_cell))}")
check("the trip counts only the leg", trip_cell["turns"] == 5 and total_cell["turns"] == 10)
check("failures before the boundary stay on the odometer only",
      total_cell["failures"] == 2 and trip_cell["failures"] == 0)

# The whole-record lens is BYTE-IDENTICAL to a world with no markers at all —
# the odometer is inviolable (W1): computing trip cells must not perturb it.
again = online_quality_by_agent(many)["OPTIM"]
check("computing a trip cell never perturbs the total cell", again == total_cell)


print(f"\n{'=' * 60}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
