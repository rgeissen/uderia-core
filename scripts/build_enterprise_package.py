#!/usr/bin/env python3
"""Build the enterprise package, `uderia-enterprise`, from this repository.

The second export of the open core (docs/roadmap/OPEN_CORE_PLAN.md). Where
`build_public_core.py` writes what the manifest includes, this writes what it excludes, in the
groups that ship to an enterprise or partner licensee, as an installable Python distribution:

    uderia_enterprise/__init__.py        register(): installs the module overlay and the asset root
    uderia_enterprise/overlay/trusted_data_agent/…   the enterprise Python modules, same relative paths
    uderia_enterprise/assets/…           skills, mcp_servers, schema, rag_templates, templates, static,
                                         agent_packs, deploy, mcp_custom_objects, scripts, maintenance
    uderia_enterprise/tests/…            the suites that moved with the modules they import or read
    pyproject.toml                       entry point  uderia.plugins: enterprise = uderia_enterprise:register
    LICENSE                              the enterprise licence outline (legal/ENTERPRISE-LICENSE.md)

Installed beside the public core (`pip install -e dist/uderia-enterprise`), the package is loaded
by `trusted_data_agent/__init__.py` before any core module, so every optional import in the core
resolves to the same module it resolves to in the monorepo. The acceptance of Phase 2 is that
the monorepo and "core + package" behave identically (scripts/verify_open_core.py).

Groups shipped:   enterprise-identity · enterprise-scale · enterprise-components (the OBDA planes) ·
                  prompt-editing · trust-signing · tests-of-excluded · tests-of-repo-shape ·
                  tests-of-dev-fixtures
Groups never shipped: secrets-and-state · third-party-content · working-notes · tests-of-monorepo (this repository's own gates)

Usage:
    python scripts/build_enterprise_package.py --out dist/uderia-enterprise
    python scripts/build_enterprise_package.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_public_core as core  # noqa: E402
from trusted_data_agent.core.entitlements import BLOCK_COMPONENTS, BLOCK_GROUPS, GROUPS as ENTITLEMENT_GROUPS, rule_component  # noqa: E402  (src on the path via core)

SHIPPED_GROUPS = ("enterprise-identity", "enterprise-scale", "enterprise-components",
                  "prompt-editing", "trust-signing", "tests-of-excluded",
                  "tests-of-repo-shape", "tests-of-dev-fixtures")
NEVER = ("secrets-and-state", "third-party-content", "working-notes", "tests-of-monorepo",
         "licence-review")   # licence-review: the Industry Data Model constructor, in neither distribution (Nov 2026)

ASSET_PREFIXES = ("skills/", "mcp_servers/", "schema/", "rag_templates/", "templates/", "static/",
                  "agent_packs/", "deploy/", "mcp_custom_objects/", "scripts/", "maintenance/",
                  "components/", "extensions/", "assurance_modules/", "benchmarks/", "tutorials/")


def classify_for_package(decided: dict) -> dict[str, list[str]]:
    """-> {"overlay": [...], "assets": [...], "tests": [...], "dropped": [...], "group": {file: group},
    "unit": {file: "group" | "group/component"}} over the excluded files. The unit is the file's
    entitlement (core/entitlements): the package is laid out one root per unit, so `register()`
    installs exactly the entitled ones."""
    out = {"overlay": [], "assets": [], "tests": [], "dropped": [], "group": {}, "unit": {}}
    for f, rule in sorted(decided.items()):
        if rule["action"] != "exclude":
            continue
        g = rule["group"]
        if g in NEVER:
            out["dropped"].append(f)
        elif f.startswith("test/"):
            out["tests"].append(f)
        elif f.startswith("src/trusted_data_agent/"):
            out["overlay"].append(f); out["group"][f] = g; out["unit"][f] = _unit(rule)
        elif f.startswith(ASSET_PREFIXES):
            out["assets"].append(f); out["group"][f] = g; out["unit"][f] = _unit(rule)
        else:
            out["dropped"].append(f)
    return out


def _unit(rule: dict) -> str:
    comp = rule_component(rule)
    return f"{rule['group']}/{comp}" if comp else rule["group"]


def distributions_table() -> str:
    """The core-versus-enterprise comparison, ONE source: the block between the
    `distributions-table` markers of the repository README (which the public core ships as its
    own README), so both distributions state the same split."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    a = readme.index("<!-- distributions-table-begin -->") + len("<!-- distributions-table-begin -->")
    b = readme.index("<!-- distributions-table-end -->")
    return readme[a:b].strip()


