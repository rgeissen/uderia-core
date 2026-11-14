"""
Single source of truth for the deployed Uderia version string.

Resolution precedence (first non-empty wins), computed once and cached:

  1. ``UDERIA_VERSION`` environment variable — the explicit deploy stamp. In
     production this is baked at image build time from the release tag (see the
     ``UDERIA_VERSION`` build-arg in the Dockerfile), so the running container
     reports exactly what was built without any runtime git dependency.
  2. ``git describe --tags`` — accurate for developer checkouts and any host
     where the ``git`` binary and repository are present. Guarded by
     ``shutil.which`` so it is skipped (never raised) when git is unavailable,
     which is the case inside the slim production image.
  3. Installed package metadata (``pyproject.toml`` version via
     ``importlib.metadata``) — the git-independent fallback that always works
     inside the container.
  4. ``"unknown"`` — last resort; the app never crashes over a version lookup.

Kept dependency-free and side-effect-free so it can be imported anywhere.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess

# Repository root: .../src/trusted_data_agent/core/version.py -> up 4 levels.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

_PACKAGE_NAME = "uderia"


def _from_env() -> str | None:
    value = os.environ.get("UDERIA_VERSION", "").strip()
    return value or None


def _from_git() -> str | None:
    if not shutil.which("git"):
        return None
    if not os.path.isdir(os.path.join(_PROJECT_ROOT, ".git")):
        return None
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _from_metadata() -> str | None:
    try:
        from importlib.metadata import version

        value = version(_PACKAGE_NAME).strip()
    except Exception:  # never fail a version lookup over metadata resolution
        return None
    return value or None


@functools.lru_cache(maxsize=1)
def get_app_version() -> str:
    """Return the deployed Uderia version string (cached for process lifetime)."""
    for resolver in (_from_env, _from_git, _from_metadata):
        value = resolver()
        if value:
            return value
    return "unknown"
