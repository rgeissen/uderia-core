"""Judge calibration — measure judge↔human agreement before trusting a judge.

A judge that doesn't agree with humans is noise. Following the methodology
research, every judge dimension should be calibrated against a small set of
human-labelled cases (Cohen's κ + raw agreement) and only promoted to a *gating*
signal once it clears a κ floor. This is pure arithmetic — CI-pure, no LLM.

Usage:
    labels = [("pass", "pass"), ("fail", "pass"), ...]   # (human, judge)
    report = calibrate(labels)
    report["cohen_kappa"], report["agreement"], report["gating_ok"]
"""
from __future__ import annotations

# Substantial agreement (Landis & Koch) — a judge below this stays advisory.
DEFAULT_KAPPA_GATING_FLOOR = 0.6

# κ over a handful of pairs is noise wearing a statistic's clothes: with 3 pairs a
# single lucky agreement clears 0.6. Below this floor the κ is still REPORTED
# (measurement is never hidden) but can never clear the gate.
DEFAULT_MIN_PAIRS = 10


def _to_binary(v) -> bool | None:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("pass", "true", "1", "yes", "correct"):
        return True
    if s in ("fail", "false", "0", "no", "incorrect"):
        return False
    return None  # unknown / abstain — excluded from κ


def cohen_kappa(pairs: list[tuple]) -> float | None:
    """Cohen's κ over (human, judge) label pairs. Abstentions excluded.

    Returns None if there is nothing decidable. κ=1 perfect, 0 chance, <0 worse
    than chance. When both raters are unanimous and agree, κ is defined as 1.0.
    """
    decided = [(_to_binary(h), _to_binary(j)) for h, j in pairs]
    decided = [(h, j) for h, j in decided if h is not None and j is not None]
    n = len(decided)
    if n == 0:
        return None
    agree = sum(1 for h, j in decided if h == j)
    po = agree / n
    h_true = sum(1 for h, _ in decided if h) / n
    j_true = sum(1 for _, j in decided if j) / n
    pe = h_true * j_true + (1 - h_true) * (1 - j_true)
    if pe >= 1.0:                     # both raters constant & identical
        return 1.0 if po == 1.0 else 0.0
    return round((po - pe) / (1 - pe), 4)


def calibrate(pairs: list[tuple], kappa_floor: float = DEFAULT_KAPPA_GATING_FLOOR) -> dict:
    """Full calibration report for one judge dimension."""
    decided = [(_to_binary(h), _to_binary(j)) for h, j in pairs]
    decided = [(h, j) for h, j in decided if h is not None and j is not None]
    n = len(decided)
    agree = sum(1 for h, j in decided if h == j) if n else 0
    # confusion (human as ground truth)
    tp = sum(1 for h, j in decided if h and j)
    tn = sum(1 for h, j in decided if not h and not j)
    fp = sum(1 for h, j in decided if not h and j)
    fn = sum(1 for h, j in decided if h and not j)
    kappa = cohen_kappa(pairs)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "n_pairs": len(pairs),
        "n_decided": n,
        "n_abstained": len(pairs) - n,
        "agreement": round(agree / n, 4) if n else None,
        "cohen_kappa": kappa,
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "kappa_floor": kappa_floor,
        "gating_ok": bool(kappa is not None and kappa >= kappa_floor),
    }


# ── annotations → κ (the loop the annotation queue exists to close) ───────────
def pairs_from_annotations(annotations: list[dict]) -> dict:
    """Group stored annotations into per-dimension (human, judge) pairs.

    An annotation records the HUMAN's stance relative to the ruling they saw:
    ``label='agree'`` → human verdict == the judge's; ``'disagree'`` → the
    opposite. ``judge_passed`` is the ruling frozen ON the row at annotation
    time — never re-derived from the turn, because a later re-score can flip
    the judge and would silently rewrite what the human agreed with.

    Rows that cannot form a pair (no dimension, no recorded ruling, or a label
    outside agree/disagree) are skipped — they are notes, not calibration
    evidence, and they still render in the annotation list.
    """
    out: dict = {}
    for a in annotations or []:
        dim = a.get("dimension")
        jp = a.get("judge_passed")
        label = str(a.get("label") or "").strip().lower()
        if not dim or jp is None or label not in ("agree", "disagree"):
            continue
        judge = bool(jp)
        human = judge if label == "agree" else (not judge)
        out.setdefault(dim, []).append((human, judge))
    return out


def report_from_annotations(annotations: list[dict],
                            kappa_floor: float = DEFAULT_KAPPA_GATING_FLOOR,
                            min_pairs: int = DEFAULT_MIN_PAIRS) -> dict:
    """Per-dimension calibration report over the stored annotation queue.

    Adds a ``status`` beside the raw ``calibrate()`` report:
      ``calibrated``   — κ ≥ floor over ≥ min_pairs (ELIGIBLE for promotion;
                         promotion itself stays a deliberate governance act)
      ``below_floor``  — enough pairs, κ under the floor: the judge disagrees
                         with humans too often to be trusted with a gate
      ``insufficient`` — fewer than min_pairs: κ is reported, never cleared
    """
    reports = {}
    for dim, pairs in pairs_from_annotations(annotations).items():
        r = calibrate(pairs, kappa_floor=kappa_floor)
        if r["n_decided"] < min_pairs:
            r["gating_ok"] = False
            r["status"] = "insufficient"
        else:
            r["status"] = "calibrated" if r["gating_ok"] else "below_floor"
        r["min_pairs"] = min_pairs
        reports[dim] = r
    return reports
