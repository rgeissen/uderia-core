"""Entitlements — what a licence key says a deployment may run, and the ONE rule that applies it.

A licence key (``core/license_file``) carries a tier; a tier in ``LICENSED_TIERS`` activates the
enterprise package. Since Nov 2026 the key may also carry **entitlements**: the groups of the
enterprise package this licensee has bought, named as the distribution manifest
(``oss/public_core.json``) groups them, plus an optional list of enterprise **components**::

    "entitlements": ["enterprise-components", "enterprise-identity"],   # absent = every group
    "components": ["obda_read", "action"]                                # absent = every component

The rule the whole platform applies: **a group the key does not entitle behaves exactly as the
public core behaves without it** — its modules are not importable (the core's optional imports
fall through, as they do in the public core), its components are not installed, its template
blocks are not rendered, its skills, connectors and templates are not listed. There is no second
"not licensed" state anywhere: the resource is simply not *available*. A key without the field is
the whole package, which is what every key issued before this module exists means.

Where it is applied (every reader asks here, nowhere else holds a list):

* the enterprise package's ``register()`` installs only the entitled groups' overlay and asset
  roots (the package is built one root per group — ``scripts/build_enterprise_package.py``);
* in the development repository, where every file is on the path, ``install_import_guard()``
  (armed by ``main.py`` at import, so the SERVER is governed, not a test or a script) refuses the
  import of a module the manifest assigns to a group the key does not entitle;
* ``components/manager.py`` asks ``component_entitled`` before loading a builtin component;
* ``main.py`` renders the page through ``strip_unentitled_blocks`` (the ``{# enterprise-begin:x #}``
  blocks of a group the key does not entitle are removed, as the public export removes them);
* the skills loader and the connector registry ask ``dir_entitled`` for a builtin directory.

**The licence governs the running server.** The gates below (components, paths, blocks, the
import guard) apply once the process is *armed*: ``main.py`` arms when it is run as the server
(``python -m trusted_data_agent.main``), the enterprise package arms when it activates, a test arms
through ``set_for_tests``. A test or a script that merely imports the code is not a deployment and
sees the whole repository; the readers (``entitlements``, ``is_entitled``, ``summary``) report the
key either way.

This is a statement of what was bought that the software honours, not a lock: a licensee holds
the source. Gate: ``test/test_license_entitlements.py``.
"""
from __future__ import annotations

import fnmatch
import importlib.abc
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, Optional

logger = logging.getLogger("quart.app")

#: The groups a key may entitle — the enterprise package's shipped groups, less the test groups
#: (``scripts/build_enterprise_package.SHIPPED_GROUPS``; the gate holds the two together).
GROUPS: tuple[str, ...] = (
    "enterprise-components",   # the three OBDA planes: OBDA-READ, OBDA-MODEL, OBDA-ACT (12 Nov 2026: the other six are public)
    "enterprise-identity",     # SSO: OIDC and SAML, and the Google/GitHub sign-in (12 Nov 2026)
    "enterprise-scale",        # the external key managers and white-label branding by host (12 Nov 2026: the storage backends are public)
    "prompt-editing",          # the system-prompt editor
    "trust-signing",           # the acts of signing: sign-off, attestation, revocation (the core reads trust, it does not sign)
)

#: The enterprise components (``components/builtin/<id>`` in manifest group enterprise-components).
ENTERPRISE_COMPONENTS: FrozenSet[str] = frozenset({"obda_read", "obda_model", "action"})

#: The template blocks (``{# enterprise-begin:<name> #}`` in templates/index.html) and their group.
BLOCK_GROUPS: Dict[str, str] = {
    "sso": "enterprise-identity",
    "prompts": "prompt-editing",
    "obda_policy": "enterprise-components",
    "action_panel": "enterprise-components", "obda_read_panel": "enterprise-components",
    "obda_model_panel": "enterprise-components",
}

#: A block's component, where the block belongs to ONE component (a sub-list in the key may
#: withhold it while the group is entitled). A block without an entry follows its group alone.
BLOCK_COMPONENTS: Dict[str, str] = {
    "action_panel": "action", "obda_read_panel": "obda_read", "obda_model_panel": "obda_model",
}

BLOCK_MARK = re.compile(r"[ \t]*\{#\s*enterprise-begin:(\w+)\s*#\}.*?\{#\s*enterprise-end:\1\s*#\}[ \t]*\n?", re.S)


def enterprise_blocks(text: str) -> dict:
    """``{name: html}`` for every marked block of a template. A name used twice is concatenated."""
    out: dict = {}
    for m in BLOCK_MARK.finditer(text):
        body = m.group(0)
        body = body[body.index("#}") + 2:]
        body = body[:body.rindex("{#")]
        out[m.group(1)] = out.get(m.group(1), "") + body
    return out


# ── the licence, read once ────────────────────────────────────────────────────────────────────

_payload: Optional[dict] = None
_loaded = False
_armed = False
_warned: set = set()


def arm() -> None:
    """Make the licence govern this process (the server does this; so does the enterprise package)."""
    global _armed
    _armed = True


