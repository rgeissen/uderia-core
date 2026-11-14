"""
The assurance-module plane (PROP-003) — third-party properties, measures and rubrics.

The fifth plugin plane, and the only one whose code runs inside the assurance path. It
therefore reuses the house shape (manifest + manager + `~/.tda/` dir + settings KV +
marketplace) but NOT the extension plane's execution model: privilege is earned by proof,
in three tiers (declarative → verified-pure → scanned/post-hoc-only), and a third-party
module must be signed before it can be BOUND.

Design: docs/proposals/PROP-003-assurance-module-plane.md
"""

from . import settings  # noqa: F401
from .manager import (  # noqa: F401
    AssuranceModuleManager, get_assurance_module_manager,
    reset_assurance_module_manager,
)
from .verifier import verify_source  # noqa: F401
