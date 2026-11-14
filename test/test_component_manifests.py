#!/usr/bin/env python3
"""
Consistency guard for component manifests.

Roots out the class of drift where a manifest, the registry, the schema enum,
and the loader's fallback default diverge silently (e.g. category 'Automation'
used by components but absent from the schema enum). Run in CI / before deploy.

Asserts:
  1. Every builtin manifest validates against component_manifest_v1.schema.json.
  2. Every manifest 'category' is in the schema's category enum.
  3. Every component_registry.json 'category' is in the enum.
  4. Manifest category == registry category for components listed in both.
  5. The loader's fallback default ('General') is itself a valid enum value.

Usage:
    python test/test_component_manifests.py
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components"
SCHEMA_FILE = COMPONENTS / "schemas" / "component_manifest_v1.schema.json"
REGISTRY_FILE = COMPONENTS / "component_registry.json"

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


def _loader_default_category():
    """Read the loader's fallback default straight from manager.py source so the
    test tracks the real code, not a hardcoded copy."""
    src = (ROOT / "src" / "trusted_data_agent" / "components" / "manager.py").read_text()
    # category=manifest.get("category", "General")
    import re
    m = re.search(r'category=manifest\.get\(\s*"category"\s*,\s*"([^"]+)"\s*\)', src)
    return m.group(1) if m else None


def main():
    print("=" * 70)
    print("COMPONENT MANIFEST CONSISTENCY GUARD")
    print("=" * 70)

    schema = json.loads(SCHEMA_FILE.read_text())
    enum = set(schema["properties"]["category"]["enum"])
    print(f"\nSchema category enum: {sorted(enum)}")

    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(schema)
    except ImportError:
        print("  FAIL  jsonschema not installed (declared in requirements.txt)")
        return 1

    # 1 + 2: builtin manifests validate and use enum categories
    print("\n[builtin manifests]")
    manifest_cats = {}
    for mf in sorted((COMPONENTS / "builtin").glob("*/manifest.json")):
        name = mf.parent.name
        m = json.loads(mf.read_text())
        errs = list(validator.iter_errors(m))
        check(f"{name}: validates against schema", not errs,
              "; ".join(f"[{'/'.join(map(str, e.path)) or 'root'}] {e.message}" for e in errs))
        if "category" in m:
            manifest_cats[m["component_id"]] = m["category"]
            check(f"{name}: category '{m['category']}' in enum", m["category"] in enum)

    # 3: registry categories in enum
    print("\n[registry categories]")
    registry = json.loads(REGISTRY_FILE.read_text())
    registry_cats = {}
    for entry in registry.get("components", []):
        cid = entry.get("component_id")
        cat = entry.get("category")
        if cat is not None:
            registry_cats[cid] = cat
            check(f"{cid}: registry category '{cat}' in enum", cat in enum)

    # 4: manifest vs registry agreement
    print("\n[manifest ↔ registry agreement]")
    overlap = set(manifest_cats) & set(registry_cats)
    if not overlap:
        check("at least one component in both manifest and registry", False)
    for cid in sorted(overlap):
        check(f"{cid}: manifest '{manifest_cats[cid]}' == registry '{registry_cats[cid]}'",
              manifest_cats[cid] == registry_cats[cid])

    # 5: loader fallback default is a valid enum value
    print("\n[loader fallback default]")
    default_cat = _loader_default_category()
    check(f"loader default category resolved from source (got {default_cat!r})",
          default_cat is not None)
    check(f"loader default '{default_cat}' is a valid enum value", default_cat in enum)

    # 6: no internal reference leaks into a string a USER reads on the component card.
    #    `description` and `display_name` are rendered verbatim in Components → the card
    #    and its detail dialog. A proposal id ("PROP-005"), an issue key or a roadmap
    #    tier tells a user nothing and dates the product; the design record belongs in
    #    the architecture doc, which the card links to. Regression: the skill_library
    #    card shipped reading "Progressive disclosure for skills (PROP-005): …".
    print("\n[no internal refs in user-facing strings]")
    leak_re = re.compile(r"\b(PROP-\d+|TIER[_ ]\d+(\.\d+)?|WS\d+\b|#\d{2,})")
    for mf in sorted((COMPONENTS / "builtin").glob("*/manifest.json")):
        try:
            data = json.loads(mf.read_text())
        except Exception:
            continue                       # shape is asserted by the earlier sections
        cid = data.get("component_id", mf.parent.name)
        for field in ("description", "display_name"):
            hit = leak_re.search(str(data.get(field) or ""))
            check(f"{cid}.{field} carries no internal reference",
                  hit is None, f"leaked {hit.group(0)!r}" if hit else "")

    # 4. The Answer Surface contract (docs/roadmap/ANSWER_SURFACE_GRAMMAR.md): a component's
    #    inline output is drawn as ONE evidence row by the one seam, from the manifest — so
    #    a new component integrates by DECLARING its identity and disclosure, never by
    #    drawing a frame. Every builtin manifest must carry `icon_accent` (the row's accent,
    #    the same as its panel and rail item) and `answer_surface.open` (a visual or an act
    #    the user asked for opens; supporting evidence stays one gesture away).
    print("\n[answer surface: identity + disclosure declared]")
    for mf in sorted((COMPONENTS / "builtin").glob("*/manifest.json")):
        try:
            data = json.loads(mf.read_text())
        except Exception:
            continue
        cid = data.get("component_id", mf.parent.name)
        acc = str(data.get("icon_accent") or "")
        check(f"{cid}: icon_accent is a 6-digit hex (the row accent)", re.fullmatch(r"#[0-9a-fA-F]{6}", acc) is not None, acc)
        a = data.get("answer_surface")
        check(f"{cid}: answer_surface.open is declared as a boolean", isinstance(a, dict) and isinstance(a.get("open"), bool), a)
        if isinstance(a, dict) and a.get("kind"):
            check(f"{cid}: a declared row kind is one the grammar knows", a["kind"] in {"data", "chart", "compute", "synthesis", "sources", "component", "attachment", "step", "experts"}, a["kind"])

    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