def armed() -> bool:
    return _armed


def license_payload() -> Optional[dict]:
    """The validated licence payload of this process, read once (``license_file.load_validated``);
    None without a valid licence. Mirrors into ``APP_STATE['license_info']`` (Community when none),
    and takes a payload another reader (the enterprise package's ``register()``) put there first."""
    global _payload, _loaded
    if _loaded:
        return _payload
    from trusted_data_agent.core import license_file
    from trusted_data_agent.core.config import APP_STATE
    info = APP_STATE.get("license_info")
    if isinstance(info, dict) and info.get("tier") in license_file.LICENSED_TIERS:
        _payload = dict(info)
    else:
        _payload = license_file.load_validated(logger)
        APP_STATE["license_info"] = _payload or {"holder": "Community", "tier": "Community"}
    _loaded = True
    return _payload


def set_for_tests(payload: Optional[dict]) -> None:
    """Test hook: make this payload the licence of the process (None = no licence)."""
    global _payload, _loaded, _armed
    _payload, _loaded, _armed = (dict(payload) if payload else None), True, True
    _warned.clear()


def reset() -> None:
    """Test hook: forget the licence and disarm, so the next reader loads it again."""
    global _payload, _loaded, _armed
    _payload, _loaded, _armed = None, False, False
    _warned.clear()


def is_licensed() -> bool:
    from trusted_data_agent.core.license_file import LICENSED_TIERS
    p = license_payload()
    return bool(p) and p.get("tier") in LICENSED_TIERS


def entitlements() -> FrozenSet[str]:
    """The groups this deployment may run: every group on a licensed key without the field, the
    named ones (unknown names ignored, once logged) with it, none without a licence."""
    if not is_licensed():
        return frozenset()
    named = license_payload().get("entitlements")
    if named is None:
        return frozenset(GROUPS)
    if not isinstance(named, (list, tuple)):
        _warn_once("field", f"licence field 'entitlements' is not a list ({type(named).__name__}); no group is entitled")
        return frozenset()
    out = frozenset(g for g in named if g in GROUPS)
    for g in named:
        if g not in GROUPS:
            _warn_once(f"group:{g}", f"licence names an unknown entitlement {g!r} — ignored (known: {', '.join(GROUPS)})")
    return out


def is_entitled(group: str) -> bool:
    return group in entitlements()


def entitled_components() -> FrozenSet[str]:
    """The enterprise components this deployment may install: all nine when the group is entitled
    and the key names none, the named ones otherwise, none without the group."""
    if not is_entitled("enterprise-components"):
        return frozenset()
    named = license_payload().get("components")
    if named is None:
        return ENTERPRISE_COMPONENTS
    if not isinstance(named, (list, tuple)):
        _warn_once("components", "licence field 'components' is not a list; no enterprise component is entitled")
        return frozenset()
    for c in named:
        if c not in ENTERPRISE_COMPONENTS:
            _warn_once(f"component:{c}", f"licence names an unknown enterprise component {c!r} — ignored")
    return frozenset(c for c in named if c in ENTERPRISE_COMPONENTS)


def component_entitled(component_id: str) -> bool:
    """A public component always; an enterprise component when the key entitles it (an unarmed
    process — a test, a script — is not a deployment and sees every component)."""
    if component_id not in ENTERPRISE_COMPONENTS or not _armed:
        return True
    return component_id in entitled_components()


def block_entitled(name: str) -> bool:
    """Whether a template block (``{# enterprise-begin:name #}``) is rendered. A block the table
    does not know is rendered: the gate holds the table to the template's markers."""
    group = BLOCK_GROUPS.get(name)
    if group is None or not _armed:
        return True
    if not is_entitled(group):
        return False
    comp = BLOCK_COMPONENTS.get(name)
    return comp is None or component_entitled(comp)


def strip_unentitled_blocks(text: str) -> str:
    """The template without the blocks this deployment is not entitled to — what the public export
    does for every block, done at render time for the ones the key withholds."""
    if not any(not block_entitled(n) for n in enterprise_blocks(text)):
        return text
    return BLOCK_MARK.sub(lambda m: m.group(0) if block_entitled(m.group(1)) else "", text)


def summary() -> dict:
    """What the About box and ``/auth/me`` report: the tier, the groups, the components."""
    p = license_payload() or {}
    return {
        "tier": p.get("tier") or "Community",
        "holder": p.get("holder"),
        "expires_at": p.get("expires_at"),
        "entitlements": sorted(entitlements()),
        "components": sorted(entitled_components()),
        "all_groups": p.get("entitlements") is None and is_licensed(),
    }


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg)


# ── the manifest: which group a repository path belongs to (development repository) ───────────

_rules: Optional[list] = None


def manifest_path() -> Path:
    from trusted_data_agent.core.utils import get_project_root
    return Path(get_project_root()) / "oss" / "public_core.json"


