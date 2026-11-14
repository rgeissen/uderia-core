"""
Skill package serializer — THE one implementation of the `.skill` zip format.

PROP-005 §3: nine sites read/write skill packages today, each hand-rolling the
format; a bundled `library/` subtree (progressive skills) would be silently
dropped — or worse, a library markdown could be picked as the skill BODY by the
import endpoint's first-candidate rule. Every writer/reader now routes through
this module. The module owns MECHANISM only (entries, selection order, subtree,
zip-slip sanitization, integrity); each call site keeps its own POLICY (manifest
flavor, SKILL.md frontmatter dialect, error codes) so existing behavior is
byte-preserved for legacy skills (no-regression contract N2/N3/N9,
test/test_progressive_skills.py).

Package layout (legacy entries unchanged; `library/` is additive):

    skill.json            manifest (caller-prepared)
    <name>.md             the root content (main_file)
    SKILL.md              optional Claude-Code-compatible copy (caller-prepared)
    library/<relpath>     optional progressive payload, declared by
                          manifest["uderia"]["progressive"]["index"]

The progressive index doubles as the integrity manifest: each entry carries the
payload file's sha256, verified at install time (a tampered or incomplete
payload is refused loudly, never materialised silently).
"""

import hashlib
import io
import json
import logging
import posixpath
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

LIBRARY_PREFIX = "library/"


# ── Progressive-manifest helpers ───────────────────────────────────────────────

