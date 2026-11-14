"""Assessor implementations — one module per PROPERTY, never per lane.

A module here is named for what it assesses (`answer_support.py`), not for the mechanism
that happens to consume it, so remapping reads as "bind `answer_support` to a different
assessor" and both lanes follow from the one act.

Adding one: create `<property>.py`, implement `Assessor`, expose `ASSESSOR` (or
`ASSESSORS` for several). Nothing else changes — this package is DISCOVERED, the registry
registers what it finds, and `conformance` decides which lanes it may serve. A hardcoded
module list was one more place to forget.
"""
import logging
from typing import List

from ..contract import Assessor

logger = logging.getLogger("quart.app")


def _module_names() -> List[str]:
    """Every assessor module in this package, sorted for determinism.

    Sorted because registration order must not depend on filesystem iteration order: two
    deployments discovering the same modules in different orders would produce the same
    registry but different logs and different failure sequences, which is the kind of
    difference that costs a day when one of them misbehaves.
    """
    import pkgutil
    return sorted(
        m.name for m in pkgutil.iter_modules(__path__)
        if not m.name.startswith("_"))


def load_all() -> List[Assessor]:
    import importlib
    out: List[Assessor] = []
    for name in _module_names():
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except Exception as exc:  # noqa: BLE001
            # A shipped module that will not import is a real fault, but taking the whole
            # substrate down means every OTHER property stops being assessed too. Loud and
            # partial beats silent and total.
            logger.error("assurance: assessor module %r failed to import: %s", name, exc)
            continue
        found = getattr(mod, "ASSESSORS", None)
        if found is None:
            one = getattr(mod, "ASSESSOR", None)
            found = [one] if one is not None else []
        out.extend(a for a in found if a is not None)
    return out