def version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


INIT = '''"""Uderia Enterprise Package — installed beside the public core, it restores the full stack.

Loaded by the core before any of its own modules (the `uderia.plugins` entry point). It adds no
behaviour of its own: on a valid Enterprise or Partner licence it makes the enterprise modules
importable under their core names and registers its assets, and the core's optional imports and
loaders do the rest. Without such a licence it stays dormant and the public core runs alone.

The package is laid out ONE ROOT PER ENTITLEMENT (`overlay/<group>/…`, `assets/<group>/…`, the
groups of core/entitlements.GROUPS; within the components group, each component's files under
`<group>/<component>/…`): a licence that names entitlements activates exactly those, and what it
withholds is absent from this process as it is from the public core.
"""
import logging
from pathlib import Path

HERE = Path(__file__).resolve().parent
OVERLAY = HERE / "overlay"
ASSETS = HERE / "assets"

__version__ = "{version}"


def register() -> None:
    """Activate the package — only on a licence of a tier in core/license_file.LICENSED_TIERS, and
    only the groups that licence entitles (core/entitlements)."""
    from trusted_data_agent.core import entitlements, license_file, overlay
    from trusted_data_agent.core.config import APP_STATE

    log = logging.getLogger("quart.app")
    payload = license_file.load_validated(log)
    tier = (payload or {{}}).get("tier")
    if tier not in license_file.LICENSED_TIERS:
        why = (f"licence tier {{tier!r}} does not activate it (needs one of {{license_file.LICENSED_TIERS}})"
               if payload else "no valid licence file (tda_keys/license.local.key)")
        log.warning(f"uderia-enterprise {{__version__}} is installed but not activated: {{why}} — the public core runs alone")
        return
    APP_STATE["license_info"] = payload
    entitlements.arm()
    entitled = entitlements.entitlements()
    components = entitlements.ENTERPRISE_COMPONENTS
    shipped = sorted({{d.name for base in (OVERLAY, ASSETS) if base.is_dir() for d in base.iterdir() if d.is_dir()}})
    withheld = [g for g in shipped if g not in entitled]
    for group in shipped:
        if group not in entitled:
            continue
        for base, install in ((OVERLAY, overlay.install_module_overlay), (ASSETS, overlay.add_asset_root)):
            root = base / group
            if not root.is_dir():
                continue
            # the group's own files (a component's files sit in a root of their own below)
            if any(d.name not in components for d in root.iterdir()):
                install(root)
            for comp_root in sorted(d for d in root.iterdir() if d.is_dir() and d.name in components):
                if entitlements.component_entitled(comp_root.name):
                    install(comp_root)
                elif f"component {{comp_root.name}}" not in withheld:
                    withheld.append(f"component {{comp_root.name}}")
    log.info(f"uderia-enterprise {{__version__}} activated for {{payload.get('holder', '?')}} ({{tier}}): "
             + ", ".join(g for g in shipped if g in entitled)
             + (f" — the licence withholds {{', '.join(withheld)}}" if withheld else ""))
'''

PYPROJECT = '''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "uderia-enterprise"
version = "{version}"
description = "The enterprise package of Uderia: the three OBDA planes (READ, MODEL, ACT), identity federation (OIDC, SAML 2.0, Google and GitHub sign-in), external key managers, white-label branding, the system-prompt editor and the acts of signing. Installed beside the public core; activated by an Enterprise or Partner licence."
readme = "README.md"
requires-python = ">=3.12"
license = {{file = "LICENSE"}}
dependencies = [{dependencies}]

[project.entry-points."uderia.plugins"]
enterprise = "uderia_enterprise:register"

[tool.setuptools]
packages = ["uderia_enterprise"]
include-package-data = true

[tool.setuptools.package-data]
uderia_enterprise = ["overlay/**/*", "assets/**/*", "tests/**/*"]
'''