def progressive_block(manifest: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The manifest's `uderia.progressive` block, or None. Malformed shapes → None."""
    if not isinstance(manifest, dict):
        return None
    uderia = manifest.get("uderia")
    if not isinstance(uderia, dict):
        return None
    prog = uderia.get("progressive")
    if not isinstance(prog, dict) or not prog.get("enabled"):
        return None
    return prog


def progressive_index(manifest: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The progressive index entries ([] when absent/malformed)."""
    prog = progressive_block(manifest)
    entries = prog.get("index") if prog else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def is_progressive(manifest: Optional[Dict[str, Any]]) -> bool:
    return progressive_block(manifest) is not None


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Zip-slip sanitization (security boundary — mutation-tested) ────────────────

def safe_library_relpath(arcname: str) -> Optional[str]:
    """Normalize a `library/…` arcname to a safe relative path INSIDE library/.

    Returns the path relative to library/ (forward slashes), or None when the
    entry must be rejected: absolute paths, drive letters, backslash separators,
    `..` traversal, empty segments, or anything that normalizes outside the
    library root. A rejected entry is never written anywhere.
    """
    if not arcname.startswith(LIBRARY_PREFIX):
        return None
    rel = arcname[len(LIBRARY_PREFIX):]
    if not rel or rel.endswith("/"):
        return None  # directory entry or empty
    if "\\" in rel or rel.startswith("/") or ":" in rel.split("/", 1)[0]:
        return None
    norm = posixpath.normpath(rel)
    if norm.startswith("../") or norm == ".." or norm.startswith("/") or norm == ".":
        return None
    # normpath collapses interior '..'; any survivor above is rejected. Also
    # refuse entries whose normalization changed the path (masked traversal).
    if norm != rel.rstrip("/"):
        return None
    return norm


# ── Build ──────────────────────────────────────────────────────────────────────

def build_skill_zip(
    manifest: Dict[str, Any],
    md_name: str,
    content: str,
    skill_md: Optional[str] = None,
    library_files: Optional[Dict[str, bytes]] = None,
) -> bytes:
    """Serialize a skill package. Legacy entries byte-compatible with the three
    historical writers; `library/` entries appended only when provided.

    The caller prepares `manifest` (its flavor: export stamps, `_`-key strips)
    and `skill_md` (its frontmatter dialect) — policy stays at the call site.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("skill.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr(md_name, content)
        if skill_md is not None:
            zf.writestr("SKILL.md", skill_md)
        for rel in sorted(library_files or {}):
            safe = safe_library_relpath(LIBRARY_PREFIX + rel)
            if safe is None:
                raise ValueError(f"Unsafe library path refused at build time: {rel!r}")
            zf.writestr(LIBRARY_PREFIX + safe, library_files[rel])
    return buf.getvalue()


# ── Parse ──────────────────────────────────────────────────────────────────────

@dataclass
class ParsedSkill:
    """Mechanical facts extracted from a `.skill`/`.zip` — no policy applied."""
    manifest: Optional[Dict[str, Any]] = None      # from skill.json (verbatim)
    frontmatter: Optional[Dict[str, Any]] = None   # from SKILL.md, when present
    content: Optional[str] = None                  # selected root content
    md_name: Optional[str] = None                  # arcname the content came from
    content_from_skill_md: bool = False            # True → frontmatter was stripped
    library_files: Dict[str, bytes] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _parse_frontmatter(raw: str) -> Tuple[Dict[str, Any], str]:
    """Minimal `--- key: value ---` frontmatter parser (SKILL.md dialect)."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    fm: Dict[str, Any] = {}
    for line in raw[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            if k.strip():
                fm[k.strip()] = v.strip()
    body = raw[end + 4:]
    return fm, body.lstrip("\n")


def _strip_common_root(names: List[str]) -> Dict[str, str]:
    """Map arcname → root-stripped name when ALL file entries share one leading
    directory (the GitHub-download zip shape); identity mapping otherwise."""
    files = [n for n in names if not n.endswith("/")]
    if not files:
        return {}
    heads = {n.split("/", 1)[0] for n in files}
    if len(heads) == 1 and all("/" in n for n in files):
        return {n: n.split("/", 1)[1] for n in files}
    return {n: n for n in files}


def parse_skill_zip(zip_bytes: bytes, skill_id_hint: Optional[str] = None) -> ParsedSkill:
    """Extract a skill package's facts. Root-content selection order (superset of
    the two historical readers, and library-aware so a payload file can never be
    promoted to the skill body — the live corruption path this module closes):

        1. manifest main_file, when present in the archive
        2. `{skill_id_hint}.md`
        3. first non-SKILL.md `*.md` OUTSIDE library/
        4. SKILL.md (frontmatter stripped, `content_from_skill_md=True`)
    """
    out = ParsedSkill()
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        mapping = _strip_common_root(zf.namelist())
        by_logical = {logical: arc for arc, logical in mapping.items()}

        # skill.json (root-stripped exact name first, any basename match second)
        mf_arc = by_logical.get("skill.json") or next(
            (arc for arc, logical in mapping.items() if logical.endswith("skill.json")), None)
        if mf_arc:
            try:
                out.manifest = json.loads(zf.read(mf_arc).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                out.warnings.append(f"unreadable skill.json: {e}")

        # SKILL.md frontmatter (facts only; callers decide whether it matters)
        skill_md_arc = by_logical.get("SKILL.md") or next(
            (arc for arc, logical in mapping.items() if logical.endswith("/SKILL.md")), None)
        if skill_md_arc:
            raw = zf.read(skill_md_arc).decode("utf-8")
            out.frontmatter, _ = _parse_frontmatter(raw)

        # library/ payload — sanitized; NEVER content candidates
        for arc, logical in mapping.items():
            if logical.startswith(LIBRARY_PREFIX):
                safe = safe_library_relpath(logical)
                if safe is None:
                    out.warnings.append(f"unsafe library entry ignored: {logical}")
                    continue
                out.library_files[safe] = zf.read(arc)

        # Root-content selection
        md_logical = [logical for logical in mapping.values()
                      if logical.endswith(".md")
                      and not logical.endswith("SKILL.md")
                      and not logical.startswith(LIBRARY_PREFIX)]
        pick: Optional[str] = None
        main_file = (out.manifest or {}).get("main_file")
        if main_file and main_file in by_logical:
            pick = main_file
        elif skill_id_hint and f"{skill_id_hint}.md" in by_logical:
            pick = f"{skill_id_hint}.md"
        elif md_logical:
            pick = md_logical[0]
        if pick:
            out.md_name = pick
            out.content = zf.read(by_logical[pick]).decode("utf-8")
        elif skill_md_arc:
            raw = zf.read(skill_md_arc).decode("utf-8")
            _, body = _parse_frontmatter(raw)
            out.content = body
            out.content_from_skill_md = True
    return out


# ── Integrity (the index is the manifest of record for the payload) ───────────

def validate_progressive_payload(
    manifest: Optional[Dict[str, Any]],
    library_files: Dict[str, bytes],
) -> List[str]:
    """Errors that make a progressive payload UNINSTALLABLE (fail loud, never a
    silent partial materialisation): index entries whose file is missing or whose
    sha256 mismatches, duplicate anchors, index entries outside library/.
    Non-indexed extra payload files are tolerated (forward compatibility) —
    they are simply not addressable.
    """
    errors: List[str] = []
    entries = progressive_index(manifest)
    if not entries:
        if is_progressive(manifest):
            errors.append("progressive skill declares no index entries")
        return errors
    seen_anchors = set()
    for e in entries:
        anchor, fpath, sha = e.get("anchor"), e.get("file", ""), e.get("sha256")
        if not anchor or anchor in seen_anchors:
            errors.append(f"missing or duplicate anchor: {anchor!r}")
            continue
        seen_anchors.add(anchor)
        if not fpath.startswith(LIBRARY_PREFIX):
            errors.append(f"index entry '{anchor}' points outside library/: {fpath!r}")
            continue
        rel = safe_library_relpath(fpath)
        if rel is None:
            errors.append(f"index entry '{anchor}' has unsafe path: {fpath!r}")
            continue
        data = library_files.get(rel)
        if data is None:
            errors.append(f"index entry '{anchor}' file missing from payload: {rel}")
        elif sha and file_sha256(data) != sha:
            errors.append(f"index entry '{anchor}' sha256 mismatch: {rel}")
    return errors
