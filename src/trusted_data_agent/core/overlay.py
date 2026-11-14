"""Overlay — how a plug-in adds modules and assets the core looks up by name or by directory.

Two seams, both used by the enterprise package (`uderia-enterprise`) and open to any plug-in:

* **Module overlay.** `install_module_overlay(root)` adds a finder at the END of `sys.meta_path`
  that resolves a missing `trusted_data_agent.*` module from `root/trusted_data_agent/…`. The
  core's own modules always win (they are found by the normal path finder first); only a module
  the core does not carry falls through to the overlay. So `from trusted_data_agent.auth import
  saml_provider` — the core's optional imports, and the enterprise modules' absolute imports of
  one another — resolve exactly as in the monorepo, without a namespace-package refactor and
  without importing a package early to extend its `__path__`.

* **Asset roots.** `add_asset_root(path)` registers a repository-shaped directory (it may carry
  `skills/`, `mcp_servers/`, `schema/`, `rag_templates/`, `templates/`, `static/`, `components/`, …). Every
  loader that reads such a directory from the project root asks `asset_roots()` as well, so the
  enterprise skills, connector manifests, schema files, template plug-ins, brand partials and
  brand assets are found beside the core's own.

Both must be installed before the core imports the modules that take the optional imports, which
is why `trusted_data_agent/__init__.py` loads the plug-ins first thing.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("quart.app")

PACKAGE = "trusted_data_agent"

_asset_roots: List[Path] = []
_module_roots: List[Path] = []


class _OverlayFinder(importlib.abc.MetaPathFinder):
    """Resolve a `trusted_data_agent.*` module the core lacks from an overlay root."""

    def __init__(self, root: Path):
        self.root = root

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname != PACKAGE and not fullname.startswith(PACKAGE + "."):
            return None
        rel = Path(*fullname.split("."))
        module_file = self.root / rel.with_suffix(".py")
        if module_file.is_file():
            return importlib.util.spec_from_file_location(fullname, module_file)
        pkg_dir = self.root / rel
        init = pkg_dir / "__init__.py"
        if init.is_file():
            return importlib.util.spec_from_file_location(fullname, init, submodule_search_locations=[str(pkg_dir)])
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OverlayFinder {self.root}>"


def install_module_overlay(root: Path) -> None:
    """Make the modules under `root/trusted_data_agent/…` importable under their core names."""
    root = Path(root).resolve()
    if root in _module_roots:
        return
    if not (root / PACKAGE).is_dir():
        logger.warning("overlay: %s carries no %s/ directory — ignored", root, PACKAGE)
        return
    _module_roots.append(root)
    sys.meta_path.append(_OverlayFinder(root))
    logger.info("overlay: modules from %s", root)


def module_roots() -> List[Path]:
    return list(_module_roots)


def add_asset_root(path: Path) -> None:
    """Register a repository-shaped directory whose assets the loaders should see."""
    path = Path(path).resolve()
    if path in _asset_roots:
        return
    if not path.is_dir():
        logger.warning("overlay: asset root %s does not exist — ignored", path)
        return
    _asset_roots.append(path)
    # `components` is a namespace package (no __init__.py): an asset root carrying one joins it
    # through sys.path, so `components.builtin.<id>` of an enterprise component imports under
    # the name the core uses (PEP 420 recomputes the package path when sys.path changes).
    if (path / "components").is_dir() and str(path) not in sys.path:
        sys.path.append(str(path))
    logger.info("overlay: assets from %s", path)


def asset_roots() -> List[Path]:
    return list(_asset_roots)


def asset_dirs(sub: str) -> List[Path]:
    """The `sub` directory (e.g. "skills", "schema") of every asset root that has one."""
    return [r / sub for r in _asset_roots if (r / sub).is_dir()]


def find_asset(rel: str) -> Optional[Path]:
    """The first asset root that carries `rel` (a project-relative path), or None."""
    for r in _asset_roots:
        p = r / rel
        if p.exists():
            return p
    return None


def reset() -> None:
    """Test hook: forget every root (finders already on sys.meta_path are removed)."""
    global _asset_roots, _module_roots
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _OverlayFinder)]
    _asset_roots, _module_roots = [], []