README = '''# Uderia Enterprise Package

Installed beside the public core, this package restores the full stack: identity federation
(OIDC, SAML 2.0), Postgres sessions, the S3, Azure Blob and Google Cloud Storage file stores with
external key managers, white-label branding, the Teradata vector store, catalog, deploy backend
and skills, the Google, Microsoft 365 and Slack connectors, the system-prompt editor, the ANDP
constructor, and the domain agent packs.

It is loaded by the core at import (the `uderia.plugins` entry point) and adds its modules and
assets under the names the core already knows — once it has validated the deployment's licence:
a signed `tda_keys/license.local.key` (or `license.key`) of tier Enterprise or Partner, checked
against `tda_keys/public_key.pem`. Without one the package stays dormant, the log says so, and
the public core runs alone. Licensed under the Uderia Enterprise Licence; see LICENSE.

The system prompts are not part of the package: they are plain text in the public core
(`schema/default_prompts/`). The package adds the editor that lets a licensed deployment
override them per user and keep their history.

## Public core and enterprise package, side by side

{table}

    pip install uderia-enterprise-{version}.tar.gz
    python maintenance/platform_operations.py backlog --write   # the operations catalog now
    python maintenance/platform_operations.py docs --write      # describes the merged routes

The package's own dependencies (the SDKs of the identity, storage and graph backends it adds)
are declared in its `pyproject.toml`; the public core's `requirements.txt` does not list them.
'''


def write(out: Path, parts: dict[str, list[str]]) -> None:
    if out.exists() and any(out.iterdir()):
        sys.exit(f"refusing to write into a non-empty directory: {out}")
    pkg = out / "uderia_enterprise"
    # one root per entitlement group (core/entitlements.GROUPS): register() installs the entitled ones
    for f in parts["overlay"]:
        dst = pkg / "overlay" / parts["unit"][f] / f[len("src/"):]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    for f in parts["assets"]:
        dst = pkg / "assets" / parts["unit"][f] / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    # the template blocks the public export strips (`{# enterprise-begin:x #}`) — shipped as the
    # partials the stripped template includes (`{% include "enterprise/x.html" ignore missing %}`),
    # each under the root of its group (core/entitlements.BLOCK_GROUPS)
    for tpl in ("templates/index.html",):
        for name, html in core.enterprise_blocks((ROOT / tpl).read_text(encoding="utf-8")).items():
            if name not in BLOCK_GROUPS:
                sys.exit(f"template block {name!r} has no entitlement group (core/entitlements.BLOCK_GROUPS)")
            unit = BLOCK_GROUPS[name] + (f"/{BLOCK_COMPONENTS[name]}" if name in BLOCK_COMPONENTS else "")
            dst = pkg / "assets" / unit / "templates" / "enterprise" / f"{name}.html"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(html, encoding="utf-8")
    for f in parts["tests"]:
        dst = pkg / "tests" / f[len("test/"):]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    v = version()
    (pkg / "__init__.py").write_text(INIT.format(version=v), encoding="utf-8")
    deps = [f"uderia>={v}"] + core.enterprise_requirements((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    (out / "pyproject.toml").write_text(PYPROJECT.format(version=v, dependencies=", ".join(f'"{d}"' for d in deps)), encoding="utf-8")
    (out / "README.md").write_text(README.format(version=v, table=distributions_table()), encoding="utf-8")
    lic = ROOT / "legal/ENTERPRISE-LICENSE.md"
    if lic.exists():
        shutil.copy2(lic, out / "LICENSE")
    # the package's manifest of what it carries, for the record and for the verifier
    (pkg / "MANIFEST.json").write_text(json.dumps({"version": v, **{k: parts[k] for k in ("overlay", "assets", "tests")},
                                                   "groups": list(ENTITLEMENT_GROUPS), "group": parts["group"], "unit": parts["unit"]},
                                                  indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if not (args.dry_run or args.out):
        ap.error("one of --dry-run, --out")
    manifest = json.loads(core.MANIFEST.read_text(encoding="utf-8"))
    files = core.tracked_files()
    decided, undecided = core.classify(files, manifest["rules"])
    if undecided:
        sys.exit(f"undecided files: {undecided[:5]}")
    public = sorted(f for f, r in decided.items() if r["action"] == "include")
    excluded = sorted(f for f, r in decided.items() if r["action"] == "exclude")
    core.tests_follow_their_modules(public, excluded, decided)
    parts = classify_for_package(decided)
    groups = Counter(decided[f]["group"] for part in ("overlay", "assets", "tests") for f in parts[part])
    print(f"enterprise package {version()}: overlay {len(parts['overlay'])} · assets {len(parts['assets'])} · tests {len(parts['tests'])} · not shipped {len(parts['dropped'])}")
    for g, n in groups.most_common():
        print(f"  ships · {g:22s} {n:5d}")
    if args.out:
        write(args.out, parts)
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
