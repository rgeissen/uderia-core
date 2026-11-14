"""Skill content addressing + version archive (best-practice steps 3 & 4).

The root cause this module closes (found live, Aug 2026): skills are
instance-global mutable singletons keyed by id, while everything that installs
them (packs, profiles) is per-user — so one user's pack operation could silently
overwrite the skill content AND provenance every other user sees, with the
previous version simply gone.

Two primitives:

- ``skill_content_hash`` — ONE deterministic content address for a skill:
  sha256 over the main markdown plus the sorted ``(relpath, sha256)`` pairs of
  its library files. Deliberately self-contained (the Trust suite's
  ``core/trust/content.py`` is part of the REMOVABLE Ontology Suite, so the
  skills layer must not depend on it; this is a separate vocabulary, not a
  drifted copy of that one — the two serve different contracts).

- the **version archive** — ``<state_dir>/.skill_versions/<skill_id>/<hash12>/``.
  A dot-prefixed directory, which the SkillManager scanner skips by its
  existing ``startswith((".", "_"))`` rule, so archived versions can never be
  discovered as live skills. Archiving is append-only and idempotent by
  content address: archiving the same content twice is a no-op.

Fail direction: every function here degrades to "no archive" rather than
raising into a save/install path — losing a version snapshot must never lose
the save itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

ARCHIVE_DIRNAME = ".skill_versions"
SHORT_HASH_LEN = 12


# ---------------------------------------------------------------------------
# Content addressing
# ---------------------------------------------------------------------------

def skill_content_hash(md_content: str,
                       library_files: Optional[Dict[str, bytes]] = None) -> str:
    """Deterministic content address of a skill's substance.

    Covers the markdown body and the library payload — never skill.json, so a
    provenance re-stamp or metadata edit does not change the address (the same
    trust-neutrality rule the pack provenance stamp follows).
    """
    md_sha = hashlib.sha256((md_content or "").encode("utf-8")).hexdigest()
    lib_pairs = sorted(
        (rel, hashlib.sha256(data).hexdigest())
        for rel, data in (library_files or {}).items()
    )
    canonical = json.dumps([md_sha, lib_pairs], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_skill_dir(skill_dir: Path) -> Tuple[Optional[str], Dict[str, bytes]]:
    """Read a skill directory's (markdown, library_files) for hashing/archiving.

    The markdown is resolved the way the loader does: ``main_file`` from
    skill.json, else ``<dirname>.md``, else the first non-SKILL ``.md``.
    Returns ``(None, {})`` when nothing readable exists.
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        return None, {}

    main_file = None
    try:
        manifest = json.loads((skill_dir / "skill.json").read_text(encoding="utf-8"))
        main_file = manifest.get("main_file")
    except Exception:
        pass

    md_path = None
    if main_file and (skill_dir / main_file).is_file():
        md_path = skill_dir / main_file
    elif (skill_dir / f"{skill_dir.name}.md").is_file():
        md_path = skill_dir / f"{skill_dir.name}.md"
    else:
        candidates = [p for p in sorted(skill_dir.glob("*.md")) if p.name != "SKILL.md"]
        if candidates:
            md_path = candidates[0]
        elif (skill_dir / "SKILL.md").is_file():
            md_path = skill_dir / "SKILL.md"

    md_content: Optional[str] = None
    if md_path is not None:
        try:
            md_content = md_path.read_text(encoding="utf-8")
        except Exception:
            md_content = None

    library: Dict[str, bytes] = {}
    lib_dir = skill_dir / "library"
    if lib_dir.is_dir():
        for p in sorted(lib_dir.rglob("*")):
            if p.is_file():
                try:
                    library[str(p.relative_to(lib_dir))] = p.read_bytes()
                except Exception:
                    continue
    return md_content, library


def live_skill_hash(skill_dir: Path) -> Optional[str]:
    """Content address of the skill currently on disk, or None if unreadable."""
    md, lib = read_skill_dir(Path(skill_dir))
    if md is None:
        return None
    return skill_content_hash(md, lib)


# ---------------------------------------------------------------------------
# Version archive
# ---------------------------------------------------------------------------

def _skill_archive_dir(state_dir: Path, skill_id: str) -> Path:
    return Path(state_dir) / ARCHIVE_DIRNAME / skill_id


def archive_current(state_dir: Path, skill_id: str, skill_dir: Path) -> Optional[str]:
    """Snapshot the CURRENT on-disk skill into the version archive.

    Called before an overwrite replaces different content. Idempotent by
    content address — the same content archives once. Returns the archived
    version's short hash, or None (unreadable skill / archive failure — the
    caller proceeds either way; see the module's fail-direction note).
    """
    try:
        skill_dir = Path(skill_dir)
        full = live_skill_hash(skill_dir)
        if full is None:
            return None
        short = full[:SHORT_HASH_LEN]
        dest = _skill_archive_dir(state_dir, skill_id) / short
        if dest.exists():
            return short  # already archived — append-only, never rewritten
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / f".tmp-{short}"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(skill_dir, tmp)
        meta = {
            "content_hash": full,
            "archived_at": datetime.now(timezone.utc).isoformat(),
        }
        (tmp / ".version.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
        tmp.rename(dest)
        logger.info(f"Archived skill version: {skill_id}@{short}")
        return short
    except Exception as e:
        logger.warning(f"Could not archive skill version for '{skill_id}': {e}")
        return None


def count_versions(state_dir: Path, skill_id: str) -> int:
    """Number of archived versions — one readdir of a usually-absent directory.

    Cheap enough for the skills LIST endpoint (unlike ``list_versions``, which
    reads per-version metadata): the UI uses it to decide whether a version
    control is worth rendering on a skill row at all.
    """
    try:
        base = _skill_archive_dir(state_dir, skill_id)
        if not base.is_dir():
            return 0
        return sum(1 for d in base.iterdir()
                   if d.is_dir() and not d.name.startswith("."))
    except Exception:
        return 0


def list_versions(state_dir: Path, skill_id: str) -> List[Dict[str, Any]]:
    """Archived versions of a skill, newest first. [] when none / on error."""
    out: List[Dict[str, Any]] = []
    try:
        base = _skill_archive_dir(state_dir, skill_id)
        if not base.is_dir():
            return []
        for d in base.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            meta: Dict[str, Any] = {"short_hash": d.name}
            try:
                meta.update(json.loads((d / ".version.json").read_text(encoding="utf-8")))
            except Exception:
                pass
            out.append(meta)
        out.sort(key=lambda m: m.get("archived_at") or "", reverse=True)
    except Exception as e:
        logger.warning(f"Could not list skill versions for '{skill_id}': {e}")
    return out


def resolve_version_dir(state_dir: Path, skill_id: str,
                        content_hash: str) -> Optional[Path]:
    """The archive directory holding the given content address, or None.

    ``content_hash`` may be the full hash or a prefix of at least the short
    length. The live skill is NOT consulted here — callers check the live
    hash first and come here only on a mismatch.
    """
    if not content_hash or len(content_hash) < SHORT_HASH_LEN:
        return None
    try:
        base = _skill_archive_dir(state_dir, skill_id)
        cand = base / content_hash[:SHORT_HASH_LEN]
        if cand.is_dir():
            return cand
    except Exception:
        pass
    return None
