#!/usr/bin/env python3
"""Publish the two distributions: build both exports from this monorepo and push each to its
own repository as one fresh commit that names the monorepo commit it came from.

    uderia-core        ← scripts/build_public_core.py        (the public core, FSL-1.1-ALv2)
    uderia-enterprise  ← scripts/build_enterprise_package.py  (the enterprise package)

The monorepo stays the single source of truth; nobody develops in the exports. Each target
repository's history is the sequence of exports, one commit per publish, so a licensee can
pin a commit and a reviewer can diff two exports. The working tree must be clean, so what is
published is a commit, never a half-edited tree.

Usage:
    python scripts/publish_distributions.py --work /tmp/publish              # both
    python scripts/publish_distributions.py --work /tmp/publish --only core  # or enterprise
    python scripts/publish_distributions.py --work /tmp/publish --dry-run    # build + diff, no push

Remotes default to https://github.com/rgeissen/uderia-core.git and …/uderia-enterprise.git
(override with --core-remote / --enterprise-remote).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
EXPORT_IGNORE = """__pycache__/
*.pyc
*.egg-info/
.env
*.db
*.db-*
logs/
tda_sessions/
tda_eval/
tda_files/
"""


def sh(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"{' '.join(cmd)} failed in {cwd}:\n{r.stdout[-800:]}{r.stderr[-800:]}")
    return r


def sync_tree(src: Path, repo: Path) -> None:
    """Replace the repository's content with the export, keeping .git."""
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for child in src.iterdir():
        dst = repo / child.name
        shutil.copytree(child, dst, symlinks=True) if child.is_dir() else shutil.copy2(child, dst)
    gi = repo / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    for line in EXPORT_IGNORE.splitlines():
        if line and line not in existing.splitlines():
            existing += ("" if existing.endswith("\n") or not existing else "\n") + line + "\n"
    gi.write_text(existing, encoding="utf-8")


def publish(name: str, build_cmd: list[str], remote: str, work: Path, dry_run: bool, source_sha: str, source_desc: str) -> None:
    export = work / f"{name}-export"
    repo = work / f"{name}-repo"
    for d in (export, repo):
        if d.exists():
            shutil.rmtree(d)
    print(f"== {name}: build")
    sh(build_cmd + ["--out", str(export)], cwd=ROOT)
    print(f"== {name}: clone {remote}")
    r = subprocess.run(["git", "clone", "-q", remote, str(repo)], capture_output=True, text=True)
    if r.returncode != 0:  # an empty repository clones with a warning or not at all: start one
        repo.mkdir(parents=True)
        sh(["git", "init", "-q", "-b", "main"], cwd=repo)
        sh(["git", "remote", "add", "origin", remote], cwd=repo)
    sync_tree(export, repo)
    sh(["git", "add", "-A"], cwd=repo)
    status = sh(["git", "status", "--porcelain"], cwd=repo).stdout
    changed = len([l for l in status.splitlines() if l.strip()])
    print(f"== {name}: {changed} files change in the export")
    if changed == 0:
        print(f"== {name}: nothing to publish")
        return
    if dry_run:
        print(sh(["git", "diff", "--cached", "--stat"], cwd=repo).stdout[-1500:])
        return
    msg = f"Export of uderia {source_desc}\n\nBuilt from monorepo commit {source_sha} by scripts/publish_distributions.py.\n"
    sh(["git", "-c", "user.name=Uderia export", "-c", "user.email=admin@uderia.com", "commit", "-q", "-m", msg], cwd=repo)
    sh(["git", "push", "-q", "-u", "origin", "main"], cwd=repo)
    print(f"== {name}: pushed {sh(['git', 'rev-parse', '--short', 'HEAD'], cwd=repo).stdout.strip()} to {remote}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--only", choices=["core", "enterprise"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--core-remote", default="https://github.com/rgeissen/uderia-core.git")
    ap.add_argument("--enterprise-remote", default="https://github.com/rgeissen/uderia-enterprise.git")
    ap.add_argument("--allow-dirty", action="store_true", help="publish from a tree with uncommitted changes (not for releases)")
    args = ap.parse_args()
    dirty = sh(["git", "status", "--porcelain"], cwd=ROOT).stdout.strip()
    if dirty and not args.allow_dirty:
        sys.exit("the monorepo has uncommitted changes — commit first, so the export names a commit")
    sha = sh(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).stdout.strip()
    desc = sh(["git", "describe", "--tags", "--always"], cwd=ROOT).stdout.strip()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.only in (None, "core"):
        publish("uderia-core", [PY, "scripts/build_public_core.py", "--bundled-packs"], args.core_remote, args.work, args.dry_run, sha, desc)
    if args.only in (None, "enterprise"):
        publish("uderia-enterprise", [PY, "scripts/build_enterprise_package.py"], args.enterprise_remote, args.work, args.dry_run, sha, desc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
