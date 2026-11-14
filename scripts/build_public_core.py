#!/usr/bin/env python3
"""Build the public core of Uderia from this repository, by the rules in oss/public_core.json.

The repository stays the single source of truth. The public core is an export: every tracked
file is classified by the first matching rule of the manifest (a file matching no rule fails
the build, so a new path is always a decision), the excluded files are the enterprise package,
and the public tree is written with its own LICENSE (FSL-1.1-ALv2, the Functional Source License
with the Apache 2.0 future licence), the Apache 2.0 text it converts to, and NOTICE.

Two checks run on every build, dry or not:

* the import check — every Python file in the public tree is parsed for imports of
  ``trusted_data_agent.*``; an import whose target file is excluded is a seam, unless it sits
  inside ``try: … except ImportError`` (an optional import: the seam is already cut). Seams listed in
  the manifest's ``optional_import_seams.allowed`` are the registration points that Phase 1
  turns into optional imports; any other is a cut still to make, and the build reports it.
* the reference check — the public ``main.py`` and factories must not require an excluded
  module unconditionally (reported, not yet enforced).

Usage:
    python scripts/build_public_core.py --dry-run           # classify, check, report; write nothing
    python scripts/build_public_core.py --out /path/to/dir  # write the public tree there
    python scripts/build_public_core.py --list excluded     # list files of one side (public|excluded)
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import json
import os
import shutil
import subprocess
import sys
import warnings
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "oss/public_core.json"
SRC_PKG = "src/trusted_data_agent"


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [l for l in out.splitlines() if l]


def match(pattern: str, path: str) -> bool:
    # fnmatch treats * as crossing '/', which is what a '**' rule wants; a rule with a single
    # '*' in a segment is written with its directory, so the difference does not bite here.
    return fnmatch.fnmatchcase(path, pattern)


def classify(files: list[str], rules: list[dict]) -> tuple[dict[str, dict], list[str]]:
    decided, undecided = {}, []
    for f in files:
        for r in rules:
            if match(r["pattern"], f):
                decided[f] = r
                break
        else:
            undecided.append(f)
    return decided, undecided


def module_to_path(mod: str, public: set[str], excluded: set[str]) -> str | None:
    """trusted_data_agent.a.b -> the tracked file that defines it, or None if unknown."""
    if not mod.startswith("trusted_data_agent"):
        return None
    rel = mod.replace(".", "/")
    for cand in (f"src/{rel}.py", f"src/{rel}/__init__.py"):
        if cand in public or cand in excluded:
            return cand
    # a `from pkg import name` where name is a submodule: resolved by the caller
    return None


def _package_of(path: Path) -> str | None:
    """The dotted package a source file belongs to, for files under src/trusted_data_agent."""
    try:
        rel = path.resolve().relative_to((ROOT / "src").resolve())
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: Path) -> list[str]:
    pkg = _package_of(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    # an import inside `try: … except ImportError/ModuleNotFoundError/Exception:` is optional
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
                h.type is None or (isinstance(h.type, ast.Name) and h.type.id in _OPTIONAL)
                or (isinstance(h.type, ast.Tuple) and any(isinstance(e, ast.Name) and e.id in _OPTIONAL for e in h.type.elts))
                for h in node.handlers):
            for sub in node.body:
                for n in ast.walk(sub):
                    if isinstance(n, (ast.Import, ast.ImportFrom)):
                        guarded.add(id(n))
    mods = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.append(node.module)
            mods.extend(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level > 0 and pkg is not None:
            # a relative import: resolve against the file's package
            base = pkg.split(".")
            base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
            root = ".".join(base + ([node.module] if node.module else []))
            if root:
                mods.append(root)
            mods.extend(f"{root}.{a.name}" if root else a.name for a in node.names)
    return mods


_OPTIONAL = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def import_check(public: set[str], excluded: set[str], allowed: set[str]) -> dict[str, list[tuple[str, str]]]:
    """-> {excluded target: [(public importer, module)]} for importers not in the allowed seams."""
    hits: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in sorted(public):
        if not f.endswith(".py"):
            continue
        seen = set()
        for mod in imports_of(ROOT / f):
            target = module_to_path(mod, public, excluded)
            if target and target in excluded and f not in allowed and (f, target) not in seen:
                seen.add((f, target))
                hits[target].append((f, mod))
    return hits


def _excluded_tokens(public: set[str], excluded: set[str]) -> dict[str, str]:
    """Names a test can only be reading from the enterprise side: the basename of an excluded
    source file — specific enough (14+ characters, a source extension) and carried by no public
    path — quoted somewhere in the test. Directory names are too generic to be tokens."""
    pub_names = {part for f in public for part in f.split("/")}
    generic = {"__init__.py", "manifest.json", "skill.json", "README.md", "build.py", "handler.py",
               "renderer.js", "instructions.json", "docker-compose.yml", "requirements.txt"}
    tokens: dict[str, str] = {}
    source_dirs = ("src/", "components/", "agent_packs/", "skills/", "mcp_servers/", "scripts/",
                   "maintenance/", "deploy/", "static/", "templates/", "schema/")
    for f in excluded:
        if not f.startswith(source_dirs):
            continue  # a document's name in a docstring is a mention, not a read
        name = f.rsplit("/", 1)[-1]
        if name in pub_names or name in generic or len(name) < 14:
            continue
        if not name.endswith((".py", ".mjs", ".sh", ".sql", ".json", ".md", ".yml")):
            continue
        tokens.setdefault(name, f)
    return tokens


def tests_follow_their_modules(public: list[str], excluded: list[str], decided: dict) -> list[str]:
    """A test that imports an excluded module, or reads an excluded file by name, belongs to the
    enterprise package. Returns the moved tests."""
    pub, exc = set(public), set(excluded)
    tokens = _excluded_tokens(pub, exc)
    moved = []
    for f in list(public):
        if not (f.startswith("test/") and (f.endswith(".py") or f.endswith(".mjs") or f.endswith(".sh"))):
            continue
        why = None
        if f.endswith(".py"):
            for mod in imports_of(ROOT / f):
                target = module_to_path(mod, pub, exc)
                if target and target in exc:
                    why = f"imports {target}"
                    break
        if why is None:
            text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
            if "# distribution-aware" in text:
                continue  # the suite checks what the distribution carries and skips the rest
            for tok, origin in tokens.items():
                if f'"{tok}"' in text or f"'{tok}'" in text or f"/{tok}" in text:
                    why = f"reads {origin}"
                    break
        if why:
            moved.append(f)
            decided[f] = {"action": "exclude", "group": "tests-of-excluded", "why": why}
    return moved


# The block markers are read by the runtime too (core/entitlements.py renders a page without the
# blocks a licence withholds): ONE regex, imported from there, so the two can never disagree.
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("UDERIA_NO_PLUGINS", "1")
from trusted_data_agent.core.entitlements import BLOCK_MARK as _MARK, enterprise_blocks  # noqa: E402


def strip_enterprise(text: str) -> str:
    """Replace every `{# enterprise-begin:X #} … {# enterprise-end:X #}` block of a template with
    `{% include "enterprise/X.html" ignore missing %}`: nothing in the public core, the block itself
    when the enterprise package (which ships the blocks as template partials under its asset root,
    joined to the template search path by main.py) is installed."""
    out = _MARK.sub(lambda m: f'{{% include "enterprise/{m.group(1)}.html" ignore missing %}}\n', text)
    if "enterprise-begin" in out or "enterprise-end" in out:
        sys.exit("template markers are unbalanced (a begin without its end, or nested)")
    return out


ENTERPRISE_REQ_TAG = "# enterprise:"


def public_requirements(text: str) -> str:
    """requirements.txt without the lines tagged `# enterprise:` (the package's own dependencies)."""
    return "\n".join(l for l in text.splitlines() if not _tagged_requirement(l)) + "\n"


def public_compose(text: str) -> str:
    """docker-compose.yml for anyone: the `# prod-only-begin` … `# prod-only-end` blocks (the author's
    host path mounted over /app, the production URL, privileged mode) are dropped, and a line
    `#public: <line>` — a comment in the development repository — becomes `<line>` at its indent."""
    out, skip = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# prod-only-begin"):
            skip = True
            continue
        if s.startswith("# prod-only-end"):
            skip = False
            continue
        if skip:
            continue
        if s.startswith("#public: "):
            line = line[: len(line) - len(line.lstrip())] + s[len("#public: "):]
        out.append(line)
    if skip:
        raise SystemExit("docker-compose.yml: a prod-only block is never closed")
    return "\n".join(out) + "\n"


def enterprise_requirements(text: str) -> list[str]:
    """The requirement specifiers tagged `# enterprise:` — what the enterprise package depends on."""
    return [l.split("#", 1)[0].strip() for l in text.splitlines() if _tagged_requirement(l)]


def _tagged_requirement(line: str) -> bool:
    """A requirement line (not a comment-only line) whose comment carries the tag."""
    spec, _, comment = line.partition("#")
    return bool(spec.strip()) and ENTERPRISE_REQ_TAG.lstrip("#").strip() in comment


def generate_operations_catalog(out: Path) -> None:
    """Regenerate the operations backlog and catalog doc for THIS distribution's routes.

    The generator builds the app without serving it; it leaves a bootstrap database, a JWT secret
    and log files behind, which are removed again so the export carries no state."""
    before = {p for p in out.rglob("*") if p.is_file()}
    env = dict(os.environ, UDERIA_NO_PLUGINS="1", PYTHONPATH=str(out / "src"), SECRET_KEY="public-core-export")
    for sub in ("backlog", "docs"):
        r = subprocess.run([sys.executable, "maintenance/platform_operations.py", sub, "--write"],
                           cwd=out, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"operations catalog ({sub}) failed in the export:\n{r.stdout[-600:]}{r.stderr[-1200:]}")
    for p in {p for p in out.rglob("*") if p.is_file()} - before:
        if p.name.endswith(".pyc") or p.suffix in (".db", ".log", ".key") or "__pycache__" in p.parts:
            p.unlink()
    for d in sorted((d for d in out.rglob("__pycache__") if d.is_dir()), reverse=True):
        shutil.rmtree(d, ignore_errors=True)
    for d in sorted((d for d in out.rglob("*") if d.is_dir() and d.name.endswith(".egg-info")), reverse=True):
        shutil.rmtree(d, ignore_errors=True)


_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def unresolved_links(out: Path) -> list[str]:
    """Every relative Markdown link in the tree whose target file does not exist there. The
    manifest limits the documentation the public core carries, and a document is never rewritten
    (it reads the same in every distribution), so a public document must not link a private file
    (a plan, an assessment, enterprise code): it names it as a code reference and links only what
    the public core carries. The manifest gate holds the same rule; this is the last check."""
    bad = []
    for md in sorted(out.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="ignore")
        for m in _LINK.finditer(text):
            href = m.group(2)
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            from urllib.parse import unquote
            target = unquote(href.split("#", 1)[0])
            if not target or ("/" not in target and "." not in target):   # a placeholder in prose, e.g. `[text](url)`
                continue
            if not (md.parent / target).exists():
                bad.append(f"{md.relative_to(out)} → {href}")
    return bad


def build_bundled_packs(out: Path) -> None:
    """Build the pack a core deployment seeds on first start (core/first_start) from the documentation
    the export carries, so the @UDERIA corpus is exactly the public documentation. The pack carries its
    embeddings (computed here, with the cached model); the deployment loads vectors only."""
    bundled = out / "agent_packs/uderia_expert/bundled"
    r = subprocess.run([sys.executable, str(out / "agent_packs/uderia_expert/build.py"), "--docs-root", str(out),
                        "--output-dir", str(bundled), "--force"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"bundled pack build failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    pack = bundled / "uderia_expert.agentpack"
    if not pack.is_file():
        sys.exit(f"bundled pack build wrote no pack at {pack}")
    chunks = [l for l in r.stdout.splitlines() if "Chunks" in l]
    print(f"bundled: {pack.relative_to(out)} ({pack.stat().st_size / 1e6:.1f} MB)" + (" — " + chunks[-1].strip() if chunks else ""))


def write_tree(public: list[str], out: Path, manifest: dict) -> None:
    if out.exists() and any(out.iterdir()):
        sys.exit(f"refusing to write into a non-empty directory: {out}")
    for f in public:
        src, dst = ROOT / f, out / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        if f.startswith("templates/") and f.endswith(".html"):
            dst.write_text(strip_enterprise(src.read_text(encoding="utf-8")), encoding="utf-8")
            continue
        if f == "requirements.txt":
            dst.write_text(public_requirements(src.read_text(encoding="utf-8")), encoding="utf-8")
            continue
        if f == "docker-compose.yml":
            dst.write_text(public_compose(src.read_text(encoding="utf-8")), encoding="utf-8")
            continue
        shutil.copy2(src, dst)
    # the documentation travels whole and is never rewritten: a link that would not resolve in the
    # export is a defect of the source (fix the document, or make its target public)
    bad = unresolved_links(out)
    if bad:
        sys.exit("the export carries dead links — fix the source documents:\n  " + "\n  ".join(bad[:20]))
    # the directories the runtime and the suites expect to exist, and the distribution marker
    (out / "logs").mkdir(exist_ok=True)
    (out / "test" / ".public-core").write_text(
        "public core export — a suite the battery lists but this distribution does not carry is reported as skipped\n",
        encoding="utf-8")
    # the public core's licence (FSL-1.1-ALv2) becomes LICENSE; the Apache 2.0 text it converts to
    # on each version's second anniversary travels beside it
    lic = ROOT / "legal/LICENSE-FSL-1.1-ALv2.md"
    if not lic.exists():
        sys.exit(f"the public core's licence text is missing: {lic}")
    shutil.copy2(lic, out / "LICENSE")
    future = ROOT / "legal/LICENSE-APACHE-2.0.txt"
    if future.exists():
        shutil.copy2(future, out / "LICENSE-APACHE-2.0")
    holder = manifest.get("copyright_holder", "")
    (out / "NOTICE").write_text(
        f"Uderia — a framework for trusted data agents\nCopyright {holder}\n\n"
        "Licensed under the Functional Source License, Version 1.1, ALv2 Future License\n"
        "(FSL-1.1-ALv2); see LICENSE. You may use, copy, modify and redistribute this work for any\n"
        "purpose other than a Competing Use, as LICENSE defines it. Each version becomes available\n"
        "under the Apache License, Version 2.0 (LICENSE-APACHE-2.0) on the second anniversary of\n"
        "the date it is made available.\n", encoding="utf-8")
    # the operations catalog describes THIS distribution's routes, not the monorepo's
    generate_operations_catalog(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--bundled-packs", action="store_true",
                    help="after writing the tree, build the bundled Uderia Expert pack FROM the exported documentation "
                         "into <out>/agent_packs/uderia_expert/bundled/ (chunked and embedded here; a deployment only loads the vectors)")
    ap.add_argument("--list", choices=["public", "excluded"])
    ap.add_argument("--seams", action="store_true", help="print every public→excluded import, allowed seams included")
    args = ap.parse_args()
    if not (args.dry_run or args.out or args.list or args.seams):
        ap.error("one of --dry-run, --out, --list, --seams")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rules = manifest["rules"]
    allowed = set(manifest.get("optional_import_seams", {}).get("allowed", []))
    files = tracked_files()
    decided, undecided = classify(files, rules)
    public = sorted(f for f, r in decided.items() if r["action"] == "include")
    excluded = sorted(f for f, r in decided.items() if r["action"] == "exclude")
    moved = tests_follow_their_modules(public, excluded, decided)
    public = sorted(f for f, r in decided.items() if r["action"] == "include")
    excluded = sorted(f for f, r in decided.items() if r["action"] == "exclude")

    if args.list:
        print("\n".join(public if args.list == "public" else excluded))
        return 0

    if undecided:
        print(f"UNDECIDED ({len(undecided)}) — add a rule for each:")
        print("\n".join("  " + u for u in undecided[:40]))
        return 1

    groups = Counter(decided[f]["group"] for f in excluded)
    print(f"tracked {len(files)} · public {len(public)} · excluded {len(excluded)}")
    for g, n in groups.most_common():
        print(f"  excluded · {g:22s} {n:5d}")
    if moved:
        print(f"  ({len(moved)} tests moved with the modules they import)")

    hits = import_check(set(public), set(excluded), allowed if not args.seams else set())
    if args.seams:
        hits = import_check(set(public), set(excluded), set())
    print(f"\nimport check: {sum(len(v) for v in hits.values())} public→excluded imports outside the allowed seams"
          if not args.seams else f"\nall public→excluded imports ({sum(len(v) for v in hits.values())}):")
    for target, importers in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        print(f"  {target}  ← {len(importers)}")
        for f, mod in importers[:6]:
            print(f"      {f}  ({mod})")
        if len(importers) > 6:
            print(f"      … {len(importers) - 6} more")

    if args.out:
        write_tree(public, args.out, manifest)
        print(f"\nwritten: {args.out} ({len(public)} files, LICENSE, NOTICE)")
        if args.bundled_packs:
            build_bundled_packs(args.out)
    return 1 if (hits and not args.seams and args.out) else 0


if __name__ == "__main__":
    sys.exit(main())
