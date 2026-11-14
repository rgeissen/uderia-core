"""
Evidence per result (PROP-009) — the bill of materials a turn seals, and the one content
function every reader re-derives it through.

WHAT A BILL OF MATERIALS IS
---------------------------
The signed parts an answer was produced with, each with the content hash used for the
turn, what its attestation said at that moment, and the attestation-history row that
matched: the agent's own composition, its deployment surface (model, tool catalog,
connectors), and every ingredient (ontology, skills, knowledge and planner repositories,
ontology extensions). Data products read and actions performed are NOT repeated here —
they are sealed by their own receipt steps in the same chain, and the evidence tree joins
them at read time.

WHEN
----
Resolved once, at the start of the turn, at the seam that already resolves the agent's
integrity (``executor.run`` and the genie coordinator's own turn assembly) — the hashes are
computed there anyway; before PROP-009 they were thrown away. A part edited DURING the turn
is recorded as it was at the start; ``meta.resolved`` says so.

HOW IT IS SEALED
----------------
Held in a contextvar for the turn (the OBDA-ACT / OBDA-READ pattern), sealed by
``ProvenanceChain.finalize()`` as a ``trust.bom`` step whose content is
``bom_step_content(bom)`` — a digest of the canonical BOM, so a BOM of any size binds fully
under the chain's 4 KB content cap — and stamped on the persisted turn (``turn['bom']``) at
the one persistence chokepoint. ``provenance.verify_content`` re-derives the step from the
stamp through the SAME function, so an edited stamp is a named mismatch.

Never raises; an absent BOM reads as *"parts not recorded"*, never as an empty tree. This
module belongs to the removable Ontology Suite: with the suite absent no step is sealed.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")

BOM_VERSION = 1
BOM_STEP_TYPE = "trust.bom"

#: The turn's bill of materials. ``None`` outside a turn or when it could not be resolved.
_TURN_BOM: contextvars.ContextVar[Optional[Dict[str, Any]]] = \
    contextvars.ContextVar("uderia_turn_bom", default=None)


def set_turn_bom(bom: Optional[Dict[str, Any]]) -> None:
    """Open the turn with its BOM (or ``None``). Setting a NEW value at every turn start
    means a still-running task from an earlier turn cannot leak its BOM into this one."""
    try:
        _TURN_BOM.set(bom)
    except Exception:  # noqa: BLE001
        pass


def current_turn_bom() -> Optional[Dict[str, Any]]:
    try:
        return _TURN_BOM.get()
    except Exception:  # noqa: BLE001
        return None


def bom_canonical(bom: Dict[str, Any]) -> str:
    """The canonical JSON of a BOM — sorted keys, no whitespace, internal keys (``_``)
    dropped. The ONE serialization both the seal and the verification read."""
    def _clean(v):
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items() if not str(k).startswith("_")}
        if isinstance(v, list):
            return [_clean(x) for x in v]
        return v
    return json.dumps(_clean(bom), sort_keys=True, separators=(",", ":"), default=str)


def bom_digest(bom: Dict[str, Any]) -> str:
    return hashlib.sha256(bom_canonical(bom).encode("utf-8")).hexdigest()


def bom_step_content(bom: Dict[str, Any]) -> str:
    """The content a ``trust.bom`` chain step hashes: the BOM's digest plus its size, so
    the step binds the whole BOM whatever its length (the chain caps content at 4 KB)."""
    return f"bom:v{BOM_VERSION}:{bom_digest(bom)}:{len(all_parts(bom))}"


def bom_step_summary(bom: Dict[str, Any]) -> str:
    parts = all_parts(bom)
    unsigned = sum(1 for p in parts if p.get("state") != "trusted")
    return (f"{len(parts)} part(s) recorded"
            + (f" · {unsigned} not signed-and-intact at turn start" if unsigned else
               " · all signed and intact at turn start"))


def all_parts(bom: Optional[Dict[str, Any]]) -> list:
    """The agent, its deployment and every ingredient, as one flat list."""
    if not isinstance(bom, dict):
        return []
    out = []
    for key in ("agent", "deployment"):
        if isinstance(bom.get(key), dict):
            out.append(bom[key])
    out.extend(p for p in (bom.get("parts") or []) if isinstance(p, dict))
    return out
