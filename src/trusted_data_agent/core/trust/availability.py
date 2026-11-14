"""
Trust Membrane availability — the single distribution-isolation + runtime-gating seam.

The Trust Membrane (Tier 5.2) is an **isolated, optional capability module**. This
file is the ONE place that decides whether the feature is active, so that:

  1. Distribution isolation (now): the whole feature is a contiguous, removable set
     (see the manifest in this package's README). If it is excluded from a build,
     the optional import in ``main.py`` fails and the platform runs without it —
     every integration point degrades gracefully.
  2. Runtime gating (later): making the Trust Membrane Enterprise-only is a
     **one-line change to ``is_licensed()`` below** — no other file changes.

``is_available()`` is the single decision every seam consults (backend registration
+ the frontend template flag). Keep it that way — never scatter the gate.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("quart.app")


def is_licensed() -> bool:
    """Runtime license gate for the Trust Membrane.

    The Trust Membrane lives *inside* the Ontology suite, so it follows the same
    master switch — ``core.enterprise.is_enterprise_ontology_enabled()``. There
    is ONE place to flip (that master gate); this just defers to it. **Today it
    returns True.** Falls back to True if the master gate is somehow absent.
    """
    try:
        from trusted_data_agent.core.enterprise import is_enterprise_ontology_enabled
        return is_enterprise_ontology_enabled()
    except Exception:
        # TC-64 (deliberate, but a standing reminder): this fail-open is harmless while the
        # suite is free for all tiers — the day the master gate flips to Enterprise-only,
        # this branch becomes a licensing bypass and MUST flip to fail-closed in the same
        # change. Search key: TRUST_LICENSE_FAIL_OPEN.
        return True


def is_available() -> bool:
    """The single availability decision (module present + licensed).

    Module *presence* is enforced structurally: if the package is removed from the
    build, importing this function (and the engine) fails and ``main.py`` skips
    registration. So here we only need the license gate.
    """
    return is_licensed()
