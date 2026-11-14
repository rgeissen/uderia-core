"""#46 (+#39) — the judge panel's composition and its per-model spend.

Owner-raised after watching a two-model panel render as "2 models" and one aggregate
figure. Two omissions, one shape — the panel meters per member and the UI kept only the
sum:

  #46  the names are discarded exactly when composition starts to matter, and the
       aggregate cost hides which member spent it.
  #39  seen from the other side: a panel of ONE renders identically to a real panel, so
       a single small model's un-seconded opinion looks like a consensus.

Requirements this pins:
  · one pricing source, so the per-model parts SUM to the aggregate already displayed;
  · a model called once per judged dimension aggregates into one row, not N;
  · the breakdown rides the SAME persisted event as the aggregate (reload parity) and is
    display detail INSIDE it — a second cost-bearing event would double-count in the
    frontend's `_getTurnCost` reconciliation;
  · all THREE judge paths carry it (online hook · grounding gate · manual re-score).

Pure: no server, no network, no LLM.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.online import judge_usage_breakdown  # noqa: E402

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")


PRICE = lambda p, m, i, o: round(i * 1e-6 + o * 3e-6, 8)  # noqa: E731

# A real panel: two families, and one of them called twice (once per judged dimension).
USAGE = [
    ("Google", "gemini-3.1-flash-lite", 800, 40),
    ("OpenRouter", "mercury2", 900, 60),
    ("Google", "gemini-3.1-flash-lite", 700, 35),
]

rows = judge_usage_breakdown(USAGE, cost_fn=PRICE)

# ── 1 · one row per MODEL, not per call ─────────────────────────────────────
check("#46 a model called per-dimension yields one row", len(rows), 2)
by = {r["model"]: r for r in rows}
check("#46 …with its calls counted", by["gemini-3.1-flash-lite"]["calls"], 2)
check("#46 …and its tokens summed", (by["gemini-3.1-flash-lite"]["input_tokens"],
                                     by["gemini-3.1-flash-lite"]["output_tokens"]), (1500, 75))
check("#46 the provider is carried", by["mercury2"]["provider"], "OpenRouter")

# ── 2 · the parts SUM to the whole ──────────────────────────────────────────
# The aggregate is priced by the same CostManager in production; if the split used a
# different source the card would show parts that contradict their own total.
agg = sum(PRICE(p, m, i, o) for p, m, i, o in USAGE)
check("#46 per-model costs sum to the aggregate",
      round(sum(r["cost_usd"] for r in rows), 8), round(agg, 8))
check("#46 per-model tokens sum to the aggregate",
      (sum(r["input_tokens"] for r in rows), sum(r["output_tokens"] for r in rows)),
      (sum(u[2] for u in USAGE), sum(u[3] for u in USAGE)))

# ── 3 · costliest first — the reader's question is "who spent it" ───────────
check("#46 rows are ordered by spend", [r["model"] for r in rows],
      ["gemini-3.1-flash-lite", "mercury2"])

# ── 4 · absence and malformed input degrade, never raise ────────────────────
check("#46 no usage ⇒ no rows", judge_usage_breakdown([]), [])
check("#46 None ⇒ no rows", judge_usage_breakdown(None), [])
check("#46 malformed entries are skipped, not raised",
      judge_usage_breakdown([("only", "three", 1), None, 42, ("a", "b", 1, 2)]),
      [{"provider": "a", "model": "b", "input_tokens": 1, "output_tokens": 2, "calls": 1}])
# Without a pricing function costs are OMITTED rather than guessed at.
check("#46 no pricing ⇒ no cost key invented",
      "cost_usd" in judge_usage_breakdown(USAGE)[0], False)
# A pricing failure loses the cost, never the row — the composition still matters.
def _boom(*_a):
    raise RuntimeError("no price for this model")


priced = judge_usage_breakdown(USAGE, cost_fn=_boom)
check("#46 a pricing failure keeps the row", len(priced), 2)
check("#46 …with an explicit null cost", priced[0]["cost_usd"], None)

# ── 5 · the wiring: all three judge paths + reload parity ───────────────────
ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


online = _src("src", "trusted_data_agent", "eval", "online.py")
grounding = _src("src", "trusted_data_agent", "agent", "grounding", "runtime.py")
rest = _src("src", "trusted_data_agent", "api", "rest_routes.py")
uijs = _src("static", "js", "ui.js")

check("#46 the online hook builds the breakdown",
      "judge_usage = judge_usage_breakdown(" in online, True)
check("#46 the grounding gate builds it too",
      "judge_usage_breakdown(" in grounding, True)
check("#46 the manual re-score builds it too",
      "judge_usage_breakdown(" in rest, True)
# The deferred route (grounding gate → flushed after the turn saves) must carry it, or
# the gate's panel would lose its split on the one path that is not immediate.
check("#46 the deferred route carries it",
      'judge_usage=entry.get("judge_usage")' in online, True)
# Reload parity: it must ride the persisted judge_evaluation_complete event.
i_evt = online.find('"type": "judge_evaluation_complete"')
i_use = online.find('"judge_usage": judge_usage or []')
check("#46 the breakdown rides the persisted judge event", i_evt >= 0 and i_use > i_evt, True)
# …and must NOT introduce a SECOND cost-bearing event (that would double-count in the
# frontend's turn-cost reconciliation). The type string appears three times — the event
# is CONSTRUCTED once and then delivered twice (broadcast live + persisted for reload) —
# so the invariant is one construction carrying the cost, reused by both deliveries.
check("#46 the cost-bearing payload is constructed exactly once",
      online.count('"judge_usage": judge_usage or []'), 1)
check("#46 …and both deliveries reuse that one payload",
      online.count('"payload": step_evt'), 2)
check("#46 …one of them persisting it for reload",
      "append_system_events_to_turn" in online, True)
check("#46 the card renders the breakdown", "details.judge_usage" in uijs, True)
check("#46 …as a collapsible disclosure", "per-model breakdown" in uijs, True)
# Match the RENDERED string, not the name: the bare phrase also appears in this code's
# own comment ("a single judge is not a panel"), so a loose regex stayed green when the
# rendering was removed. Fourth recurrence of that shape in this work — a comment, a
# docstring, a definition, and now a comment again.
check("#39 …and marks a single judge honestly",
      "&gt; · single judge" in uijs or ";\">· single judge" in uijs, True)

# ── report ──────────────────────────────────────────────────────────────────
print()
if failures:
    print(f"FAILED — {len(failures)} assertion(s)\n")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print("ALL PASS — #46/#39: panel composition and per-model spend, on every judge path.")
