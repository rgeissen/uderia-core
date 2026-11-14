"""
S1.1 — the checks the platform ran on an answer, as ONE compact record.

The Live Status stream only announces a check that ACTED (a clean guardrail is quiet by
design), while the persisted turn stamps every check that RAN. The answer's verdict
("Verified · N findings · Not checked") must rest on the same facts whether the reader
watched the turn or opened it later, so the `final_answer` frame carries this summary and
the reload reads the stamps it was summarised from — one shape, two moments.

Pure: no I/O, never raises; an absent or malformed event contributes nothing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _d(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _n(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, (list, tuple, set)):
        return len(v)
    return None


def checks_summary(
    grounding: Any = None,
    guardrail: Any = None,
    honesty: Any = None,
    compute_lineage: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """The checks that RAN on this answer — keys present only for a check that ran."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        g = _d(grounding)
        if g.get("decision"):
            out["grounding"] = {"decision": str(g.get("decision"))}
        gu = _d(guardrail)
        if gu.get("ran"):
            out["guardrail"] = {"ran": True, "acted": bool(gu.get("acted")), "action": str(gu.get("action") or "pass")}
        h = _d(honesty)
        if h.get("ran"):
            out["honesty"] = {"ran": True, "acted": bool(h.get("acted")), "action": str(h.get("action") or "pass")}
        cl = _d(compute_lineage)
        if cl.get("ran") and ((_n(cl.get("figures")) or 0) or (_n(cl.get("receipts")) or 0)):
            out["compute_lineage"] = {
                "ran": True,
                "figures": _n(cl.get("figures")) or 0,
                "receipts": _n(cl.get("receipts")) or 0,
                "unreceipted": _n(cl.get("unreceipted")) or 0,
            }
    except Exception:  # noqa: BLE001 — a summary must never break the answer
        return {}
    return out