def _manifest_rules() -> list:
    """The manifest's exclude rules for the entitlement groups as ``(pattern, group, component)``;
    empty where the manifest is absent (the public core and the merged layout, where the package's
    roots decide instead). A rule of the components group names its component (``"component"``),
    or is a ``components/builtin/<id>/**`` rule, whose component is the directory."""
    global _rules
    if _rules is None:
        rules: list = []
        p = manifest_path()
        if p.is_file():
            try:
                for r in json.loads(p.read_text(encoding="utf-8")).get("rules", []):
                    if r.get("action") == "exclude" and r.get("group") in GROUPS:
                        rules.append((r["pattern"], r["group"], rule_component(r)))
            except (OSError, ValueError) as e:  # noqa: BLE001
                logger.warning(f"entitlements: the manifest {p} is unreadable ({e}); nothing is gated by path")
        _rules = rules
    return _rules


def rule_component(rule: dict) -> Optional[str]:
    """The component a manifest rule belongs to: its ``component`` field, else the directory of a
    ``components/builtin/<id>/**`` pattern, else None (the rule follows its group alone)."""
    comp = rule.get("component")
    if comp:
        return comp
    pat = rule.get("pattern", "")
    if pat.startswith("components/builtin/"):
        parts = pat.split("/")
        if len(parts) >= 3 and parts[2] and "*" not in parts[2]:
            return parts[2]
    return None


def unit_of_path(rel: str) -> tuple:
    """``(group, component)`` of a repository-relative path — ``(None, None)`` for a public file or
    without a manifest; the component is None when the file follows its group alone."""
    rel = rel.replace(os.sep, "/")
    for pattern, group, component in _manifest_rules():
        if fnmatch.fnmatchcase(rel, pattern):
            return group, component
    return None, None


def group_of_path(rel: str) -> Optional[str]:
    """The entitlement group of a repository-relative path, or None (public, or no manifest)."""
    return unit_of_path(rel)[0]


def _unit_entitled(group: Optional[str], component: Optional[str]) -> bool:
    if group is None:
        return True
    if not is_entitled(group):
        return False
    return component is None or component_entitled(component)


def path_entitled(rel: str) -> bool:
    """Whether this deployment may use the file at a repository-relative path: its group must be
    entitled and, where the file belongs to one enterprise component, that component too."""
    if not _armed:
        return True
    return _unit_entitled(*unit_of_path(rel))


def dir_entitled(rel_dir: str) -> bool:
    """Whether a builtin directory (``skills/builtin/<id>``, ``mcp_servers/builtin/<id>``,
    ``components/builtin/<id>``) is this deployment's to use."""
    return path_entitled(rel_dir.rstrip("/") + "/_")


# ── the import guard (development repository) ─────────────────────────────────────────────────

PACKAGE = "trusted_data_agent"


class _EntitlementGuard(importlib.abc.MetaPathFinder):
    """Refuses the import of a core module whose file the manifest assigns to a group the licence
    does not entitle — so the core's optional imports fall through exactly as in the public core."""

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == PACKAGE or not fullname.startswith(PACKAGE + "."):
            return None
        if not _armed:
            return None
        rel = "src/" + "/".join(fullname.split("."))
        for candidate in (rel + ".py", rel + "/__init__.py"):
            g, c = unit_of_path(candidate)
            if g is not None and not _unit_entitled(g, c):
                what = f"entitlement {g!r}" + (f", component {c!r}" if c and is_entitled(g) else "")
                raise ModuleNotFoundError(
                    f"{fullname} is not licensed in this deployment ({what}); the public core runs without it",
                    name=fullname)
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return "<EntitlementGuard>"


def install_import_guard() -> bool:
    """Arm the guard at the FRONT of ``sys.meta_path``; returns whether anything is gated (a
    manifest exists and the licence withholds a group). Idempotent. Modules a non-entitled group
    owns that are already imported are dropped from ``sys.modules`` so a later import asks again."""
    arm()
    if any(isinstance(f, _EntitlementGuard) for f in sys.meta_path):
        return bool(_manifest_rules()) and entitlements() != frozenset(GROUPS)
    if not _manifest_rules():
        return False
    sys.meta_path.insert(0, _EntitlementGuard())
    withheld = sorted(set(GROUPS) - entitlements())
    withheld_components = sorted(ENTERPRISE_COMPONENTS - entitled_components()) if is_entitled("enterprise-components") else []
    if withheld or withheld_components:
        for name in [n for n in list(sys.modules) if n.startswith(PACKAGE + ".")]:
            rel = "src/" + "/".join(name.split("."))
            if any(not _unit_entitled(*unit_of_path(c)) for c in (rel + ".py", rel + "/__init__.py")):
                del sys.modules[name]
        logger.info("entitlements: the licence withholds " + ", ".join(withheld + [f"component {c}" for c in withheld_components])
                    + " — the deployment runs without them, as the public core does")
    return bool(withheld or withheld_components)


def uninstall_import_guard() -> None:
    """Test hook."""
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _EntitlementGuard)]


def reset_manifest() -> None:
    """Test hook: re-read the manifest on the next question."""
    global _rules
    _rules = None
